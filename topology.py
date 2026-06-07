#!/usr/bin/env python3
"""
SDN Network Slicing (SFC 버전) — Smart City Topology
EC5209 Advanced Computer Networking, Spring 2026

토폴로지:
  클라이언트(S1) ── S_edge(NFV) ── S_core(서버)

SFC 체인:
  URLLC: S1 → sedge → nfv_fw              → s_core → AutoDrive Hub
  eMBB:  S1 → sedge → nfv_fw → nfv_cache  → s_core → EntertainPort (ent_port)
  mMTC:  S1 → sedge → nfv_fw → nfv_aggr   → s_core → CityPulse Hub

지연 차이는 netem 주입이 아닌 경유 홉 수 차이에서 자연 발생.
HTB 큐는 GBR/MBR 보장 용도로만 사용.
"""

import subprocess
import sys
import os

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI

import config as cfg

# 동적으로 할당된 IP를 추적 (h.IP()는 런타임 addHost 후 None 반환 버그 우회)
_dynamic_ips: set[str] = set()


def setup_qos(iface: str = cfg.BOTTLENECK_IFACE):
    """S1 → S_edge 병목 인터페이스에 HTB 큐 + DSCP Strict Priority 설정.

    2계층 QoS:
      (1) HTB rate 보장 — 기존 GBR/MBR 수치 유지 (URLLC 10Mbps 고정,
          eMBB 20~50Mbps, mMTC 1~10Mbps).
      (2) Strict Priority 레이어 — HTB class 의 prio 파라미터(other-config:priority)
          로 큐 간 우선순위를 부여하고, DSCP 값을 보고 큐를 고르는 tc u32 filter 를
          root(1:0)에 얹는다. URLLC(EF/46) → 1:1 이 항상 최우선, eMBB(AF41/34) →
          1:2, mMTC(BE/0) → 1:3(default) 순으로 처리된다.

    참고: OVS 가 root htb qdisc(handle 1:)를 직접 관리하므로, 별도의 prio qdisc 를
    root 로 두면 OVS 설정과 충돌한다. 대신 HTB class prio + DSCP u32 filter 조합으로
    동일한 strict-priority 효과를 얻는다.
    """
    info("*** Cleaning up existing QoS settings\n")
    subprocess.run(f"ovs-vsctl clear port {iface} qos",
                   shell=True, stderr=subprocess.DEVNULL)
    subprocess.run("ovs-vsctl --all destroy qos; ovs-vsctl --all destroy queue",
                   shell=True, stderr=subprocess.DEVNULL)

    info(f"*** Setting up HTB QoS + Strict Priority on {iface}\n")
    cmd = (
        f"ovs-vsctl set port {iface} qos=@q "
        f"-- --id=@q create QoS type=linux-htb "
        f"other-config:max-rate=100000000 "
        f"other-config:default-queue=2 "
        f"queues=0=@q0,1=@q1,2=@q2 "
        # Queue 0: URLLC — GBR=MBR=10Mbps, priority=0 (최우선)
        f"-- --id=@q0 create Queue "
        f"other-config:min-rate=10000000 other-config:max-rate=10000000 "
        f"other-config:priority=0 "
        # Queue 1: eMBB — GBR=20Mbps, MBR=50Mbps, priority=1
        f"-- --id=@q1 create Queue "
        f"other-config:min-rate=20000000 other-config:max-rate=50000000 "
        f"other-config:priority=1 "
        # Queue 2: mMTC — GBR=1Mbps, MBR=10Mbps, priority=2 (최하위)
        f"-- --id=@q2 create Queue "
        f"other-config:min-rate=1000000 other-config:max-rate=10000000 "
        f"other-config:priority=2"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        info(f"*** QoS setup failed: {result.stderr}\n")
        return

    info("*** HTB QoS setup complete (no netem — latency from SFC hop count)\n")
    info("    Queue 0 (URLLC/EF  ): GBR=MBR=10Mbps, prio=0\n")
    info("    Queue 1 (eMBB/AF41 ): GBR=20Mbps, MBR=50Mbps, prio=1\n")
    info("    Queue 2 (mMTC/BE   ): GBR=1Mbps,  MBR=10Mbps, prio=2\n")

    setup_dscp_filters(iface)


def setup_dscp_filters(iface: str = cfg.BOTTLENECK_IFACE):
    """DSCP → HTB class strict-priority 매핑 tc u32 filter 설치.

    OVS htb root qdisc(handle 1:)에 filter 를 얹어, IP 헤더 DSCP 값으로 큐를
    선택한다. tc u32 에는 'dscp' 키워드가 없으므로 ToS 바이트(DSCP<<2)를
    'match ip tos <tos> 0xfc' 로 매칭한다(0xfc 마스크로 ECN 2비트 무시).

    동등한 수동 명령 예시:
      tc filter add dev s1-eth4 protocol ip parent 1:0 prio 1 \\
          u32 match ip tos 0xb8 0xfc flowid 1:1   # DSCP 46 (EF)   → URLLC
      tc filter add dev s1-eth4 protocol ip parent 1:0 prio 2 \\
          u32 match ip tos 0x88 0xfc flowid 1:2   # DSCP 34 (AF41) → eMBB
    """
    info(f"*** Installing DSCP strict-priority filters on {iface}\n")
    for fprio, (dscp, tos, htb_class, service) in enumerate(
            cfg.get_dscp_filter_map(), start=1):
        # DSCP 0(BE)은 default-queue(2)로 자연스럽게 떨어지므로 filter 생략.
        if dscp == 0:
            info(f"    DSCP {dscp:2d} ({service:5s}) → {htb_class} "
                 f"(default-queue, filter 생략)\n")
            continue
        cmd = (
            f"tc filter add dev {iface} protocol ip parent 1:0 prio {fprio} "
            f"u32 match ip tos 0x{tos:02x} 0xfc flowid {htb_class}"
        )
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if r.returncode == 0:
            info(f"    DSCP {dscp:2d} ({service:5s}) → tos 0x{tos:02x} "
                 f"→ flowid {htb_class}\n")
        else:
            info(f"    filter add 실패 (DSCP {dscp}): {r.stderr.strip()}\n")

    # ----------------------------------------------------------------------
    # 동작 확인 방법:
    #   ovs-vsctl list qos                         # QoS row + queue 참조 확인
    #   ovs-vsctl list queue                       # min/max-rate, priority 확인
    #   tc -s qdisc show dev s1-eth4               # htb qdisc + 큐별 통계
    #   tc -s class show dev s1-eth4               # class 별 prio / 전송 바이트
    #   tc -s filter show dev s1-eth4 parent 1:0   # DSCP u32 filter 매칭 카운트
    #   ovs-ofctl -O OpenFlow13 dump-flows s1      # set_field(dscp)/set_queue 확인
    # 부하 테스트(슬라이스 격리):
    #   mMTC/eMBB 포화 중 URLLC iperf3 가 10Mbps 를 유지하는지,
    #   tc -s class show 의 URLLC(1:1) 우선 전송량으로 strict-priority 확인.
    # ----------------------------------------------------------------------


def register_client(name: str, ip: str, requirements: str = "") -> bool:
    """컨트롤러에 클라이언트 hostname 및 요구사항 등록."""
    import requests
    try:
        payload = {"name": name, "ip": ip}
        if requirements:
            payload["requirements"] = requirements
        resp = requests.post(
            f"http://{cfg.CONTROLLER_HOST}:{cfg.CONTROLLER_PORT}/clients/register",
            json=payload,
            timeout=3,
        )
        return resp.ok
    except Exception:
        return False


def add_client(net, name: str, switch, ip: str = None,
               requirements: str = "") -> object:
    """런타임에 클라이언트 동적 추가.

    Mininet CLI에서:
        py add_client(net, 'device_01', s1)
        py add_client(net, 'device_01', s1, requirements='latency < 5ms, bandwidth 8Mbps')
    """
    global _dynamic_ips
    if ip is None:
        static_ips = (
            {p["ip"] for p in cfg.HOST_PROFILES.values()}
            | {s["ip"] for s in cfg.SERVERS.values()}
        )
        used = static_ips | _dynamic_ips
        idx = 7
        while f"10.0.0.{idx}" in used:
            idx += 1
        ip = f"10.0.0.{idx}/24"
        _dynamic_ips.add(f"10.0.0.{idx}")

    host = net.addHost(name, ip=ip)
    link = net.addLink(host, switch, bw=1000)

    host.cmd(f"ip addr add {ip} dev {name}-eth0")
    host.cmd(f"ifconfig {name}-eth0 up")
    host.cmd(f"ip route add 10.0.0.0/24 dev {name}-eth0")
    switch.attach(link.intf2)

    raw_ip = ip.split("/")[0]
    ok = register_client(name, raw_ip, requirements)

    service, rule_based = cfg.classify_hostname(name)
    server = cfg.get_server_for_service(service)
    chain  = " → ".join(cfg.get_sfc_chain(service))

    info(f"*** Added client: {name} ({raw_ip})\n"
         f"    → {service.upper()} via [{chain}] → {server['name']}"
         f"  {'[registered]' if ok else '[register failed]'}\n")
    return host


def build_network():
    """스마트 시티 SFC 네트워크 구성."""
    net = Mininet(controller=RemoteController,
                  switch=OVSSwitch,
                  link=TCLink,
                  autoSetMacs=True)

    c0 = net.addController("c0", controller=RemoteController,
                            ip="127.0.0.1", port=6633)

    # 스위치 (DPID 명시)
    s1    = net.addSwitch("s1",    protocols="OpenFlow13",
                          dpid="0000000000000001")
    sedge = net.addSwitch("sedge", protocols="OpenFlow13",
                          dpid="0000000000000002")
    s_core = net.addSwitch("s_core", protocols="OpenFlow13",
                          dpid="0000000000000003")

    # 데모 클라이언트 (S1 쪽)
    vehicle_01   = net.addHost("vehicle_01",   ip="10.0.0.1/24")
    camera_01    = net.addHost("camera_01",    ip="10.0.0.2/24")
    sensor_01    = net.addHost("sensor_01",    ip="10.0.0.3/24")

    # 서버 (S_core 쪽)
    autodrive    = net.addHost("autodrive",    ip="10.0.0.4/24")
    ent_port= net.addHost("ent_port",ip="10.0.0.5/24")
    citypulse    = net.addHost("citypulse",    ip="10.0.0.6/24")

    # NFV 호스트 (S_edge 쪽)
    nfv_fw       = net.addHost("nfv_fw",       ip="10.1.0.1/24")
    nfv_cache    = net.addHost("nfv_cache",    ip="10.1.0.2/24")
    nfv_aggr     = net.addHost("nfv_aggr",     ip="10.1.0.3/24")

    # S1 ─ 클라이언트 링크  (포트 순서 = s1_port)
    net.addLink(vehicle_01,  s1, bw=1000)   # s1-eth1
    net.addLink(camera_01,   s1, bw=1000)   # s1-eth2
    net.addLink(sensor_01,   s1, bw=1000)   # s1-eth3
    # S1 ─ S_edge 병목 링크 (HTB 큐 적용)
    net.addLink(s1, sedge, bw=100)           # s1-eth4, sedge-eth1

    # S_edge ─ NFV 링크  (sedge 포트 순서)
    net.addLink(sedge, nfv_fw,    bw=1000)   # sedge-eth2
    net.addLink(sedge, nfv_cache, bw=1000)   # sedge-eth3
    net.addLink(sedge, nfv_aggr,  bw=1000)   # sedge-eth4
    # S_edge ─ S_core 링크
    net.addLink(sedge, s_core, bw=100)         # sedge-eth5, s_core-eth1

    # S_core ─ 서버 링크
    net.addLink(s_core, autodrive,     bw=1000)  # s_core-eth2
    net.addLink(s_core, ent_port, bw=1000)  # s_core-eth3
    net.addLink(s_core, citypulse,     bw=1000)  # s_core-eth4

    net.build()
    c0.start()
    s1.start([c0])
    sedge.start([c0])
    s_core.start([c0])

    # HTB 큐 설정 (S1 → S_edge 병목)
    setup_qos(cfg.BOTTLENECK_IFACE)

    # NFV 스크립트 시작 (promiscuous 모드로 경유 + 로그)
    project_dir = os.path.dirname(os.path.abspath(__file__))
    for nfv_name, nfv_info in cfg.NFV_HOSTS.items():
        script = os.path.join(project_dir, nfv_info["script"])
        host   = net.get(nfv_name)
        host.cmd(f"python3 {script} > /tmp/{nfv_name}.log 2>&1 &")
        info(f"*** Started {nfv_name} ({nfv_info['description']})\n")

    # 서버 HTTP 리스너 (슬라이스 연결 확인용)
    for srv_name in ("autodrive", "ent_port", "citypulse"):
        net.get(srv_name).cmd(
            f"python3 -m http.server 8000 > /tmp/{srv_name}.log 2>&1 &")

    return net, s1


def create_topology():
    """CLI 모드."""
    net, s1 = build_network()

    info("\n*** Smart City SFC Network Slicing\n")
    info("  SFC Chains:\n")
    info("    URLLC: S1 → sedge → [nfv_fw]                  → s_core → AutoDrive Hub\n")
    info("    eMBB:  S1 → sedge → [nfv_fw] → [nfv_cache]    → s_core → EntertainPort (ent_port)\n")
    info("    mMTC:  S1 → sedge → [nfv_fw] → [nfv_aggr]     → s_core → CityPulse Hub\n")
    info("  Dynamic client:\n")
    info("    py vehicle_02 = add_client(net, 'vehicle_02', s1)\n\n")

    import builtins
    builtins.add_client = add_client
    builtins.net = net
    builtins.s1 = s1
    CLI(net)

    info("*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    create_topology()
