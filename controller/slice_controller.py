#!/usr/bin/env python3
"""
Slice Controller
EC5209 Advanced Computer Networking, Spring 2026

L2 포워딩 + 슬라이스별 큐 할당을 함께 처리하는 컨트롤러.

슬라이스 정의:
  Slice A (High Priority)  : H1(10.0.0.1) <-> H4(10.0.0.4) → 큐 0 (10Mbps 보장)
  Slice B (Medium Priority): H2(10.0.0.2) <-> H5(10.0.0.5) → 큐 1 (5Mbps 상한)
  Slice C (Best Effort)    : H3(10.0.0.3) <-> H6(10.0.0.6) → 큐 2 (베스트 에포트)

큐는 s1-eth4 (S1→S2 병목 링크)에 설정되어 있어야 함.
사전에 ovs_qos_setup.sh 실행 필요.
"""

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ipv4, ether_types


# 슬라이스 정의: (src_ip, dst_ip) → queue_id
# 양방향 모두 정의
SLICE_MAP = {
    ('10.0.0.1', '10.0.0.4'): 0,  # H1 → H4 (Slice A)
    ('10.0.0.4', '10.0.0.1'): 0,  # H4 → H1 (Slice A)
    ('10.0.0.2', '10.0.0.5'): 1,  # H2 → H5 (Slice B)
    ('10.0.0.5', '10.0.0.2'): 1,  # H5 → H2 (Slice B)
    ('10.0.0.3', '10.0.0.6'): 2,  # H3 → H6 (Slice C)
    ('10.0.0.6', '10.0.0.3'): 2,  # H6 → H3 (Slice C)
}

SLICE_NAMES = {0: 'Slice A (High)', 1: 'Slice B (Medium)', 2: 'Slice C (Best Effort)'}

# S1-S2 병목 링크 포트 번호 (topology.py addLink 순서 기준: h1,h2,h3,h4→s2,h5→s2,h6→s2,s1-s2)
BOTTLENECK_PORT = 4  # s1-eth4

# S1에서 병목 포트로 나가는 슬라이스별 규칙: (in_port, src_ip, dst_ip, queue_id)
# topology.py addLink 순서: h1→s1-eth1(1), h2→s1-eth2(2), h3→s1-eth3(3), s2→s1-eth4(4)
S1_SLICE_RULES = [
    (1, '10.0.0.1', '10.0.0.4', 0),  # H1(eth1) → H4: Slice A, queue 0
    (2, '10.0.0.2', '10.0.0.5', 1),  # H2(eth2) → H5: Slice B, queue 1
    (3, '10.0.0.3', '10.0.0.6', 2),  # H3(eth3) → H6: Slice C, queue 2
]


class SliceController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(SliceController, self).__init__(*args, **kwargs)
        # MAC 주소 테이블: {datapath_id: {mac: port}}
        self.mac_to_port = {}

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0):
        """플로우 룰을 스위치에 설치"""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        inst = [parser.OFPInstructionActions(
            ofproto.OFPIT_APPLY_ACTIONS, actions)]

        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout)

        datapath.send_msg(mod)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """스위치 연결 시 table-miss 플로우 + S1 슬라이스 규칙 선제 설치"""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # table-miss: 매칭 룰 없으면 컨트롤러로 (L2 학습용)
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(
            ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, priority=0, match=match, actions=actions)

        # S1에서만 슬라이스별 규칙을 스위치 연결 시점에 선제 설치.
        # priority-2 catch-all 방식은 packet_in을 차단해 slice 규칙이 영영
        # 설치되지 않는 버그를 일으킴 — 대신 priority-10으로 전체 쌍을 미리 설치.
        if datapath.id == 1:
            for in_port, src_ip, dst_ip, queue_id in S1_SLICE_RULES:
                match = parser.OFPMatch(
                    in_port=in_port,
                    eth_type=ether_types.ETH_TYPE_IP,
                    ipv4_src=src_ip,
                    ipv4_dst=dst_ip
                )
                actions = [
                    parser.OFPActionSetQueue(queue_id),
                    parser.OFPActionOutput(BOTTLENECK_PORT)
                ]
                self.add_flow(datapath, priority=10, match=match,
                              actions=actions, idle_timeout=0)
                self.logger.info(
                    "Pre-installed: port%d %s→%s queue=%d (%s)",
                    in_port, src_ip, dst_ip, queue_id, SLICE_NAMES[queue_id])

        self.logger.info("Switch %s connected", datapath.id)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """패킷이 컨트롤러로 올라왔을 때 처리"""
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']
        dpid = datapath.id

        # 패킷 파싱
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        # LLDP 무시
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst_mac = eth.dst
        src_mac = eth.src

        # MAC 테이블 초기화
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src_mac] = in_port

        # 목적지 포트 결정
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]
        else:
            out_port = ofproto.OFPP_FLOOD

        # IP 패킷인지 확인 → 슬라이스 분류
        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        if ip_pkt and out_port == BOTTLENECK_PORT:
            # S1→S2 병목 링크로 나가는 IP 패킷 → 슬라이스 큐 할당
            src_ip = ip_pkt.src
            dst_ip = ip_pkt.dst
            queue_id = SLICE_MAP.get((src_ip, dst_ip), 2)  # 기본값: Slice C
            slice_name = SLICE_NAMES.get(queue_id, 'Slice C (Best Effort)')

            # 큐 액션: set_queue → output
            actions = [
                parser.OFPActionSetQueue(queue_id),
                parser.OFPActionOutput(out_port)
            ]

            # 플로우 룰 설치 (IP 기반, 높은 우선순위)
            match = parser.OFPMatch(
                in_port=in_port,
                eth_type=ether_types.ETH_TYPE_IP,
                ipv4_src=src_ip,
                ipv4_dst=dst_ip
            )
            self.add_flow(datapath, priority=10, match=match,
                          actions=actions, idle_timeout=0)

            self.logger.info(
                "dpid=%s %s→%s → %s (queue=%d, port=%d)",
                dpid, src_ip, dst_ip, slice_name, queue_id, out_port)

        else:
            # 병목 링크가 아니거나 non-IP 패킷 → 일반 L2 포워딩
            actions = [parser.OFPActionOutput(out_port)]

            if out_port != ofproto.OFPP_FLOOD:
                match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac)
                self.add_flow(datapath, priority=1, match=match,
                              actions=actions, idle_timeout=30)

        # 현재 패킷 전송
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None)

        datapath.send_msg(out)