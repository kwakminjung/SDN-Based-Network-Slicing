#!/usr/bin/env python3
"""
slicing_agent.py — Gemma3 기반 자율 슬라이싱 에이전트 (SFC 버전)
EC5209 Advanced Computer Networking, Spring 2026

Gemma3가 실제로 개입하는 경우:
  1. hostname prefix가 없거나 모호한 경우 (classify_new_host)
     예) device_01, unknown_03 → Gemma3가 패킷 패턴 분석 후 슬라이스 결정
  2. SLA 위반 감지 시 재배정 결정 (run_once 루프)
  3. /slices/request 엔드포인트로 명시적 요청 (handle_service_request)

hostname prefix가 명확한 경우 (vehicle_*, camera_*, sensor_*)는
Gemma3를 호출하지 않고 즉시 규칙 기반으로 처리.

실행:
  python agent/slicing_agent.py           # 10초 주기 SLA 감시
  python agent/slicing_agent.py --once    # 1회 실행
  python agent/slicing_agent.py --dry-run # 분석만, 조치 없음
"""

import sys
import os
import re
import json
import time
import logging
import argparse
import subprocess
import requests
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config as cfg

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [Agent] %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

CONTROLLER_BASE = f"http://{cfg.CONTROLLER_HOST}:{cfg.CONTROLLER_PORT}"


# ---------------------------------------------------------------------------
# 측정: tc class 통계 → 슬라이스별 처리량
# ---------------------------------------------------------------------------

def read_tc_bytes(iface: str = cfg.BOTTLENECK_IFACE) -> dict[str, int]:
    result = subprocess.run(
        ["tc", "-s", "class", "show", "dev", iface],
        capture_output=True, text=True,
    )
    out: dict[str, int] = {}
    cur = None
    for line in result.stdout.splitlines():
        m = re.match(r'\s*class htb (1:\d+)', line)
        if m:
            cur = m.group(1)
        if cur and cur in cfg.CLASS_TO_SERVICE:
            m2 = re.match(r'\s*Sent (\d+) bytes', line)
            if m2:
                out[cfg.CLASS_TO_SERVICE[cur]] = int(m2.group(1))
                cur = None
    return out


def measure_throughput(sample_sec: float = 3.0) -> dict[str, float]:
    b0 = read_tc_bytes()
    t0 = time.time()
    time.sleep(sample_sec)
    b1 = read_tc_bytes()
    dt = time.time() - t0
    return {
        svc: round(max(b1.get(svc, 0) - b0.get(svc, 0), 0) * 8 / dt / 1e6, 2)
        for svc in cfg.SERVICES
    }


# ---------------------------------------------------------------------------
# SLA 위반 감지
# ---------------------------------------------------------------------------

def detect_violations(throughput: dict[str, float]) -> list[dict]:
    violations = []
    for service, mbps in throughput.items():
        svc = cfg.SERVICES[service]
        threshold = svc["gbr_mbps"] * cfg.SLA_MARGIN
        if mbps > 0 and mbps < threshold:
            violations.append({
                "service":      service,
                "metric":       "bandwidth",
                "expected_mbps": svc["gbr_mbps"],
                "actual_mbps":  mbps,
                "threshold_mbps": threshold,
            })
    return violations


# ---------------------------------------------------------------------------
# Gemma3 호출 (system / user 분리, /api/chat 엔드포인트)
# ---------------------------------------------------------------------------

# ── 고정 system 프롬프트 ──────────────────────────────────────────────────
_SYSTEM_CLASSIFY = (
    "You are an SDN smart city network manager responsible for assigning "
    "new clients to the correct network slice.\n"
    "Available slices: URLLC (ultra-low latency, e.g. autonomous vehicles, V2X), "
    "eMBB (high bandwidth, e.g. HD streaming, CCTV), "
    "mMTC (massive IoT, e.g. sensors, smart meters).\n"
    "Respond in JSON only — no text outside the JSON object:\n"
    '{"service": "urllc" | "embb" | "mmtc", "reason": "one sentence"}'
)

