# SDN-Based Network Slicing — Smart City SFC

**EC5209 Advanced Computer Networking, Spring 2026 — GIST EECS**  
Minjung Kwak (20261053), Dept. of AI Convergence

Mininet + os-ken(OpenFlow 1.3) + OVS HTB 큐로 스마트 시티 5G 스타일 네트워크 슬라이싱을 구현합니다.  
**슬라이스마다 다른 NFV 체인(SFC)을 물리적으로 경유**하는 것이 핵심입니다. 지연 차이는 netem 주입이 아닌 경유 홉 수 차이에서 자연 발생합니다.  
클라이언트는 슬라이스를 지정하지 않으며, hostname을 보고 Gemma3 에이전트가 자동으로 배정합니다.

---

## 토폴로지

```
클라이언트 (S1)          엣지 클라우드 (S_edge)           코어 클라우드 (S_core)
vehicle_01 (10.0.0.1) ─┐  ┌─ nfv_fw   (10.1.0.1)        ┌─ AutoDrive Hub  (10.0.0.4)
camera_01  (10.0.0.2) ─┤──┤  nfv_cache (10.1.0.2)   ──   ├─ EntertainPort  (10.0.0.5)
sensor_01  (10.0.0.3) ─┘  └─ nfv_aggr  (10.1.0.3)        └─ CityPulse Hub  (10.0.0.6)
+ 동적 클라이언트 (10.0.1.x)
```

- **S1–S_edge, S_edge–S_core**: 100 Mbps
- **호스트–스위치**: 1 Gbps
- **OpenFlow**: 1.3

---

## 슬라이스별 SFC 체인

슬라이스마다 경유하는 NFV 체인이 다릅니다. 트래픽이 **실제로** 해당 노드를 물리적으로 통과합니다.

```
URLLC: S1 → S_edge → [nfv_fw]                    → S_core → AutoDrive Hub
eMBB:  S1 → S_edge → [nfv_fw] → [nfv_cache]      → S_core → EntertainPort
mMTC:  S1 → S_edge → [nfv_fw] → [nfv_aggr]       → S_core → CityPulse Hub
```

| NFV | 역할 | 경유 슬라이스 |
|-----|------|------------|
| `nfv_fw` | 방화벽 (경유 + 로그) | URLLC / eMBB / mMTC 공통 |
| `nfv_cache` | 콘텐츠 캐시 (경유 + 로그) | eMBB 전용 |
| `nfv_aggr` | 데이터 집계 (경유 + 로그) | mMTC 전용 |

> NFV는 실제 기능 없이 패킷을 수신·로깅·재전송합니다. 중요한 것은 트래픽이 그 노드를 물리적으로 경유한다는 사실입니다.

---

## SLA 조건 (3GPP TS 23.501 기준)

### SLA 조건

| 슬라이스 | 서버 | GBR | MBR | PDB | PER | 사용 사례 |
|---------|------|-----|-----|-----|-----|---------|
| **URLLC** | AutoDrive Hub | 10 Mbps | 10 Mbps | 1 ms | 10⁻⁵ | 자율주행, V2X |
| **eMBB** | EntertainPort | 20 Mbps | 50 Mbps | 100 ms | 10⁻⁶ | HD 스트리밍, CCTV |
| **mMTC** | CityPulse Hub | 1 Mbps | 10 Mbps | 300 ms | 10⁻² | IoT 센서, 스마트미터 |

HTB 큐는 GBR/MBR 보장 용도로만 사용합니다. netem은 사용하지 않습니다.

---

## hostname 자동 분류 규칙

클라이언트는 슬라이스를 직접 지정하지 않습니다. hostname prefix로 자동 배정됩니다.

