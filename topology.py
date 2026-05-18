#!/usr/bin/env python3
"""
SDN Network Slicing - Base Topology
EC5209 Advanced Computer Networking, Spring 2026

Topology:
  H1 (Slice A) ─┐           ┌─ H4 (Slice A)
  H2 (Slice B) ─┤── S1 ── S2 ├─ H5 (Slice B)
  H3 (Slice C) ─┘           └─ H6 (Slice C)

Slice assignment:
  Slice A (High Priority)  : H1 <-> H4  (10.0.0.1, 10.0.0.4)
  Slice B (Medium Priority): H2 <-> H5  (10.0.0.2, 10.0.0.5)
  Slice C (Best Effort)    : H3 <-> H6  (10.0.0.3, 10.0.0.6)
"""

import subprocess

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI


def setup_qos(bottleneck_port="s1-eth4"):
    """S1-S2 병목 링크에 HTB QoS 큐 설정
    큐 0: Slice A — 10Mbps 보장
    큐 1: Slice B — 5Mbps 상한
    큐 2: Slice C — 베스트 에포트 (100Mbps)
    """
    info("*** Cleaning up existing QoS settings\n")
    subprocess.run(
        f"ovs-vsctl clear port {bottleneck_port} qos",
        shell=True, stderr=subprocess.DEVNULL)
    subprocess.run(
        "ovs-vsctl --all destroy qos; ovs-vsctl --all destroy queue",
        shell=True, stderr=subprocess.DEVNULL)

    info(f"*** Setting up HTB QoS on {bottleneck_port}\n")
    cmd = (
        f"ovs-vsctl set port {bottleneck_port} qos=@q "
        f"-- --id=@q create QoS type=linux-htb "
        f"other-config:max-rate=100000000 "
        f"queues=0=@q0,1=@q1,2=@q2 "
        f"-- --id=@q0 create Queue "
        f"other-config:min-rate=10000000 other-config:max-rate=10000000 "
        f"-- --id=@q1 create Queue "
        f"other-config:max-rate=5000000 "
        f"-- --id=@q2 create Queue "
        f"other-config:max-rate=100000000"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        info("*** QoS setup complete\n")
        info("    Queue 0 (Slice A): 10Mbps guaranteed\n")
        info("    Queue 1 (Slice B): 5Mbps cap\n")
        info("    Queue 2 (Slice C): best effort\n")
    else:
        info(f"*** QoS setup failed: {result.stderr}\n")


def create_topology():
    net = Mininet(controller=RemoteController,
                  switch=OVSSwitch,
                  link=TCLink,
                  autoSetMacs=True)

    info("*** Creating controller\n")
    c0 = net.addController("c0",
                            controller=RemoteController,
                            ip="127.0.0.1",
                            port=6633)

    info("*** Creating switches\n")
    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    s2 = net.addSwitch("s2", protocols="OpenFlow13")

    info("*** Creating hosts\n")
    # Slice A - High Priority
    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h4 = net.addHost("h4", ip="10.0.0.4/24")

    # Slice B - Medium Priority
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    h5 = net.addHost("h5", ip="10.0.0.5/24")

    # Slice C - Best Effort
    h3 = net.addHost("h3", ip="10.0.0.3/24")
    h6 = net.addHost("h6", ip="10.0.0.6/24")

    info("*** Creating links\n")
    # Host - Switch links (1Gbps)
    net.addLink(h1, s1, bw=1000)
    net.addLink(h2, s1, bw=1000)
    net.addLink(h3, s1, bw=1000)
    net.addLink(h4, s2, bw=1000)
    net.addLink(h5, s2, bw=1000)
    net.addLink(h6, s2, bw=1000)

    # Switch - Switch link (bottleneck, 100Mbps)
    net.addLink(s1, s2, bw=100)

    info("*** Starting network\n")
    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])

    # QoS 설정 (네트워크 시작 후)
    setup_qos("s1-eth4")

    info("*** Network started\n")
    info("\n")
    info("Slice A (High Priority) : H1 (10.0.0.1) <-> H4 (10.0.0.4)\n")
    info("Slice B (Medium Priority): H2 (10.0.0.2) <-> H5 (10.0.0.5)\n")
    info("Slice C (Best Effort)   : H3 (10.0.0.3) <-> H6 (10.0.0.6)\n")
    info("\n")

    info("*** Running CLI\n")
    CLI(net)

    info("*** Stopping network\n")
    net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    create_topology()