#!/usr/bin/env python3
"""
Slice Controller (SFC 버전)
EC5209 Advanced Computer Networking, Spring 2026

3개 스위치를 관리:
  S1    (dpid=1): 액세스 — HTB 큐 배정 + S_edge로 포워딩
  S_edge(dpid=2): 엣지   — SFC 라우팅 (in_port + dst_ip → NFV 경유)
  S_core (dpid=3): 코어   — 서버로 포워딩

SFC 체인 (S_edge 플로우 룰):
  URLLC (dst=10.0.0.4): in_port=S1 → nfv_fw → s_core
  eMBB  (dst=10.0.0.5): in_port=S1 → nfv_fw → nfv_cache → s_core
  mMTC  (dst=10.0.0.6): in_port=S1 → nfv_fw → nfv_aggr  → s_core

REST API (포트 8080):
  GET  /slices              → 슬라이스 상태 + 연결 현황
  POST /slices/reassign     → 직접 재배정
  POST /slices/request      → Gemma3 부하 기반 최적 배정
  POST /clients/register    → hostname 등록 (topology.py에서 호출)
"""

import sys
import os
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ipv4, ether_types
from os_ken.lib import hub

import eventlet
import eventlet.wsgi

import config as cfg

_agent = None

def _get_agent():
    global _agent
    if _agent is None:
        import agent.slicing_agent as agent_mod
        _agent = agent_mod
    return _agent