| hostname prefix | 슬라이스 | 서버 | Gemma3 호출 |
|----------------|----------|------|------------|
| `vehicle_*`, `car_*`, `v2x_*`, `ambulance_*` | URLLC | AutoDrive Hub | ❌ 규칙 기반 즉시 처리 |
| `camera_*`, `cctv_*`, `stream_*`, `cam_*` | eMBB | EntertainPort | ❌ 규칙 기반 즉시 처리 |
| `sensor_*`, `iot_*`, `meter_*`, `light_*` | mMTC | CityPulse Hub | ❌ 규칙 기반 즉시 처리 |
| (그 외: `device_*`, `unknown_*` 등) | mMTC 기본값 | CityPulse Hub | ✅ Gemma3 판단 |

---

## 기술 스택

| 레이어 | 도구 |
|--------|------|
| 네트워크 에뮬레이터 | Mininet 2.3.0 |
| SDN 컨트롤러 | os-ken 2.0.0 (OpenFlow 1.3) |
| Python | 3.10.14 (pyenv virtualenv: `sdn-env`) |
| 데이터 플레인 / QoS | Open vSwitch 3.3.4 — HTB 큐 (netem 없음) |
| NFV | Python + scapy (promiscuous 수신 + 재전송) |
| AI 에이전트 | Gemma3 via Ollama (모호한 hostname + SLA 위반 시만 개입) |
| 측정 | iperf3 (처리량), ping (RTT) |
| 대시보드 | Python rich (SFC 경로 실시간 표시) |

> **os-ken 선택 이유**: Ryu는 Ubuntu 24.04/Python 3.10+ 환경에서 eventlet 의존성 충돌. os-ken은 동일한 OpenFlow API + Python 3.10 완전 지원.

---

## 프로젝트 구조

```
SDN-Based-Network-Slicing/
├── config.py                    # SFC 체인 정의, 슬라이스 정책, hostname 분류 규칙
├── topology.py                  # 3스위치 토폴로지 (S1 + S_edge + S_core + NFV 호스트)
├── nfv/
│   ├── nfv_base.py              # NFV 공통 로직 (scapy promiscuous + 로그 + 재전송)
│   ├── nfv_fw.py                # 방화벽 NFV
│   ├── nfv_cache.py             # 캐시 NFV (eMBB 전용)
│   └── nfv_aggr.py              # 집계 NFV (mMTC 전용)
├── controller/
│   ├── l2_switch.py             # 베이스라인 L2 포워딩
│   └── slice_controller.py      # SFC 컨트롤러 (3스위치 + REST API)
├── agent/
│   └── slicing_agent.py         # Gemma3 에이전트 (모호한 hostname + SLA 재배정)
├── demo/
│   ├── dashboard.py             # 실시간 TUI (SFC 경로 + GBR 달성 현황)
│   └── request_injector.py      # 인터랙티브 요청 터미널
└── measurement/
    └── run_measurement.py       # 측정 스크립트 (GBR/MBR/PDB 검증)
```

---

## 시스템 구조

```
클라이언트 (vehicle_01)
        │ 첫 패킷 → Packet-In
        ▼
┌──────────────────────────────────────────────────────┐
│              slice_controller (os-ken)               │
│                                                      │
│  S1: hostname 조회 → classify_hostname()             │
│    prefix 명확 → 즉시 HTB queue + flow rule 설치     │
│    prefix 모호 → Gemma3 비동기 판단                  │
│                                                      │
│  S_edge: SFC 룰 (in_port + dst_ip → NFV 경유)        │
│    URLLC: in_port=S1 → nfv_fw → s_core               │
│    eMBB:  in_port=S1 → nfv_fw → nfv_cache → s_core   │
│    mMTC:  in_port=S1 → nfv_fw → nfv_aggr  → s_core   │
│                                                      │
│  S_core: dst_ip → 서버 포트 포워딩                    │
└──────────────────────────────────────────────────────┘
        │ OpenFlow FlowMod
        ▼
┌─────────────────────────────────────────────────────┐
│  OVS 스위치                                          │
│  S1:     HTB queue (GBR/MBR 강제)                   │
│  S_edge: SFC 라우팅 (경유 홉으로 지연 자연 발생)      │
│  S_core:  서버 포워딩                                 │
└──────┬──────────────────────────────────────────────┘
       │
       ├─ [nfv_fw]    ← 모든 슬라이스 경유 (scapy 수신 + 로그 + 재전송)
       ├─ [nfv_cache] ← eMBB만 경유
       └─ [nfv_aggr]  ← mMTC만 경유
```

