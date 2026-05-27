# SDN-Based Network Slicing

**EC5209 Advanced Computer Networking, Spring 2026 — GIST EECS**  
Minjung Kwak (20261053), Dept. of AI Convergence

SDN 기반 네트워크 슬라이싱 구현 및 성능 평가 프로젝트.  
Mininet + os-ken(OpenFlow 1.3) + OVS HTB 큐를 사용해 공유 물리 토폴로지 위에서 3개 슬라이스의 QoS를 동시에 강제합니다.

---

## 토폴로지

```
H1 (Slice A, 10.0.0.1) ─┐                       ┌─ H4 (Slice A, 10.0.0.4)
H2 (Slice B, 10.0.0.2) ─┤── S1 ──(100Mbps)── S2 ─┤─ H5 (Slice B, 10.0.0.5)
H3 (Slice C, 10.0.0.3) ─┘                       └─ H6 (Slice C, 10.0.0.6)
```

- **S1–S2 링크**: 100 Mbps (병목 / QoS 측정 지점)
- **호스트–스위치 링크**: 1 Gbps
- **OpenFlow**: 1.3

---

## 슬라이스 정책

| 슬라이스 | 호스트 | 대역폭 | 지연 | 지터 | 손실 |
|---------|--------|--------|------|------|------|
| **Slice A** (High Priority) | H1 ↔ H4 | 10 Mbps 보장 | 10 ms | 1 ms | 0 % |
| **Slice B** (Medium Priority) | H2 ↔ H5 | 5 Mbps 상한 | 50 ms | 10 ms | 1 % |
| **Slice C** (Best Effort) | H3 ↔ H6 | 나머지 대역폭 | 100 ms | 20 ms | 5 % |

---

## 기술 스택

| 레이어 | 도구 |
|--------|------|
| 네트워크 에뮬레이터 | Mininet 2.3.0 |
| SDN 컨트롤러 | os-ken 2.0.0 (OpenFlow 1.3) |
| Python | 3.10.14 (pyenv virtualenv: `sdn-env`) |
| 데이터 플레인 / QoS | Open vSwitch 3.3.4 — HTB 큐 + tc netem |
| 측정 | iperf3 (처리량·지터), ping (RTT) |

> **os-ken 선택 이유**: Ryu는 Ubuntu 24.04/Python 3.10+ 환경에서 eventlet 의존성 충돌로 동작 불가. os-ken은 OpenStack 커뮤니티가 유지하는 Ryu 포크로 동일한 OpenFlow API를 제공하며 Python 3.10을 완전 지원.

---

## 프로젝트 구조

```
SDN-Based-Network-Slicing/
├── topology.py                 # Mininet 토폴로지 + OVS HTB/netem 설정
├── controller/
│   ├── l2_switch.py            # 베이스라인 L2 포워딩 컨트롤러 (MAC 학습)
│   └── slice_controller.py     # 슬라이스 컨트롤러 (OpenFlow QoS 분류)
├── measurement/
│   └── run_measurement.py      # 자동 측정 스크립트 (처리량·RTT·지터·손실)
└── results/
    ├── slice_a.json
    ├── slice_b.json
    ├── slice_c.json
    └── summary.json
```

---

## QoS 구현 방식

### 1. OVS HTB 큐 (`topology.py`)

`s1-eth4`(S1→S2 병목 포트)에 3-큐 HTB를 설정합니다.

```
HTB root (100 Mbps)
├── Queue 0 / class 1:1 → Slice A (min 10 Mbps, max 10 Mbps)
│   └── netem: delay 10ms 1ms
├── Queue 1 / class 1:2 → Slice B (min 1 Mbps,  max 5 Mbps)
│   └── netem: delay 50ms 10ms loss 1%
└── Queue 2 / class 1:3 → Slice C (min 1 Mbps,  max 100 Mbps)
    └── netem: delay 100ms 20ms loss 5%
```

netem은 HTB 클래스의 자식 qdisc로 연결되어 HTB 스케줄링 후 지연·손실을 부여합니다.

### 2. 슬라이스 컨트롤러 (`controller/slice_controller.py`)

**핵심 설계**: 스위치 연결 시점에 Slice 규칙을 선제 설치합니다.

```python
S1_SLICE_RULES = [
    (in_port=1, src='10.0.0.1', dst='10.0.0.4', queue=0),  # Slice A
    (in_port=2, src='10.0.0.2', dst='10.0.0.5', queue=1),  # Slice B
    (in_port=3, src='10.0.0.3', dst='10.0.0.6', queue=2),  # Slice C
]
# match: (in_port, eth_type=IP, ipv4_src, ipv4_dst)
# action: set_queue(n) → output(BOTTLENECK_PORT)
# priority=10, idle_timeout=0
```