_SYSTEM_REQUEST = (
    "You are an SDN network slice manager responsible for accepting or redirecting "
    "client slice requests based on current network load.\n"
    "Respond in JSON only — no text outside the JSON object:\n"
    '{"action": "assign" | "assign_alternative" | "reassign_and_assign" | "reject", '
    '"reason": "one sentence", '
    '"changes": [{"host_ip": "10.x.x.x", "to_service": "urllc|embb|mmtc"}]}'
)

_SYSTEM_SLA = (
    "You are an SDN network slice manager responsible for resolving GBR SLA violations "
    "by reassigning clients to less congested slices.\n"
    "Respond in JSON only — no text outside the JSON object:\n"
    '{"action": "reassign_host" | "no_action", "reason": "one sentence", '
    '"changes": [{"host_ip": "10.x.x.x", "to_service": "urllc|embb|mmtc"}]}'
)


def ask_gemma(system: str, user: str) -> str | None:
    """Ollama /api/chat 호출 (system/user role 분리)."""
    try:
        t0 = time.perf_counter()
        resp = requests.post(
            cfg.OLLAMA_URL,
            json={
                "model": cfg.OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": user},
                ],
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        log.info("Gemma3 latency: %.2f s", time.perf_counter() - t0)
        return resp.json().get("message", {}).get("content", "")
    except requests.exceptions.ConnectionError:
        log.warning("Ollama 연결 실패 — localhost:11434 에서 실행 중인지 확인")
        return None
    except Exception as e:
        log.warning("Gemma3 API 오류: %s", e)
        return None


def parse_json_response(response: str) -> dict | None:
    match = re.search(r'\{[\s\S]*\}', response)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 1. 모호한 hostname 분류 (컨트롤러 Packet-In에서 비동기 호출)
# ---------------------------------------------------------------------------

def classify_new_host(hostname: str,
                      pkt_info: dict | None = None,
                      requirements: str = "") -> str | None:
    """hostname prefix로 분류 불가능한 경우 Gemma3에 판단 위임.

    hostname prefix가 명확한 경우는 컨트롤러에서 직접 처리하므로
    이 함수는 모호한 경우(device_01, unknown_03 등)에만 호출된다.

    Returns:
        서비스 이름 ('urllc' | 'embb' | 'mmtc') 또는 None
    """
    if not hostname:
        return None

    # 혹시 prefix가 있으면 즉시 반환 (이중 방어)
    service, is_rule_based = cfg.classify_hostname(hostname)
    if is_rule_based:
        return service

    pkt_info  = pkt_info or {}
    protocol  = pkt_info.get("protocol", "Unknown")
    dst_port  = pkt_info.get("dst_port")
    pkt_size  = pkt_info.get("pkt_size", 0)

    svc_list = "\n".join(
        f"  {name.upper()}: {svc['description']} "
        f"(PDB {svc['pdb_ms']}ms, GBR {svc['gbr_mbps']}-{svc['mbr_mbps']}Mbps)"
        for name, svc in cfg.SERVICES.items()
    )

    port_line = f"  Destination Port: {dst_port}\n" if dst_port else ""
    size_line = f"  Packet Size: {pkt_size} bytes\n" if pkt_size else ""
    req_line  = f"  Requirements: {requirements}\n" if requirements else ""

    user = (
        "## New Client Information\n"
        f"  Hostname: {hostname}\n"
        f"  Protocol: {protocol}\n"
        f"{port_line}"
        f"{size_line}"
        f"{req_line}"
        "\n## Slice Options\n"
        f"{svc_list}\n\n"
        "The hostname prefix does not match any known classification rule. "
        "Select the most appropriate slice based on the information above."
    )

    log.info("Gemma3 분류 요청 (모호한 hostname): %s", hostname)
    response = ask_gemma(_SYSTEM_CLASSIFY, user)

    if not response:
        log.warning("Gemma3 미응답 — 기본값 mmtc 사용")
        return "mmtc"

    parsed = parse_json_response(response)
    if parsed:
        svc = parsed.get("service", "").lower()
        if svc in cfg.SERVICES:
            log.info("Gemma3: %s → %s (%s)", hostname, svc, parsed.get("reason", ""))
            return svc

    return "mmtc"


# ---------------------------------------------------------------------------
# 2. 요청 기반 배정 (/slices/request 엔드포인트에서 호출)
# ---------------------------------------------------------------------------

def get_controller_state() -> dict | None:
    try:
        return requests.get(f"{CONTROLLER_BASE}/slices", timeout=5).json()
    except Exception as e:
        log.warning("컨트롤러 상태 조회 실패: %s", e)
        return None


def apply_reassignment(host_ip: str, to_service: str,
                        dry_run: bool = False) -> bool:
    log.info("재배정: %s → %s", host_ip, to_service)
    if dry_run:
        return True
    try:
        resp = requests.post(f"{CONTROLLER_BASE}/slices/reassign",
                              json={"host_ip": host_ip, "to_service": to_service},
                              timeout=5)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("재배정 실패: %s", e)
        return False


def handle_service_request(host_ip: str, requested_service: str,
                             dry_run: bool = False) -> dict:
    """명시적 서비스 요청 → Gemma3가 부하 기반으로 최적 배정 결정."""
    if requested_service not in cfg.SERVICES:
        return {"action": "reject",
                "reason": f"알 수 없는 서비스: {requested_service}",
                "changes": []}

    log.info("서비스 요청: %s → %s", host_ip, requested_service)

    current_loads = measure_throughput(sample_sec=2.0)
    current_state = get_controller_state() or {}

    host = cfg.get_host_by_ip(host_ip)
    host_name = host["name"] if host else host_ip
    svc_info  = cfg.SERVICES[requested_service]
    server    = cfg.get_server_for_service(requested_service)
    chain     = " → ".join(cfg.get_sfc_chain(requested_service))

    lines = ["## Current Slice Status\n"]
    for svc_name, svc in cfg.SERVICES.items():
        mbps = current_loads.get(svc_name, 0)
        util = mbps / svc["mbr_mbps"] * 100 if svc["mbr_mbps"] > 0 else 0
        clients = current_state.get("slices", {}).get(svc_name, {}).get("clients", [])
        host_str = ", ".join(c["name"] for c in clients) or "none"
        lines.append(
            f"{svc_name.upper()} (GBR {svc['gbr_mbps']}-{svc['mbr_mbps']}Mbps, "
            f"SFC: {' → '.join(cfg.SFC_CHAINS[svc_name])})\n"
            f"  Load: {mbps:.1f}Mbps ({util:.0f}%)  Hosts: {host_str}\n"
        )

    lines.append(
        f"\n## Request\n"
        f"{host_name} ({host_ip}) requests {requested_service.upper()} "
        f"(SLA: GBR {svc_info['gbr_mbps']}Mbps, PDB {svc_info['pdb_ms']}ms, "
        f"SFC: {chain} → {server['name']})\n\n"
        "Decide whether to accept the requested slice."
    )

    user = "\n".join(lines)
    log.info("Gemma3 배정 판단 요청...")
    response = ask_gemma(_SYSTEM_REQUEST, user)

    if response:
        action = parse_json_response(response)
    else:
        log.warning("Gemma3 미응답 — 규칙 기반 폴백")
        action = _rule_based_assignment(host_ip, requested_service, current_loads)

    if not action:
        action = _rule_based_assignment(host_ip, requested_service, current_loads)

    log.info("배정 결정: %s — %s", action.get("action"), action.get("reason"))

    if action.get("action") != "reject" and action.get("changes"):
        for change in action["changes"]:
            apply_reassignment(change["host_ip"], change["to_service"], dry_run)

    return action


def _rule_based_assignment(host_ip: str, requested_service: str,
                             loads: dict[str, float]) -> dict:
    svc  = cfg.SERVICES[requested_service]
    util = loads.get(requested_service, 0) / svc["mbr_mbps"] if svc["mbr_mbps"] > 0 else 0

    if util < 0.8:
        return {"action": "assign",
                "reason": f"{requested_service.upper()} slice has capacity ({util*100:.0f}% used)",
                "changes": [{"host_ip": host_ip, "to_service": requested_service}]}

    for alt in ["mmtc", "embb", "urllc"]:
        if alt == requested_service:
            continue
        alt_util = loads.get(alt, 0) / cfg.SERVICES[alt]["mbr_mbps"]
        if alt_util < 0.8:
            return {"action": "assign_alternative",
                    "reason": f"{requested_service.upper()} saturated — assigning {alt.upper()} as alternative",
                    "changes": [{"host_ip": host_ip, "to_service": alt}]}

    return {"action": "reject", "reason": "All slices saturated", "changes": []}


# ---------------------------------------------------------------------------
# 3. SLA 모니터링 루프 (주기적 자동 재배정)
# ---------------------------------------------------------------------------

def build_sla_user(throughput: dict, violations: list[dict],
                   ctrl_state: dict) -> str:
    """SLA 모니터링용 user 메시지 생성 (system은 _SYSTEM_SLA 상수 사용)."""
    lines = ["## Current Network Slice Status\n"]
    for service, svc in cfg.SERVICES.items():
        mbps  = throughput.get(service, 0)
        chain = " → ".join(cfg.SFC_CHAINS[service])
        status = "OK"
        for v in violations:
            if v["service"] == service:
                status = f"GBR VIOLATION ({mbps}Mbps < {svc['gbr_mbps']}Mbps)"

        clients = ctrl_state.get("slices", {}).get(service, {}).get("clients", [])
        host_str = ", ".join(c["name"] for c in clients) or "none"

        lines.append(
            f"{service.upper()} (GBR {svc['gbr_mbps']}Mbps, SFC: {chain})\n"
            f"  Current: {mbps}Mbps  Hosts: {host_str}  Status: {status}\n"
        )

    lines.append("\nPropose a reassignment plan to resolve the GBR violation.")
    return "\n".join(lines)


def run_once(dry_run: bool = False) -> None:
    log.info("=" * 60)
    log.info("SLA 감시 사이클 (%s)", datetime.now().strftime("%H:%M:%S"))

    throughput = measure_throughput(sample_sec=3.0)
    for svc, mbps in throughput.items():
        log.info("  %-6s: %.2f Mbps", svc, mbps)

    violations = detect_violations(throughput)
    if not violations:
        log.info("SLA 위반 없음")
        return

    for v in violations:
        log.warning("GBR 위반 — %s: %.2f < %.2f Mbps",
                    v["service"], v["actual_mbps"], v["expected_mbps"])

    ctrl_state = get_controller_state() or {}
    user       = build_sla_user(throughput, violations, ctrl_state)
    log.info("Gemma3 SLA 복구 방안 요청...")
    response = ask_gemma(_SYSTEM_SLA, user)

    if response:
        action = parse_json_response(response)
    else:
        log.warning("Gemma3 미응답 — 룰 기반 폴백")
        action = _fallback_sla_action(violations)

    if not action:
        log.warning("파싱 실패 — 이번 사이클 건너뜀")
        return

    log.info("결정: %s — %s", action.get("action"), action.get("reason"))

    if action.get("action") == "reassign_host" and action.get("changes"):
        for change in action["changes"]:
            apply_reassignment(change["host_ip"], change["to_service"], dry_run)


def _fallback_sla_action(violations: list[dict]) -> dict:
    for v in violations:
        if v["service"] == "urllc":
            for name, profile in cfg.HOST_PROFILES.items():
                if profile["service"] == "embb":
                    return {
                        "action": "reassign_host",
                        "reason": "URLLC GBR violation — demoting eMBB host to mMTC to free bandwidth",
                        "changes": [{"host_ip": profile["ip"], "to_service": "mmtc"}],
                    }
    return {"action": "no_action", "reason": "No rule-based action applicable", "changes": []}


def main():
    parser = argparse.ArgumentParser(description="SDN SFC Slicing Agent (Gemma3)")
    parser.add_argument("--once",    action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    log.info("에이전트 시작 (모델: %s, 주기: %ds)", cfg.OLLAMA_MODEL, cfg.AGENT_INTERVAL_SEC)
    log.info("Gemma3 개입 조건: 모호한 hostname | SLA 위반 | /slices/request 명시적 요청")

    if args.once:
        run_once(dry_run=args.dry_run)
        return

    while True:
        try:
            run_once(dry_run=args.dry_run)
        except KeyboardInterrupt:
            log.info("에이전트 종료")
            break
        except Exception as e:
            log.error("사이클 오류: %s", e)
        time.sleep(cfg.AGENT_INTERVAL_SEC)


if __name__ == "__main__":
    main()