---

## SFC 구현 방식

### S_edge 플로우 룰

S_edge는 `in_port + dst_ip` 조합으로 패킷을 SFC 체인 순서대로 라우팅합니다.

```
# URLLC (dst=10.0.0.4)
in_port=S1_port,    dst=10.0.0.4 → output(nfv_fw_port)
in_port=nfv_fw_port, dst=10.0.0.4 → output(s_core_port)

# eMBB (dst=10.0.0.5)
in_port=S1_port,      dst=10.0.0.5 → output(nfv_fw_port)
in_port=nfv_fw_port,  dst=10.0.0.5 → output(nfv_cache_port)
in_port=nfv_cache_port, dst=10.0.0.5 → output(s_core_port)

# mMTC (dst=10.0.0.6)
in_port=S1_port,      dst=10.0.0.6 → output(nfv_fw_port)
in_port=nfv_fw_port,  dst=10.0.0.6 → output(nfv_aggr_port)
in_port=nfv_aggr_port, dst=10.0.0.6 → output(s_core_port)
```

NFV 스크립트는 패킷을 수신한 뒤 **동일 인터페이스로 재전송**합니다. S_edge는 `in_port=nfv_포트`를 보고 다음 홉으로 라우팅합니다.

### HTB 큐 (S1 → S_edge)

```
HTB root (100 Mbps)
├── Queue 0 / class 1:1 → URLLC (GBR=MBR=10 Mbps)
├── Queue 1 / class 1:2 → eMBB  (GBR=20 Mbps, MBR=50 Mbps)
└── Queue 2 / class 1:3 → mMTC  (GBR=1 Mbps,  MBR=10 Mbps)
```

netem 없음. 지연은 경유 홉 수에 따라 자연 발생합니다.

### 플로우 우선순위 테이블

| Priority | 스위치 | Match | Action | 역할 |
|----------|--------|-------|--------|------|
| 10 | S1 | `in_port + IP + src + dst` | `set_queue(n) + output(S_edge)` | HTB 큐 배정 |
| 10 | S_edge | `in_port + IP + dst` | `output(next_hop)` | SFC 경유 라우팅 |
| 10 | S_core | `IP + dst` | `output(server_port)` | 서버 포워딩 |
| 1 | 전체 | `in_port + eth_dst` | `output(port)` | L2 포워딩 |
| 0 | 전체 | (any) | `→ controller` | table-miss |

---

## Gemma3 에이전트

### 개입 조건

| 상황 | 처리 방식 |
|------|---------|
| hostname prefix 명확 (`vehicle_*` 등) | 규칙 기반 즉시 처리 — Gemma3 **미호출** |
| hostname prefix 모호 (`device_01` 등) | Gemma3 비동기 판단 → 결과 다르면 flow rule 재설치 |
| SLA(GBR) 위반 감지 | Gemma3에게 재배정 방안 질의 |
| `/slices/request` 명시적 요청 | Gemma3가 현재 부하 기반으로 최적 슬라이스 결정 |

### REST API (포트 8080)

| Method | Path | 설명 |
|--------|------|------|
| GET | `/slices` | 슬라이스 상태 + SFC 체인 + 연결 현황 |
| POST | `/slices/reassign` | 직접 재배정 (즉시 적용) |
| POST | `/slices/request` | Gemma3 부하 기반 최적 배정 |
| POST | `/clients/register` | hostname 등록 (topology.py 자동 호출) |

---

## 실행 방법

### 사전 준비

```bash
pyenv activate sdn-env

# scapy (NFV 스크립트 의존성)
pip install scapy

# Ollama + Gemma3 (에이전트 사용 시)
ollama serve &
ollama pull gemma3
```