class SliceController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.datapaths   = {}     # dpid → datapath
        self.mac_to_port = {}     # dpid → {mac: port}

        # hostname 레지스트리: ip → name
        self.ip_to_name: dict[str, str] = {}
        # 분류 캐시: ip → {name, service, server_ip, server_name, sfc_chain, in_port, ts}
        self.classified: dict[str, dict] = {}
        # 현재 호스트 서비스 배정 (재배정 반영)
        self.host_service: dict[str, str] = {}

        # 정적 클라이언트 사전 등록
        for name, profile in cfg.HOST_PROFILES.items():
            self.ip_to_name[profile["ip"]] = name
            self.host_service[profile["ip"]] = profile["service"]

        hub.spawn(self._start_rest_server)
        self.logger.info("SliceController (SFC) initialized — REST API on port %d",
                         cfg.CONTROLLER_PORT)

    # ------------------------------------------------------------------
    # 헬퍼
    # ------------------------------------------------------------------

    def add_flow(self, datapath, priority, match, actions, idle_timeout=0):
        ofproto = datapath.ofproto
        parser  = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod  = parser.OFPFlowMod(datapath=datapath, priority=priority,
                                  match=match, instructions=inst,
                                  idle_timeout=idle_timeout)
        datapath.send_msg(mod)

    # ------------------------------------------------------------------
    # OpenFlow 이벤트 핸들러
    # ------------------------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        dpid     = datapath.id
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser

        self.datapaths[dpid] = datapath

        # table-miss: 컨트롤러로 (MAC 학습 + 동적 클라이언트 분류)
        self.add_flow(datapath, priority=0,
                      match=parser.OFPMatch(),
                      actions=[parser.OFPActionOutput(ofproto.OFPP_CONTROLLER,
                                                       ofproto.OFPCML_NO_BUFFER)])

        if dpid == cfg.DPID_S1:
            self._install_s1_rules(datapath)
        elif dpid == cfg.DPID_SEDGE:
            self._install_sedge_sfc_rules(datapath)
        elif dpid == cfg.DPID_SCORE:
            self._install_s_core_rules(datapath)

        self.logger.info("Switch dpid=%d connected", dpid)

    def _install_s1_rules(self, datapath):
        """S1: 정적 클라이언트 → HTB 큐 배정 + S_edge 포워딩."""
        parser = datapath.ofproto_parser
        for in_port, src_ip, dst_ip, queue_id in cfg.get_s1_egress_rules():
            match   = parser.OFPMatch(in_port=in_port,
                                       eth_type=ether_types.ETH_TYPE_IP,
                                       ipv4_src=src_ip,
                                       ipv4_dst=dst_ip)
            actions = [parser.OFPActionSetQueue(queue_id),
                       parser.OFPActionOutput(cfg.S1_PORT_SEDGE)]
            self.add_flow(datapath, priority=10, match=match, actions=actions)

            name    = self.ip_to_name.get(src_ip, src_ip)
            service = cfg.SERVICES[self._queue_to_service(queue_id)]
            server  = cfg.get_server_for_service(self._queue_to_service(queue_id))
            chain   = " → ".join(cfg.get_sfc_chain(self._queue_to_service(queue_id)))
            self.logger.info("S1 rule: %s (%s) queue=%d → [%s] → %s",
                             name, src_ip, queue_id, chain, server["name"])

            # 분류 캐시 등록
            svc_name = self._queue_to_service(queue_id)
            self.classified[src_ip] = {
                "name":        name,
                "service":     svc_name,
                "server_ip":   server["ip"],
                "server_name": server["name"],
                "sfc_chain":   cfg.get_sfc_chain(svc_name),
                "in_port":     in_port,
                "ts":          time.time(),
                "auto":        False,
            }

    def _install_sedge_sfc_rules(self, datapath):
        """S_edge: SFC 라우팅 룰 설치.

        각 서버 IP(= 슬라이스 식별자)와 in_port 조합으로 경로 결정:
          URLLC (dst=10.0.0.4):
            in_port=S1 → output(nfv_fw)
            in_port=nfv_fw → output(s_core)
          eMBB (dst=10.0.0.5):
            in_port=S1 → output(nfv_fw)
            in_port=nfv_fw → output(nfv_cache)
            in_port=nfv_cache → output(s_core)
          mMTC (dst=10.0.0.6):
            in_port=S1 → output(nfv_fw)
            in_port=nfv_fw → output(nfv_aggr)
            in_port=nfv_aggr → output(s_core)
        """
        parser = datapath.ofproto_parser

        for service, chain in cfg.SFC_CHAINS.items():
            server  = cfg.get_server_for_service(service)
            dst_ip  = server["ip"]
            queue_id = cfg.SERVICES[service]["queue_id"]

            hops = chain + ["__s_core__"]   # 가상 마지막 홉
            prev_port = cfg.SEDGE_PORT_S1   # 첫 번째 규칙의 in_port = S1 포트

            for i, nfv_name in enumerate(hops):
                if nfv_name == "__s_core__":
                    out_port = cfg.SEDGE_PORT_SCORE
                else:
                    out_port = cfg.NFV_TO_SEDGE_PORT[nfv_name]

                match = parser.OFPMatch(
                    in_port=prev_port,
                    eth_type=ether_types.ETH_TYPE_IP,
                    ipv4_dst=dst_ip,
                )
                actions = [
                    parser.OFPActionSetQueue(queue_id),
                    parser.OFPActionOutput(out_port),
                ]
                self.add_flow(datapath, priority=10, match=match, actions=actions)
                self.logger.info(
                    "SEDGE SFC [%s] in_port=%d, dst=%s → out_port=%d (%s)",
                    service, prev_port, dst_ip, out_port, nfv_name)

                # 다음 룰의 in_port = 이번 out_port (NFV가 같은 포트로 되돌아오므로)
                prev_port = out_port

    def _install_s_core_rules(self, datapath):
        """S_core: dst_ip → 서버 포트로 포워딩."""
        parser = datapath.ofproto_parser
        for srv in cfg.SERVERS.values():
            match   = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                       ipv4_dst=srv["ip"])
            actions = [parser.OFPActionOutput(srv["s_core_port"])]
            self.add_flow(datapath, priority=10, match=match, actions=actions)
            self.logger.info("S_CORE rule: dst=%s → port%d (%s)",
                             srv["ip"], srv["s_core_port"], srv["name"])

    def _queue_to_service(self, queue_id: int) -> str:
        for name, svc in cfg.SERVICES.items():
            if svc["queue_id"] == queue_id:
                return name
        return "mmtc"

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        """MAC 학습 + 동적 클라이언트 자동 분류."""
        msg      = ev.msg
        datapath = msg.datapath
        ofproto  = datapath.ofproto
        parser   = datapath.ofproto_parser
        in_port  = msg.match['in_port']
        dpid     = datapath.id

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        src_mac, dst_mac = eth.src, eth.dst
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src_mac] = in_port
        out_port = self.mac_to_port[dpid].get(dst_mac, ofproto.OFPP_FLOOD)

        ip_pkt = pkt.get_protocol(ipv4.ipv4)

        # S1에서 S_edge 방향으로 나가는 새 IP 클라이언트 감지
        if (dpid == cfg.DPID_S1 and ip_pkt
                and out_port == cfg.S1_PORT_SEDGE):
            src_ip = ip_pkt.src
            if (src_ip not in cfg.SERVER_IPS
                    and src_ip not in self.classified):
                self._classify_and_install(datapath, src_ip, in_port, ip_pkt)

        # L2 포워딩 (return traffic + non-IP)
        actions = [parser.OFPActionOutput(out_port)]
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=dst_mac)
            self.add_flow(datapath, priority=1, match=match,
                          actions=actions, idle_timeout=30)

        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions,
            data=msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None)
        datapath.send_msg(out)

    def _classify_and_install(self, datapath, src_ip: str,
                               in_port: int, ip_pkt):
        """새 클라이언트 hostname 분류 → S1 flow rule + 분류 캐시 등록."""
        hostname = self.ip_to_name.get(src_ip, "")
        service, is_rule_based = cfg.classify_hostname(hostname)

        if not is_rule_based and hostname:
            # 모호한 hostname → Gemma3 비동기 판단 (즉시는 기본값 사용)
            hub.spawn(self._gemma3_classify_async, src_ip, hostname,
                      in_port, service, ip_pkt.proto)

        server   = cfg.get_server_for_service(service)
        queue_id = cfg.SERVICES[service]["queue_id"]
        chain    = cfg.get_sfc_chain(service)

        # S1 flow rule 설치
        parser = datapath.ofproto_parser
        match  = parser.OFPMatch(in_port=in_port,
                                  eth_type=ether_types.ETH_TYPE_IP,
                                  ipv4_src=src_ip,
                                  ipv4_dst=server["ip"])
        actions = [parser.OFPActionSetQueue(queue_id),
                   parser.OFPActionOutput(cfg.S1_PORT_SEDGE)]
        self.add_flow(datapath, priority=10, match=match, actions=actions)

        self.classified[src_ip] = {
            "name":        hostname or src_ip,
            "service":     service,
            "server_ip":   server["ip"],
            "server_name": server["name"],
            "sfc_chain":   chain,
            "in_port":     in_port,
            "ts":          time.time(),
            "auto":        True,
        }
        self.host_service[src_ip] = service

        self.logger.info(
            "Auto-classified: %s (%s) → %s via [%s] → %s%s",
            hostname or src_ip, src_ip, service.upper(),
            " → ".join(chain), server["name"],
            " (Gemma3 pending)" if not is_rule_based else "")

    def _gemma3_classify_async(self, src_ip: str, hostname: str,
                                in_port: int, current_service: str,
                                protocol: int):
        """Gemma3로 모호한 hostname 비동기 분류. 결과 다르면 룰 재설치."""
        try:
            agent = _get_agent()
            pkt_info = {"protocol": "UDP" if protocol == 17 else "TCP"}
            gemma_service = agent.classify_new_host(hostname, pkt_info)

            if gemma_service and gemma_service != current_service:
                server   = cfg.get_server_for_service(gemma_service)
                queue_id = cfg.SERVICES[gemma_service]["queue_id"]
                chain    = cfg.get_sfc_chain(gemma_service)
                dp = self.datapaths.get(cfg.DPID_S1)
                if dp:
                    parser = dp.ofproto_parser
                    match  = parser.OFPMatch(in_port=in_port,
                                              eth_type=ether_types.ETH_TYPE_IP,
                                              ipv4_src=src_ip,
                                              ipv4_dst=server["ip"])
                    actions = [parser.OFPActionSetQueue(queue_id),
                               parser.OFPActionOutput(cfg.S1_PORT_SEDGE)]
                    self.add_flow(dp, priority=10, match=match, actions=actions)

                if src_ip in self.classified:
                    self.classified[src_ip].update({
                        "service":     gemma_service,
                        "server_ip":   server["ip"],
                        "server_name": server["name"],
                        "sfc_chain":   chain,
                    })
                self.host_service[src_ip] = gemma_service
                self.logger.info("Gemma3 override: %s → %s (was %s)",
                                 hostname, gemma_service, current_service)
            else:
                self.logger.info("Gemma3 confirms: %s → %s",
                                 hostname, current_service)
        except Exception as e:
            self.logger.warning("Gemma3 async classify failed: %s", e)

    # ------------------------------------------------------------------
    # 제어 메서드
    # ------------------------------------------------------------------

    def reassign_host(self, host_ip: str, to_service: str) -> dict:
        """호스트를 다른 슬라이스로 재배정."""
        if to_service not in cfg.SERVICES:
            raise ValueError(f"Unknown service: {to_service}")

        from_service = self.host_service.get(host_ip, "unknown")
        server       = cfg.get_server_for_service(to_service)
        queue_id     = cfg.SERVICES[to_service]["queue_id"]
        chain        = cfg.get_sfc_chain(to_service)
        name         = self.ip_to_name.get(host_ip, host_ip)

        in_port = (self.classified.get(host_ip, {}).get("in_port")
                   or (cfg.get_host_by_ip(host_ip) or {}).get("s1_port"))

        self.host_service[host_ip] = to_service

        dp = self.datapaths.get(cfg.DPID_S1)
        if dp and in_port:
            parser  = dp.ofproto_parser
            match   = parser.OFPMatch(in_port=in_port,
                                       eth_type=ether_types.ETH_TYPE_IP,
                                       ipv4_src=host_ip,
                                       ipv4_dst=server["ip"])
            actions = [parser.OFPActionSetQueue(queue_id),
                       parser.OFPActionOutput(cfg.S1_PORT_SEDGE)]
            self.add_flow(dp, priority=10, match=match, actions=actions)

        if host_ip in self.classified:
            self.classified[host_ip].update({
                "service":     to_service,
                "server_ip":   server["ip"],
                "server_name": server["name"],
                "sfc_chain":   chain,
            })

        self.logger.info("Reassigned %s (%s): %s → %s via [%s]",
                         name, host_ip, from_service, to_service,
                         " → ".join(chain))
        return {
            "name": name, "host_ip": host_ip,
            "from_service": from_service, "to_service": to_service,
            "sfc_chain": chain, "server": server["name"],
            "queue_id": queue_id, "status": "ok",
        }

    def register_client(self, name: str, ip: str) -> dict:
        self.ip_to_name[ip] = name
        self.logger.info("Registered: %s → %s", name, ip)
        return {"status": "ok", "name": name, "ip": ip}

    def get_slice_state(self) -> dict:
        slices = {}
        for svc_name, svc in cfg.SERVICES.items():
            clients = [
                {"name": info["name"], "ip": ip, "auto": info.get("auto", False)}
                for ip, info in self.classified.items()
                if info["service"] == svc_name
            ]
            server = cfg.SERVICE_TO_SERVER[svc_name]
            slices[svc_name] = {
                "description": svc["description"],
                "queue_id":    svc["queue_id"],
                "server":      server["name"],
                "server_ip":   server["ip"],
                "sfc_chain":   cfg.SFC_CHAINS[svc_name],
                "policy": {
                    "gbr_mbps": svc["gbr_mbps"],
                    "mbr_mbps": svc["mbr_mbps"],
                    "pdb_ms":   svc["pdb_ms"],
                    "per":      svc["per"],
                },
                "clients": clients,
            }

        connections = sorted(
            [
                {
                    "name":        info["name"],
                    "ip":          ip,
                    "service":     info["service"],
                    "server_name": info["server_name"],
                    "sfc_chain":   info["sfc_chain"],
                    "auto":        info.get("auto", False),
                    "ts":          info.get("ts", 0),
                }
                for ip, info in self.classified.items()
            ],
            key=lambda x: x["ts"],
        )
        return {
            "slices":      slices,
            "switches":    list(self.datapaths.keys()),
            "connections": connections,
        }

    # ------------------------------------------------------------------
    # REST 서버
    # ------------------------------------------------------------------

    def _start_rest_server(self):
        sock = eventlet.listen(('0.0.0.0', cfg.CONTROLLER_PORT))
        self.logger.info("REST API listening on port %d", cfg.CONTROLLER_PORT)
        eventlet.wsgi.server(sock, self._wsgi_app, log=self.logger)

    def _wsgi_app(self, environ, start_response):
        path   = environ.get('PATH_INFO', '/')
        method = environ.get('REQUEST_METHOD', 'GET')

        def respond(status, body_dict):
            body = json.dumps(body_dict, indent=2).encode()
            start_response(status, [('Content-Type', 'application/json'),
                                     ('Content-Length', str(len(body)))])
            return [body]

        def read_body():
            length = int(environ.get('CONTENT_LENGTH', 0) or 0)
            return json.loads(environ['wsgi.input'].read(length))

        try:
            if path == '/slices' and method == 'GET':
                return respond('200 OK', self.get_slice_state())
            elif path == '/slices/reassign' and method == 'POST':
                d = read_body()
                return respond('200 OK', self.reassign_host(d['host_ip'], d['to_service']))
            elif path == '/slices/request' and method == 'POST':
                d = read_body()
                result = _get_agent().handle_service_request(
                    d['host_ip'], d['requested_service'])
                return respond('200 OK', result)
            elif path == '/clients/register' and method == 'POST':
                d = read_body()
                return respond('200 OK', self.register_client(d['name'], d['ip']))
            else:
                return respond('404 Not Found', {"error": "Not Found"})
        except (KeyError, ValueError) as e:
            return respond('400 Bad Request', {"error": str(e)})
        except Exception as e:
            self.logger.exception("REST error")
            return respond('500 Internal Server Error', {"error": str(e)})
