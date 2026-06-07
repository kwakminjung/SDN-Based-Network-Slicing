"""
config.py — SDN Network Slicing (SFC 버전) 중앙 설정
EC5209 Advanced Computer Networking, Spring 2026

슬라이스마다 다른 NFV 체인(SFC)을 경유하는 구조.
지연 차이는 netem 주입이 아닌 경유 홉 수 차이에서 자연 발생.

스마트 시티 구성:
  클라이언트: vehicle_* → URLLC, camera_* → eMBB, sensor_* → mMTC
  엣지 NFV:   nfv_fw (공통) / nfv_cache (eMBB) / nfv_aggr (mMTC)
  서버:       AutoDrive Hub / EntertainPort / CityPulse Hub
"""

# ---------------------------------------------------------------------------
# 3GPP 서비스 타입 정의 (TS 23.501 기준)
# GBR: Guaranteed Bit Rate / MBR: Maximum Bit Rate
# PDB: Packet Delay Budget / PER: Packet Error Rate
# queue_id: OVS HTB 큐 ID (0=urllc, 1=embb, 2=mmtc)
# htb_class: tc class 핸들 (1:1, 1:2, 1:3)
# dscp:     IP 헤더 DSCP 마킹 값 (6비트, 0~63)
#             URLLC → 46 (EF,   Expedited Forwarding)
#             eMBB  → 34 (AF41, Assured Forwarding 4-1)
#             mMTC  →  0 (BE,   Best Effort)
# htb_prio: HTB strict-priority 우선순위 (작을수록 우선, 0=최우선)
# ---------------------------------------------------------------------------
SERVICES = {
    "urllc": {
        "description": "Ultra-Reliable Low Latency (자율주행, V2X)",
        "queue_id": 0,
        "htb_class": "1:1",
        "dscp": 46,          # EF
        "htb_prio": 0,       # 최우선
        "gbr_mbps": 10,
        "mbr_mbps": 10,
        "pdb_ms": 1,
        "per": 1e-5,
        "emoji": "🚗",
        "color": "cyan",
    },
    "embb": {
        "description": "Enhanced Mobile Broadband (HD 스트리밍, CCTV)",
        "queue_id": 1,
        "htb_class": "1:2",
        "dscp": 34,          # AF41
        "htb_prio": 1,
        "gbr_mbps": 20,
        "mbr_mbps": 50,
        "pdb_ms": 100,
        "per": 1e-6,
        "emoji": "📺",
        "color": "magenta",
    },
    "mmtc": {
        "description": "Massive Machine Type (IoT 센서, 스마트미터)",
        "queue_id": 2,
        "htb_class": "1:3",
        "dscp": 0,           # BE
        "htb_prio": 2,
        "gbr_mbps": 1,
        "mbr_mbps": 10,
        "pdb_ms": 300,
        "per": 1e-2,
        "emoji": "🏙️",
        "color": "green",
    },
}

# HTB class → service 역방향 조회
CLASS_TO_SERVICE = {svc["htb_class"]: name for name, svc in SERVICES.items()}

# ---------------------------------------------------------------------------
# DSCP 우선순위 사다리 (낮음 → 높음). "priority:high" 요구 시 한 단계 승급.
# mMTC(BE,0) < eMBB(AF41,34) < URLLC(EF,46)
# ---------------------------------------------------------------------------
DSCP_LADDER = [0, 34, 46]


def get_dscp(service: str, high_priority: bool = False) -> int:
    """슬라이스의 DSCP 마킹 값 반환.

    high_priority=True 이면 DSCP 사다리에서 한 단계 위 값으로 승급한다
    (URLLC는 이미 최상위 EF이므로 그대로 유지). Strict-Priority tc filter가
    DSCP 값으로 큐를 고르므로, 승급된 패킷은 한 단계 높은 큐에서 처리된다.
    """
    base = SERVICES[service]["dscp"]
    if not high_priority:
        return base
    if base in DSCP_LADDER:
        i = DSCP_LADDER.index(base)
        return DSCP_LADDER[min(i + 1, len(DSCP_LADDER) - 1)]
    return base


def dscp_to_tos(dscp: int) -> int:
    """DSCP(6비트) → IP ToS 바이트 값 (DSCP를 상위 6비트로 시프트, ECN=0).
    tc u32 'match ip tos' 가 ToS 바이트 전체를 보므로 이 변환이 필요하다.
    예) DSCP 46(EF) → 0xB8, DSCP 34(AF41) → 0x88, DSCP 0(BE) → 0x00
    """
    return (dscp & 0x3F) << 2