### 1. 컨트롤러 (Terminal 1, 비root)

```bash
python -m os_ken.cmd.manager controller.slice_controller
```

정상 시작 로그:
```
S1 rule: vehicle_01 (10.0.0.1) queue=0 → [nfv_fw] → AutoDrive Hub
S1 rule: camera_01  (10.0.0.2) queue=1 → [nfv_fw → nfv_cache] → EntertainPort
S1 rule: sensor_01  (10.0.0.3) queue=2 → [nfv_fw → nfv_aggr] → CityPulse Hub
SEDGE SFC rules installed
REST API listening on port 8080
```

### 2. 토폴로지 (Terminal 2, root 필요)

```bash
sudo python3 topology.py          # Mininet CLI 모드
sudo python3 topology.py --measure  # 자동 측정 후 종료
```

NFV 스크립트는 토폴로지 시작 시 자동으로 실행됩니다.

### 3. NFV 로그 확인

```bash
tail -f /tmp/nfv_fw.log    # 방화벽 경유 로그
tail -f /tmp/nfv_cache.log # 캐시 경유 로그 (eMBB만)
tail -f /tmp/nfv_aggr.log  # 집계 경유 로그 (mMTC만)
```

### 4. 에이전트 (Terminal 3)

```bash
python agent/slicing_agent.py           # 10초 주기 SLA 감시
python agent/slicing_agent.py --dry-run # 분석만
```

### 5. 데모 UI (Terminal 4)

```bash
python demo/dashboard.py
```

대시보드 출력 예시:
```
🚗 vehicle_01  URLLC  [nfv_fw]                  → AutoDrive Hub
📺 camera_01   eMBB   [nfv_fw] → [nfv_cache]    → EntertainPort
🏙️ sensor_01   mMTC   [nfv_fw] → [nfv_aggr]     → CityPulse Hub
```

### 6. 동적 클라이언트 추가 (Mininet CLI)

```python
# hostname prefix로 슬라이스 자동 배정
py vehicle_02 = add_client(net, 'vehicle_02', s1)
py camera_02  = add_client(net, 'camera_02',  s1)

# 모호한 hostname → Gemma3가 패킷 패턴 분석 후 판단
py device_01  = add_client(net, 'device_01',  s1)
```

### 7. 상태 검증

```bash
sudo ovs-ofctl dump-flows s1      # S1 HTB 큐 배정 룰
sudo ovs-ofctl dump-flows sedge   # S_edge SFC 라우팅 룰
sudo ovs-ofctl dump-flows s_core   # S_core 서버 포워딩 룰
tc -s class show dev s1-eth4      # HTB 큐 통계
curl localhost:8080/slices        # 슬라이스 상태 + SFC 체인 + 연결 현황
```

---

## 주요 구현 이슈 및 해결

### NFV 재수신 루프 방지

scapy로 패킷을 재전송할 때 동일 인터페이스에서 자신이 보낸 패킷을 다시 sniff하는 루프 문제.  
**해결**: `filter="ip and not ether src {own_mac}"` — 자신의 MAC이 eth_src인 패킷은 무시.

### `os_ken.app.wsgi` 모듈 없음

os-ken 2.0.0에 Ryu의 `wsgi` 모듈 미포함. `eventlet.wsgi` + `hub.spawn()`으로 대체.

### OVS `other-config:default-queue` 무시됨

OVS는 HTB default를 항상 Queue 0으로 설정. 명시적 OpenFlow 룰로 모든 트래픽을 분류하므로 영향 없음.

---

## 브랜치 구성

| 브랜치 | 내용 |
|--------|------|
| `main` | 기초 구현 (S1–S2, HTB + netem, 정적 슬라이스) |
| `feature/agent` | Gemma3 에이전트 + 요청 기반 배정 + 데모 UI |
| `feature/sfc` | **SFC 기반 재설계** — NFV 체인 경유 + 동적 hostname 분류 |
