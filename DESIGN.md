# SDN Network Slicing — Service-Aware Dynamic Slicing 설계 문서

## 현재 구현의 한계

현재 구현은 **정적 슬라이싱**이다. IP 주소로 호스트를 슬라이스에 고정 배정하고,
호스트가 어떤 서비스를 쓰는지 전혀 고려하지 않는다.

```
현재:
  H1 → 항상 Slice A  (H1이 뭘 하든 상관없이)
  H2 → 항상 Slice B
  H3 → 항상 Slice C
```

실제 5G 네트워크 슬라이싱은 **서비스 요구사항 기반 동적 할당**이다:

```
목표:
  H1이 "URLLC 서비스 필요" 요청
      ↓
  컨트롤러: "URLLC 슬라이스 여유 있음 → H1 할당"
      ↓
  H1이 "이제 파일 다운로드로 바꿀게" 요청
      ↓
  컨트롤러: "eMBB 슬라이스로 재할당"
```

---

## 목표 아키텍처

### 서비스 타입 정의 (3GPP 기반)

```python
SERVICES = {
    "urllc": {
        "description": "Ultra-Reliable Low Latency (자율주행, 원격제어)",
        "min_bw_mbps": 10,
        "max_delay_ms": 10,
        "max_jitter_ms": 1,
        "max_loss_pct": 0,
    },
    "embb": {
        "description": "Enhanced Mobile Broadband (스트리밍, 화상회의)",
        "min_bw_mbps": 5,
        "max_delay_ms": 50,
        "max_jitter_ms": 10,
        "max_loss_pct": 1,
    },
    "mmtc": {
        "description": "Massive Machine Type (IoT, 백그라운드 센서)",
        "min_bw_mbps": 1,
        "max_delay_ms": 100,
        "max_jitter_ms": 20,
        "max_loss_pct": 5,
    },
}
```

### 호스트 설정 (서비스 요청 포함)

```python
HOST_PROFILES = {
    "h1": {"service": "urllc", "description": "자율주행 차량"},
    "h2": {"service": "embb",  "description": "영상 스트리밍 클라이언트"},
    "h3": {"service": "mmtc",  "description": "IoT 센서"},
    "h4": {"service": "urllc", "description": "자율주행 제어 서버"},
    "h5": {"service": "embb",  "description": "영상 스트리밍 서버"},
    "h6": {"service": "mmtc",  "description": "IoT 데이터 수집 서버"},
}
```

### 슬라이스는 서비스 타입과 1:1 매핑 (기본)

같은 서비스 타입을 요구하는 호스트들은 같은 슬라이스를 공유한다.
슬라이스가 포화되면 Gemma4 에이전트가 새 슬라이스 생성 또는 QoS 재조정을 판단한다.

```
Slice URLLC: H1 ↔ H4  (urllc 서비스)
Slice eMBB:  H2 ↔ H5  (embb 서비스)
Slice mMTC:  H3 ↔ H6  (mmtc 서비스)
```

---

## 구현할 컴포넌트

### 1. `config.py` (새 파일)
서비스 타입, 호스트 프로파일, 슬라이스 정책을 중앙 관리.

```python
# config.py
SERVICES = { ... }       # 서비스 타입 정의
HOST_PROFILES = { ... }  # 호스트별 서비스 요청
SLICE_POLICIES = { ... } # 슬라이스별 QoS 정책 (SERVICES에서 파생)
```

### 2. `controller/slice_controller.py` (수정)
- 호스트 IP를 보고 `HOST_PROFILES`에서 서비스 타입 조회
- 서비스 타입에 맞는 슬라이스 큐로 플로우 룰 설치
- 슬라이스 할당 상태를 REST API로 노출 (Gemma4 에이전트가 읽음)
- 동적 재할당 지원: 외부에서 슬라이스 변경 명령 수신 가능

### 3. `agent/slicing_agent.py` (새 파일) — Gemma4 에이전트
주기적으로 슬라이스 상태를 측정하고 Gemma4에게 판단을 요청한다.

```
루프 (10초마다):
  1. 현재 슬라이스별 처리량/지연/손실 측정
  2. SLA 위반 감지 (예: Slice URLLC 처리량 < 10Mbps)
  3. Gemma4에게 상태 전달 + 조정 요청
  4. Gemma4 응답 파싱 → os-ken REST API로 큐 변경
  5. 변경 결과 기록
```

#### Gemma4 프롬프트 예시