def get_dscp_filter_map() -> list[tuple]:
    """topology.py 가 tc filter 를 만들 때 쓰는 (dscp, tos, htb_class, service).
    DSCP 0(BE)은 default-queue 로 떨어지므로 명시 filter 에서 제외할 수 있으나,
    명확성을 위해 함께 반환한다 (우선순위가 가장 낮은 filter prio 로 설치).
    """
    rows = []
    for name, svc in SERVICES.items():
        rows.append((svc["dscp"], dscp_to_tos(svc["dscp"]),
                     svc["htb_class"], name))
    # DSCP 큰 값(높은 우선순위)부터 filter prio 1, 2, 3 ...
    return sorted(rows, key=lambda r: -r[0])

# ---------------------------------------------------------------------------
# 서버 정의 (S_core 스위치에 연결)
# ---------------------------------------------------------------------------
SERVERS = {
    "autodrive": {
        "name": "AutoDrive Hub",
        "ip": "10.0.0.4",
        "service": "urllc",
        "s_core_port": 2,
        "description": "자율주행 / V2X 제어 서버",
    },
    "ent_port": {
        "name": "EntertainPort",
        "ip": "10.0.0.5",
        "service": "embb",
        "s_core_port": 3,
        "description": "HD 스트리밍 / CCTV 수신 서버",
    },
    "citypulse": {
        "name": "CityPulse Hub",
        "ip": "10.0.0.6",
        "service": "mmtc",
        "s_core_port": 4,
        "description": "IoT 센서 / 스마트미터 데이터 수집 서버",
    },
}

SERVER_IPS = {s["ip"] for s in SERVERS.values()}
SERVICE_TO_SERVER = {s["service"]: s for s in SERVERS.values()}

# ---------------------------------------------------------------------------
# NFV 호스트 정의 (S_edge 스위치에 연결)
# ---------------------------------------------------------------------------
NFV_HOSTS = {
    "nfv_fw": {
        "ip": "10.1.0.1",
        "sedge_port": 2,
        "description": "방화벽 (모든 슬라이스 공통 경유)",
        "script": "nfv/nfv_fw.py",
    },
    "nfv_cache": {
        "ip": "10.1.0.2",
        "sedge_port": 3,
        "description": "콘텐츠 캐시 (eMBB 전용)",
        "script": "nfv/nfv_cache.py",
    },
    "nfv_aggr": {
        "ip": "10.1.0.3",
        "sedge_port": 4,
        "description": "데이터 집계 (mMTC 전용)",
        "script": "nfv/nfv_aggr.py",
    },
}

# ---------------------------------------------------------------------------
# SFC 체인 정의 (슬라이스 → 경유할 NFV 이름 목록)
# ---------------------------------------------------------------------------
SFC_CHAINS = {
    "urllc": ["nfv_fw"],
    "embb":  ["nfv_fw", "nfv_cache"],
    "mmtc":  ["nfv_fw", "nfv_aggr"],
}

# ---------------------------------------------------------------------------
# 스위치 DPID
# ---------------------------------------------------------------------------
DPID_S1    = 1   # 액세스 스위치 (클라이언트)
DPID_SEDGE = 2   # 엣지 스위치 (NFV 호스트들)
DPID_SCORE = 3   # 코어 스위치 (서버들)

# ---------------------------------------------------------------------------
# 포트 번호 (topology.py addLink 순서 기준)
#
# S1 포트: vehicle_01=1, camera_01=2, sensor_01=3, sedge=4
# S_edge 포트: s1=1, nfv_fw=2, nfv_cache=3, nfv_aggr=4, s_core=5
# S_core 포트: sedge=1, autodrive=2, ent_port=3, citypulse=4
# ---------------------------------------------------------------------------
S1_PORT_SEDGE     = 4   # S1 → S_edge
SEDGE_PORT_S1     = 1   # S_edge → S1
SEDGE_PORT_NFW    = 2   # S_edge → nfv_fw
SEDGE_PORT_NCACHE = 3   # S_edge → nfv_cache
SEDGE_PORT_NAGGR  = 4   # S_edge → nfv_aggr
SEDGE_PORT_SCORE  = 5   # S_edge → S_core
SCORE_PORT_SEDGE  = 1   # S_core → S_edge

