#!/usr/bin/env python3
"""
L2 Forwarding Controller (Baseline)
EC5209 Advanced Computer Networking, Spring 2026

MAC 주소를 학습해서 패킷을 올바른 포트로 전달하는 기본 L2 스위치.
슬라이싱 구현 전 베이스라인으로 사용.
"""

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ether_types


class L2Switch(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(L2Switch, self).__init__(*args, **kwargs)
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
        """스위치 연결 시 table-miss 플로우 설치 (모르는 패킷은 컨트롤러로)"""
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # table-miss: 매칭되는 룰 없으면 컨트롤러로 전송
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(
            ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self.add_flow(datapath, priority=0, match=match, actions=actions)

        self.logger.info("Switch %s connected", datapath.id)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """패킷이 컨트롤러로 올라왔을 때 처리"""
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        # 패킷 파싱
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]

        # LLDP 무시
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst_mac = eth.dst
        src_mac = eth.src
        dpid = datapath.id

        # MAC 테이블 초기화
        self.mac_to_port.setdefault(dpid, {})

        # src MAC 학습
        self.mac_to_port[dpid][src_mac] = in_port

        # dst MAC 알면 해당 포트로, 모르면 flood
        if dst_mac in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst_mac]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # dst MAC을 알면 플로우 룰 설치 (다음부터는 컨트롤러 안 거침)
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac)
            self.add_flow(datapath, priority=1, match=match, actions=actions,
                          idle_timeout=30)

        # 현재 패킷 전송
        out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None)

        datapath.send_msg(out)

        self.logger.info("dpid=%s src=%s dst=%s in_port=%s out_port=%s",
                         dpid, src_mac, dst_mac, in_port, out_port)