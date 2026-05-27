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

QoS policy:
  Slice A: 10Mbps guaranteed, delay 10ms, jitter 1ms,  loss 0%
  Slice B: 5Mbps cap,         delay 50ms, jitter 10ms, loss 1%
  Slice C: best effort,       delay 100ms, jitter 20ms, loss 5%
"""

import subprocess
import sys
import time

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI


def setup_qos(bottleneck_port="s1-eth4"):
    """S1-S2 병목 링크에 HTB QoS 큐 설정"""
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
        f"other-config:default-queue=2 "
        f"queues=0=@q0,1=@q1,2=@q2 "
        f"-- --id=@q0 create Queue "
        f"other-config:min-rate=10000000 other-config:max-rate=10000000 "
        f"-- --id=@q1 create Queue "
        f"other-config:min-rate=1000000 other-config:max-rate=5000000 "
        f"-- --id=@q2 create Queue "
        f"other-config:min-rate=1000000 other-config:max-rate=100000000"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        info("*** QoS setup complete\n")
        info("    Queue 0 (Slice A): 10Mbps guaranteed\n")
        info("    Queue 1 (Slice B): 5Mbps cap\n")
        info("    Queue 2 (Slice C): best effort\n")
    else:
        info(f"*** QoS setup failed: {result.stderr}\n")




def setup_netem(bottleneck_port="s1-eth4"):
    """HTB 클래스 아래에 netem 설정"""
    info("*** Cleaning up existing netem settings\n")
    for handle in ["10:", "20:", "30:"]:
        subprocess.run(
            f"tc qdisc del dev {bottleneck_port} handle {handle} netem",
            shell=True, stderr=subprocess.DEVNULL)

    info(f"*** Setting up netem on {bottleneck_port}\n")
    cmds = [
        f"tc qdisc add dev {bottleneck_port} parent 1:1 handle 10: netem delay 10ms 1ms",
        f"tc qdisc add dev {bottleneck_port} parent 1:2 handle 20: netem delay 50ms 10ms loss 1%",
        f"tc qdisc add dev {bottleneck_port} parent 1:3 handle 30: netem delay 100ms 20ms loss 5%",
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            info(f"*** netem setup warning: {result.stderr}\n")

    info("*** netem setup complete\n")
    info("    Slice A: delay 10ms,  jitter 1ms,  loss 0%\n")
    info("    Slice B: delay 50ms,  jitter 10ms, loss 1%\n")
    info("    Slice C: delay 100ms, jitter 20ms, loss 5%\n")


def build_network():
    """공통 네트워크 구성"""
    net = Mininet(controller=RemoteController,
                  switch=OVSSwitch,
                  link=TCLink,
                  autoSetMacs=True)

    c0 = net.addController("c0", controller=RemoteController,
                            ip="127.0.0.1", port=6633)

    s1 = net.addSwitch("s1", protocols="OpenFlow13")
    s2 = net.addSwitch("s2", protocols="OpenFlow13")

    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h4 = net.addHost("h4", ip="10.0.0.4/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    h5 = net.addHost("h5", ip="10.0.0.5/24")
    h3 = net.addHost("h3", ip="10.0.0.3/24")
    h6 = net.addHost("h6", ip="10.0.0.6/24")

    net.addLink(h1, s1, bw=1000)
    net.addLink(h2, s1, bw=1000)
    net.addLink(h3, s1, bw=1000)
    net.addLink(h4, s2, bw=1000)
    net.addLink(h5, s2, bw=1000)
    net.addLink(h6, s2, bw=1000)
    net.addLink(s1, s2, bw=100)

    net.build()
    c0.start()
    s1.start([c0])
    s2.start([c0])

    setup_qos("s1-eth4")
    setup_netem("s1-eth4")

    return net


def create_topology():
    """CLI 모드"""
    net = build_network()

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


def create_topology_and_measure():
    """측정 모드"""
    from measurement.run_measurement import run_measurement

    net = build_network()

    info("*** Waiting for controller to install flows...\n")
    net.pingAll(timeout=2)
    time.sleep(3)
    # 플로우 룰 재확인
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