# NFV 이름 → S_edge 포트 매핑
NFV_TO_SEDGE_PORT = {
    "nfv_fw":    SEDGE_PORT_NFW,
    "nfv_cache": SEDGE_PORT_NCACHE,
    "nfv_aggr":  SEDGE_PORT_NAGGR,
}

# ---------------------------------------------------------------------------
# 데모용 정적 클라이언트 프로파일 (S1에 연결)
# S1 포트: topology.py addLink 순서 (vehicle_01=1, camera_01=2, sensor_01=3)
# ---------------------------------------------------------------------------
HOST_PROFILES = {
    "vehicle_01": {
        "ip": "10.0.0.1",
        "s1_port": 1,
        "service": "urllc",
        "description": "자율주행 차량 #1",
    },
    "camera_01": {
        "ip": "10.0.0.2",
        "s1_port": 2,
        "service": "embb",
        "description": "CCTV 카메라 #1",
    },
    "sensor_01": {
        "ip": "10.0.0.3",
        "s1_port": 3,
        "service": "mmtc",
        "description": "IoT 센서 #1",
    },
}

# ---------------------------------------------------------------------------
# HTB 병목 인터페이스 (S1 → S_edge)
# ---------------------------------------------------------------------------
BOTTLENECK_PORT  = S1_PORT_SEDGE   # S1 기준 포트 번호
BOTTLENECK_IFACE = "s1-eth4"

# ---------------------------------------------------------------------------
# 컨트롤러 REST API
# ---------------------------------------------------------------------------
CONTROLLER_HOST = "localhost"
CONTROLLER_PORT = 8080

# ---------------------------------------------------------------------------
# Ollama / Gemma4
# ---------------------------------------------------------------------------
OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma4"

# ---------------------------------------------------------------------------
# 에이전트
# ---------------------------------------------------------------------------
AGENT_INTERVAL_SEC = 10
SLA_MARGIN         = 0.8   # GBR의 몇 %까지 허용 (0.8 = 20% 여유)

# ---------------------------------------------------------------------------
# hostname prefix → 슬라이스 자동 분류 규칙
# prefix가 명확하면 Gemma4를 호출하지 않고 즉시 규칙 기반 처리.
# prefix가 없거나 모호하면 Gemma4에게 판단 위임.
# ---------------------------------------------------------------------------
HOSTNAME_RULES = [
    ("vehicle",   "urllc"),
    ("car",       "urllc"),
    ("v2x",       "urllc"),
    ("ambulance", "urllc"),
    ("camera",    "embb"),
    ("cam",       "embb"),
    ("cctv",      "embb"),
    ("stream",    "embb"),
    ("sensor",    "mmtc"),
    ("iot",       "mmtc"),
    ("meter",     "mmtc"),
    ("light",     "mmtc"),
]

def classify_hostname(hostname: str) -> tuple[str, bool]:
    """hostname prefix로 슬라이스 분류.

    Returns:
        (service, is_rule_based)
        is_rule_based=True → Gemma4 불필요
        is_rule_based=False → Gemma4 판단 필요
    """
    h = hostname.lower()
    for prefix, service in HOSTNAME_RULES:
        if h.startswith(prefix):
            return service, True
    return "mmtc", False   # 모호한 hostname → 기본값 + Gemma4 판단 요청

def get_server_for_service(service: str) -> dict:
    return SERVICE_TO_SERVER.get(service, SERVICE_TO_SERVER["mmtc"])

def get_sfc_chain(service: str) -> list[str]:
    """서비스에 해당하는 SFC NFV 체인 반환."""
    return SFC_CHAINS.get(service, ["nfv_fw"])

def get_host_by_ip(ip: str) -> dict | None:
    for name, profile in HOST_PROFILES.items():
        if profile["ip"] == ip:
            return {"name": name, **profile}
    return None

def get_s1_egress_rules() -> list[tuple]:
    """S1에서 S_edge로 나가는 정적 클라이언트 슬라이스 룰.
    Returns: [(in_port, src_ip, dst_ip, queue_id), ...]
    """
    rules = []
    for name, profile in HOST_PROFILES.items():
        src_ip  = profile["ip"]
        service = profile["service"]
        server  = get_server_for_service(service)
        dst_ip  = server["ip"]
        queue_id = SERVICES[service]["queue_id"]
        rules.append((profile["s1_port"], src_ip, dst_ip, queue_id))
    return sorted(rules)