나머지 트래픽(ARP, 리턴 방향)은 MAC 학습 기반 L2 포워딩으로 처리합니다.

### 3. 플로우 우선순위 테이블

| Priority | Match | Action | 역할 |
|----------|-------|--------|------|
| 10 | `in_port + IP + src + dst` | `set_queue(n) + output(4)` | 슬라이스 분류 |
| 1 | `in_port + eth_dst` | `output(port)` | L2 포워딩 |
| 0 | (any) | `→ controller` | table-miss |

---

## 실행 방법

### 사전 준비

```bash
pyenv activate sdn-env
```

### 1. 컨트롤러 실행 (Terminal 1, 비root)

```bash
# 베이스라인 L2 포워딩
python -m os_ken.cmd.manager controller.l2_switch

# 슬라이스 컨트롤러 (QoS)
python -m os_ken.cmd.manager controller.slice_controller
```

컨트롤러 시작 시 아래 로그가 찍히면 정상:
```
Pre-installed: port1 10.0.0.1→10.0.0.4 queue=0 (Slice A (High))
Pre-installed: port2 10.0.0.2→10.0.0.5 queue=1 (Slice B (Medium))
Pre-installed: port3 10.0.0.3→10.0.0.6 queue=2 (Slice C (Best Effort))
Switch 1 connected
Switch 2 connected
```

### 2. 토폴로지 실행 (Terminal 2, root 필요)

```bash
# Mininet CLI 모드
sudo python3 topology.py

# 자동 측정 모드
sudo python3 topology.py --measure
```

### 3. 상태 검증

```bash
# 플로우 룰 확인 (root 또는 Mininet CLI 내부에서)
sudo ovs-ofctl dump-flows s1

# HTB 큐 트래픽 분배 확인
tc -s class show dev s1-eth4

# QoS 설정 확인
sudo ovs-vsctl list qos
```

---

## 측정 결과

`sudo python3 topology.py --measure` 실행 결과 (2026-05-27):

| 슬라이스 | UDP 수신량 | TCP 처리량 | RTT avg | 지터 | 손실률 |
|---------|-----------|-----------|---------|------|--------|
| Slice A | **10.87 Mbps** ✅ | 9.54 Mbps | 10.3 ms ✅ | 0.65 ms | 0.0 % ✅ |
| Slice B | **6.00 Mbps** ✅ | 1.46 Mbps | 49.4 ms ✅ | 5.95 ms | 0.0 % |
| Slice C | **83.02 Mbps** ✅ | 0.52 Mbps | 100.5 ms ✅ | 12.6 ms | 5.0 % ✅ |

> **TCP 처리량이 낮은 이유**: TCP는 패킷 손실과 지연에 민감합니다.  
> Slice B (1% loss + 50ms RTT), Slice C (5% loss + 100ms RTT) 조건에서 TCP 이론 한계는 각각 ~1–2 Mbps, ~0.3 Mbps입니다.  
> **대역폭 격리 효과는 UDP 측정값으로 확인**해야 합니다.

---

## 구현 과정의 주요 문제 및 해결

### 문제: priority-2 catch-all이 packet_in을 차단

초기 구현에서 `switch_features_handler`가 `(in_port, IP) → queue 2` 규칙을 `idle_timeout=0`으로 설치했습니다. 이 규칙이 모든 IP 패킷을 가로채 컨트롤러의 `packet_in_handler`가 호출되지 않았고, 결과적으로 slice별 priority-10 규칙이 영구적으로 설치되지 않는 버그가 발생했습니다.

**증상**: 모든 트래픽이 HTB default 큐(class 1:1 = Slice A)로 몰림

**해결**: 스위치 연결 시점에 모든 slice 쌍에 대한 priority-10 규칙을 선제 설치. catch-all 제거.

### 주의: OVS `other-config:default-queue` 무시됨

OVS는 `other-config:default-queue=2` 설정을 무시하고 HTB root의 default를 항상 `0x1`(class 1:1 = Queue 0)로 설정합니다. 명시적 OpenFlow 규칙으로 모든 slice 트래픽을 분류하므로 측정에 영향 없음.

---

## 향후 계획

- [ ] Slice B 1% loss ping 측정 정밀도 개선 (패킷 수 증가)
- [ ] 격리 검증: Slice B/C 포화 시 Slice A 처리량 안정성 확인
- [ ] 결과 시각화 (`matplotlib` 그래프)
- [ ] Gemma4 기반 자율 슬라이싱 에이전트 (stretch goal)
