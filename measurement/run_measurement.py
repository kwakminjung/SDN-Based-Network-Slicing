#!/usr/bin/env python3
"""
SDN Network Slicing - Automated Measurement Script
EC5209 Advanced Computer Networking, Spring 2026

사용법:
  sudo python3 topology.py --measure

측정 항목:
  - 처리량 (throughput): iperf3 TCP (3개 슬라이스 동시 측정)
  - 지터 (jitter): iperf3 UDP (3개 슬라이스 동시 측정)
  - 지연 (latency): ping RTT
  - 패킷 손실률 (loss): ping

결과 저장:
  results/slice_a.json, results/slice_b.json, results/slice_c.json
  results/summary.json
"""

import json
import os
import time
import threading


RESULTS_DIR = "results"
DURATION = 10  # 측정 시간 (초)


def ensure_results_dir():
    os.makedirs(RESULTS_DIR, exist_ok=True)


def measure_latency(src, dst_ip, count=20):
    """ping으로 RTT 및 손실률 측정"""
    result = src.cmd(f"ping -c {count} -i 0.2 {dst_ip}")
    lines = result.strip().split('\n')

    loss_line = [l for l in lines if 'packet loss' in l]
    rtt_line  = [l for l in lines if 'rtt min' in l]

    loss = 0.0
    rtt  = {"min": 0, "avg": 0, "max": 0, "mdev": 0}

    if loss_line:
        try:
            loss = float(loss_line[0].split('%')[0].split()[-1])
        except:
            pass

    if rtt_line:
        try:
            vals = rtt_line[0].split('=')[1].strip().split('/')
            rtt = {
                "min":  float(vals[0]),
                "avg":  float(vals[1]),
                "max":  float(vals[2]),
                "mdev": float(vals[3].split()[0])
            }
        except:
            pass

    return {"loss_percent": loss, "rtt_ms": rtt}



def measure_latency_concurrent(slices, count=20):
    """3개 슬라이스 동시 ping 측정"""
    results = {}
    threads = []

    def _measure(name, src, dst_ip):
        results[name] = measure_latency(src, dst_ip, count)

    for s in slices:
        t = threading.Thread(
            target=_measure,
            args=(s['name'], s['src'], s['dst_ip']))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results

def measure_throughput_concurrent(slices, duration=DURATION):
    """3개 슬라이스 동시 TCP 처리량 측정"""
    results = {}
    threads = []

    def _measure(name, src, dst_ip):
        result = src.cmd(f"iperf3 -c {dst_ip} -t {duration} -J 2>/dev/null")
        try:
            data = json.loads(result)
            bps = data['end']['sum_received']['bits_per_second']
            results[name] = round(bps / 1e6, 2)
        except:
            results[name] = 0

    for s in slices:
        t = threading.Thread(
            target=_measure,
            args=(s['name'], s['src'], s['dst_ip']))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results


def measure_jitter_concurrent(slices, duration=DURATION):
    """3개 슬라이스 동시 UDP 지터 측정 (보수적 bitrate로 queue 부하 최소화)"""
    results = {}
    threads = []

    def _measure(name, src, dst_ip):
        result = src.cmd(
            f"iperf3 -c {dst_ip} -u -b 5M -t {duration} -J 2>/dev/null")
        try:
            data = json.loads(result)
            udp_data = data['end'].get('sum', {})
            jitter = udp_data.get('jitter_ms', 0)
            loss   = udp_data.get('lost_percent', 0)
            results[name] = {
                "jitter_ms": round(jitter, 3),
                "udp_loss_percent": round(loss, 2)
            }
        except:
            results[name] = {"jitter_ms": 0, "udp_loss_percent": 0}

    for s in slices:
        t = threading.Thread(
            target=_measure,
            args=(s['name'], s['src'], s['dst_ip']))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results


def measure_udp_bw_concurrent(slices, duration=DURATION):
    """3개 슬라이스 동시 UDP 대역폭 측정 — HTB 상한 확인용.
    각 슬라이스가 허용 대역폭을 훨씬 초과하는 속도로 쏟아붓고,
    실제 수신량(HTB가 통과시킨 양)을 계산한다.
    """
    TARGET_BW = {"Slice A": "50M", "Slice B": "50M", "Slice C": "200M"}
    results = {}
    threads = []

    def _measure(name, src, dst_ip):
        bw = TARGET_BW.get(name, "50M")
        result = src.cmd(
            f"iperf3 -c {dst_ip} -u -b {bw} -t {duration} -J 2>/dev/null")
        try:
            data   = json.loads(result)
            s      = data['end']['sum']
            sent_mbps = s['bits_per_second'] / 1e6
            loss_pct  = s.get('lost_percent', 0)
            received_mbps = round(sent_mbps * (1 - loss_pct / 100), 2)
            results[name] = {
                "sent_mbps":     round(sent_mbps, 2),
                "received_mbps": received_mbps,
                "loss_percent":  round(loss_pct, 2),
            }
        except:
            results[name] = {"sent_mbps": 0, "received_mbps": 0, "loss_percent": 0}

    for s in slices:
        t = threading.Thread(
            target=_measure,
            args=(s['name'], s['src'], s['dst_ip']))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    return results