```
현재 네트워크 슬라이스 상태:

Slice URLLC (목표: 10Mbps, 10ms, 0% 손실)
  현재: 7.2 Mbps, 12ms, 0% 손실
  호스트: h1(자율주행), h4(제어서버)
  상태: ⚠️ 처리량 SLA 위반

Slice eMBB (목표: 5Mbps, 50ms, 1% 손실)
  현재: 8.1 Mbps, 48ms, 0% 손실
  호스트: h2(스트리밍), h5(서버)
  상태: ✅ 정상 (여유 대역폭 있음)

Slice mMTC (목표: 1Mbps, 100ms, 5% 손실)
  현재: 0.4 Mbps, 101ms, 5% 손실
  호스트: h3(IoT), h6(수집서버)
  상태: ✅ 정상

조치를 JSON으로 응답하시오:
{
  "action": "adjust_queue" | "reassign_host" | "no_action",
  "reason": "...",
  "changes": [
    {"slice": "urllc", "min_rate_mbps": 12},
    ...
  ]
}
```

#### Gemma4 연동 방식
- **Ollama 로컬 실행** 사용
- 모델: `gemma4` (ollama pull gemma4 완료)
- Ollama API 엔드포인트: `http://localhost:11434/api/generate`
- API 키 불필요, 인터넷 불필요

```python
import requests

def ask_gemma(prompt: str) -> str:
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": "gemma4", "prompt": prompt, "stream": False}
    )
    return response.json()["response"]
```

### 4. `topology.py` (수정)
- `config.py`에서 호스트/서비스 설정 읽어서 토폴로지 구성
- `--agent` 옵션 추가: 에이전트 루프 자동 시작

---

## 파일 구조 (목표)

```
SDN-Based-Network-Slicing/
├── config.py                      # (NEW) 서비스/호스트/슬라이스 설정
├── topology.py                    # (MODIFY) config.py 기반으로 동작
├── controller/
│   ├── slice_controller.py        # (MODIFY) 서비스 기반 동적 할당
│   └── l2_switch.py               # (유지)
├── agent/
│   └── slicing_agent.py           # (NEW) Gemma4 에이전트
├── measurement/
│   └── run_measurement.py         # (MODIFY) 슬라이스 상태 수집 함수 분리
├── results/
└── CLAUDE.md
```

---

## 동작 시나리오 (데모용)

### 시나리오 1: SLA 위반 자동 복구
```
1. 초기 상태: 3개 슬라이스 정상 운영
2. 부하 주입: Slice eMBB에 과도한 트래픽 (iperf3 -b 100M)
3. 관찰: Slice URLLC 처리량이 10Mbps 밑으로 떨어짐
4. 에이전트 동작:
   - Gemma4: "eMBB 트래픽이 URLLC 방해, eMBB max-rate 3Mbps로 제한"
   - os-ken REST API로 큐 변경
5. 결과: Slice URLLC 처리량 회복
```

### 시나리오 2: 동적 서비스 전환
```
1. H1이 현재 URLLC 서비스 사용 중
2. H1 서비스 요청 변경: "mmtc로 전환" (시뮬레이션)
3. 에이전트 동작:
   - Gemma4: "H1을 URLLC에서 mMTC 슬라이스로 재할당"
   - 플로우 룰 변경: H1 트래픽 → Slice mMTC 큐
4. 결과: H1 RTT 10ms → 100ms (mMTC 정책 적용)
```

---

## 현재 환경

- Ubuntu 24.04, Python 3.10.14 (pyenv sdn-env)
- os-ken 2.0.0, Mininet 2.3.0, OVS 3.3.4
- OpenFlow 1.3
- S1-S2 병목 100Mbps, 호스트-스위치 1Gbps

## 현재 동작 확인된 것

- HTB 큐 3개 (URLLC 10Mbps 보장, eMBB 5Mbps 상한, mMTC 베스트 에포트)
- netem (지연/지터/손실 슬라이스별 차별화)
- 슬라이스 선제 플로우 룰 (switch_features_handler에서 priority=10으로 즉시 설치)
- 자동 측정 스크립트 (topology.py --measure)

## 구현 우선순위

1. `config.py` 작성 (서비스/호스트 정의)
2. `slice_controller.py` 수정 (config 기반으로 동작)
3. `slicing_agent.py` 기본 루프 구현 (측정 → Gemma4 → 조정)
4. 데모 시나리오 1 검증 (SLA 위반 자동 복구)
5. 데모 시나리오 2 검증 (동적 서비스 전환)