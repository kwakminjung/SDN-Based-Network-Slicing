#!/usr/bin/env python3
"""
SDN Network Slicing (SFC 버전) — Smart City Topology
EC5209 Advanced Computer Networking, Spring 2026

토폴로지:
  클라이언트(S1) ── S_edge(NFV) ── S_core(서버)

SFC 체인:
  URLLC: S1 → sedge → nfv_fw              → s_core → AutoDrive Hub
  eMBB:  S1 → sedge → nfv_fw → nfv_cache  → s_core → EntertainPort
  mMTC:  S1 → sedge → nfv_fw → nfv_aggr   → s_core → CityPulse Hub

지연 차이는 netem 주입이 아닌 경유 홉 수 차이에서 자연 발생.
HTB 큐는 GBR/MBR 보장 용도로만 사용.
"""

import subprocess
import sys
import os
import time

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI

import config as cfg


def setup_qos(iface: str = cfg.BOTTLENECK_IFACE):
    """S1 → S_edge 병목 인터페이스에 HTB 큐 설정 (GBR/MBR 보장용)."""
    info("*** Cleaning up existing QoS settings\n")
    subprocess.run(f"ovs-vsctl clear port {iface} qos",
                   shell=True, stderr=subprocess.DEVNULL)
    subprocess.run("ovs-vsctl --all destroy qos; ovs-vsctl --all destroy queue",
                   shell=True, stderr=subprocess.DEVNULL)

    info(f"*** Setting up HTB QoS on {iface}\n")
    cmd = (
        f"ovs-vsctl set port {iface} qos=@q "
        f"-- --id=@q create QoS type=linux-htb "
        f"other-config:max-rate=100000000 "
        f"other-config:default-queue=2 "
        f"queues=0=@q0,1=@q1,2=@q2 "
        # Queue 0: URLLC — GBR=MBR=10Mbps
        f"-- --id=@q0 create Queue "
        f"other-config:min-rate=10000000 other-config:max-rate=10000000 "
        # Queue 1: eMBB — GBR=20Mbps, MBR=50Mbps
        f"-- --id=@q1 create Queue "
        f"other-config:min-rate=20000000 other-config:max-rate=50000000 "
        # Queue 2: mMTC — GBR=1Mbps, MBR=10Mbps
        f"-- --id=@q2 create Queue "
        f"other-config:min-rate=1000000 other-config:max-rate=10000000"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        info("*** HTB QoS setup complete (no netem — latency from SFC hop count)\n")
        info("    Queue 0 (URLLC): GBR=MBR=10Mbps\n")
        info("    Queue 1 (eMBB):  GBR=20Mbps, MBR=50Mbps\n")
        info("    Queue 2 (mMTC):  GBR=1Mbps,  MBR=10Mbps\n")
    else:
        info(f"*** QoS setup failed: {result.stderr}\n")


def register_client(name: str, ip: str) -> bool:
    """컨트롤러에 클라이언트 hostname 등록."""
    import requests
    try:
        resp = requests.post(
            f"http://{cfg.CONTROLLER_HOST}:{cfg.CONTROLLER_PORT}/clients/register",
            json={"name": name, "ip": ip},
            timeout=3,
        )
        return resp.ok
    except Exception:
        return False


def add_client(net, name: str, switch, ip: str = None) -> object:
    """런타임에 클라이언트 동적 추가.

    Mininet CLI에서:
        py vehicle_02 = add_client(net, 'vehicle_02', s1)
        py vehicle_02.cmd('ping 10.0.0.4 &')
    """
    if ip is None:
        existing_dynamic = [h for h in net.hosts if h.ip.startswith("10.0.1.")]
        idx = len(existing_dynamic) + 1
        ip = f"10.0.1.{idx}/24"

    host = net.addHost(name, ip=ip)
    link = net.addLink(host, switch, bw=1000)
    host.cmd(f"ifconfig {name}-eth0 up")
    switch.attach(link.intf2)

    raw_ip = ip.split("/")[0]
    ok = register_client(name, raw_ip)

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
    entertainport= net.addHost("entertainport",ip="10.0.0.5/24")
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
    net.addLink(s_core, entertainport, bw=1000)  # s_core-eth3
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
    for srv_name in ("autodrive", "entertainport", "citypulse"):
        net.get(srv_name).cmd(
            f"python3 -m http.server 8000 > /tmp/{srv_name}.log 2>&1 &")

    return net


def create_topology():
    """CLI 모드."""
    net = build_network()

    info("\n*** Smart City SFC Network Slicing\n")
    info("  SFC Chains:\n")
    info("    URLLC: S1 → sedge → [nfv_fw]                  → s_core → AutoDrive Hub\n")
    info("    eMBB:  S1 → sedge → [nfv_fw] → [nfv_cache]    → s_core → EntertainPort\n")
    info("    mMTC:  S1 → sedge → [nfv_fw] → [nfv_aggr]     → s_core → CityPulse Hub\n")
    info("  Dynamic client:\n")
    info("    py vehicle_02 = add_client(net, 'vehicle_02', s1)\n\n")

    CLI(net)

    info("*** Stopping network\n")
    net.stop()


def create_topology_and_measure():
    """측정 모드."""
    from measurement.run_measurement import run_measurement

    net = build_network()
    info("*** Waiting for controller to install flows...\n")
    net.pingAll(timeout=2)
    time.sleep(3)
    net.pingAll(timeout=2)
    time.sleep(2)

    run_measurement(net)
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    if "--measure" in sys.argv:
        create_topology_and_measure()
    else:
        create_topology()