def start_servers(hosts):
    """iperf3 서버 시작 및 대기"""
    for host in hosts:
        host.cmd("pkill -f iperf3 2>/dev/null")
    time.sleep(1)
    for host in hosts:
        host.cmd("iperf3 -s -D --forceflush")
    time.sleep(2)


def run_measurement(net):
    """전체 측정 실행"""
    ensure_results_dir()

    h1 = net.get('h1')
    h2 = net.get('h2')
    h3 = net.get('h3')
    h4 = net.get('h4')
    h5 = net.get('h5')
    h6 = net.get('h6')

    slices = [
        {"name": "Slice A", "src": h1, "dst": h4, "dst_ip": "10.0.0.4",
         "policy": {"bandwidth": "10Mbps guaranteed", "delay": "10ms", "loss": "0%"}},
        {"name": "Slice B", "src": h2, "dst": h5, "dst_ip": "10.0.0.5",
         "policy": {"bandwidth": "5Mbps cap", "delay": "50ms", "loss": "1%"}},
        {"name": "Slice C", "src": h3, "dst": h6, "dst_ip": "10.0.0.6",
         "policy": {"bandwidth": "best effort", "delay": "100ms", "loss": "5%"}},
    ]

    print("\n" + "="*60)
    print("SDN Network Slicing — Automated Measurement")
    print("="*60)

    # 서버 시작
    print("\n[1/4] Starting iperf3 servers...")
    start_servers([h4, h5, h6])
    print("      Servers ready.")

    # 지연/손실 순차 측정 (동시 측정 시 netem loss 간섭 방지)
    print("\n[2/4] Measuring latency & loss (ping, sequential)...")
    latency_results = {}
    for s in slices:
        print(f"      {s['name']}...")
        latency_results[s['name']] = measure_latency(s['src'], s['dst_ip'])

    # 처리량 동시 측정
    print("\n[3/4] Measuring throughput (iperf3 TCP, concurrent)...")
    throughput_results = measure_throughput_concurrent(slices)
    print("      Done.")

    # 지터 동시 측정
    print("\n[4/4] Measuring jitter (iperf3 UDP 5Mbps, concurrent)...")
    jitter_results = measure_jitter_concurrent(slices)
    print("      Done.")

    # 서버 재시작 후 UDP 대역폭 측정
    print("\n[5/5] Measuring UDP bandwidth (HTB isolation test, concurrent)...")
    start_servers([h4, h5, h6])
    udp_bw_results = measure_udp_bw_concurrent(slices)
    print("      Done.")

    # 서버 종료
    for host in [h4, h5, h6]:
        host.cmd("pkill -f iperf3 2>/dev/null")

    # 결과 합치기
    results = []
    for s in slices:
        name = s['name']
        result = {
            "slice":   name,
            "src":     s['src'].name,
            "dst":     s['dst'].name,
            "policy":  s['policy'],
            "measured": {
                "tcp_throughput_mbps":  throughput_results.get(name, 0),
                "udp_received_mbps":    udp_bw_results[name]['received_mbps'],
                "udp_sent_mbps":        udp_bw_results[name]['sent_mbps'],
                "loss_percent":         latency_results[name]['loss_percent'],
                "rtt_ms":               latency_results[name]['rtt_ms'],
                "jitter_ms":            jitter_results[name]['jitter_ms'],
                "udp_loss_percent":     jitter_results[name]['udp_loss_percent'],
            }
        }
        results.append(result)

        fname = f"{RESULTS_DIR}/slice_{name[-1].lower()}.json"
        with open(fname, 'w') as f:
            json.dump(result, f, indent=2)

    # 요약 저장
    summary = {
        "timestamp":    time.strftime("%Y-%m-%d %H:%M:%S"),
        "duration_sec": DURATION,
        "results":      results
    }
    with open(f"{RESULTS_DIR}/summary.json", 'w') as f:
        json.dump(summary, f, indent=2)

    # 출력
    print("\n" + "="*70)
    print("SDN Network Slicing — Measurement Summary")
    print("="*70)
    print(f"{'Slice':<10} {'TCP(Mbps)':>10} {'UDP rx(Mbps)':>13} {'RTT(ms)':>9} {'Jitter(ms)':>11} {'Loss%':>7}")
    print("-"*70)
    for r in results:
        m = r['measured']
        print(f"{r['slice']:<10} "
              f"{m['tcp_throughput_mbps']:>10} "
              f"{m['udp_received_mbps']:>13} "
              f"{m['rtt_ms']['avg']:>9} "
              f"{m['jitter_ms']:>11} "
              f"{m['loss_percent']:>7}")
    print("="*70)
    print(f"\nResults saved to ./{RESULTS_DIR}/")