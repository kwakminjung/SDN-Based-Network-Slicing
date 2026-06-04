#!/usr/bin/env python3
"""
demo/dashboard.py — SDN SFC Slicing 실시간 대시보드
EC5209 Advanced Computer Networking, Spring 2026

화면 구성:
  ┌── 헤더 ─────────────────────────────────────────────┐
  │  Active Connections (SFC 경로 포함)  │  Slice QoS   │
  ├──────────────────────────────────────┤              │
  │  Agent / Request Log                 │              │
  └──────────────────────────────────────┴──────────────┘
"""

import sys
import os
import re
import time
import subprocess
import threading
import requests
from datetime import datetime
from collections import deque

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config as cfg

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

REFRESH_INTERVAL = 2.0
BW_SAMPLE_SEC    = 1.5
LOG_MAX          = 20

SERVICE_COLOR = {"urllc": "cyan", "embb": "magenta", "mmtc": "green"}
SLICE_LABEL   = {"urllc": "URLLC", "embb": "eMBB",   "mmtc": "mMTC"}

agent_log: deque[str] = deque(maxlen=LOG_MAX)
_log_lock = threading.Lock()

def add_log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    with _log_lock:
        agent_log.appendleft(f"[dim]{ts}[/dim]  {msg}")


# ---------------------------------------------------------------------------
# 데이터 수집
# ---------------------------------------------------------------------------

def read_tc_bytes() -> dict[str, int]:
    result = subprocess.run(
        ["tc", "-s", "class", "show", "dev", cfg.BOTTLENECK_IFACE],
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


def measure_throughput() -> dict[str, float]:
    b0 = read_tc_bytes()
    time.sleep(BW_SAMPLE_SEC)
    b1 = read_tc_bytes()
    result: dict[str, float] = {}
    for svc in cfg.SERVICES:
        delta = max(b1.get(svc, 0) - b0.get(svc, 0), 0)
        result[svc] = round(delta * 8 / BW_SAMPLE_SEC / 1e6, 2)
    return result


def fetch_state() -> dict:
    try:
        r = requests.get(
            f"http://{cfg.CONTROLLER_HOST}:{cfg.CONTROLLER_PORT}/slices", timeout=2)
        return r.json() if r.ok else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# UI 구성
# ---------------------------------------------------------------------------

def sla_status(svc_name: str, mbps: float) -> tuple[str, str]:
    svc = cfg.SERVICES[svc_name]
    if mbps == 0:
        return "-- no traffic", "dim"
    if mbps >= svc["gbr_mbps"] * cfg.SLA_MARGIN:
        return "✅ GBR OK", "green"
    if mbps >= svc["gbr_mbps"] * 0.5:
        return "⚠️ 경고", "yellow"
    return "❌ GBR 위반", "bold red"


def build_connections_table(ctrl_state: dict) -> Table:
    """활성 연결 현황 — SFC 경로 포함."""
    table = Table(
        title="[bold white]Active Connections (SFC Path)[/bold white]",
        box=box.ROUNDED,
        border_style="bright_blue",
        header_style="bold bright_white",
        expand=True,
    )
    table.add_column("클라이언트",   width=14)
    table.add_column("슬라이스",    width=8)
    table.add_column("SFC 경로",    width=30)
    table.add_column("서버",        width=17)

    connections = ctrl_state.get("connections", [])

    if not connections:
        table.add_row("[dim]연결 없음[/dim]", "", "", "")
    else:
        for conn in connections:
            svc   = conn.get("service", "mmtc")
            color = SERVICE_COLOR.get(svc, "white")
            emoji = cfg.SERVICES[svc]["emoji"]
            label = SLICE_LABEL.get(svc, svc)

            chain = conn.get("sfc_chain", [])
            # 예: [nfv_fw] → [nfv_cache] → score
            chain_str = " → ".join(f"[{n}]" for n in chain) + " → score"

            table.add_row(
                f"[{color}]{emoji} {conn['name']}[/{color}]",
                f"[{color}]{label}[/{color}]",
                f"[dim]{chain_str}[/dim]",
                f"[white]{conn['server_name']}[/white]",
            )

    return table


def build_slice_table(throughput: dict, ctrl_state: dict) -> Table:
    """슬라이스별 GBR 달성 현황."""
    table = Table(
        title="[bold white]Slice QoS (GBR/MBR)[/bold white]",
        box=box.ROUNDED,
        border_style="blue",
        header_style="bold bright_white",
        expand=True,
    )
    table.add_column("슬라이스", width=8)
    table.add_column("GBR",     width=10)
    table.add_column("Mbps",    justify="right", width=8)
    table.add_column("SLA",     width=13)

    slices_info = ctrl_state.get("slices", {})

    for svc_name, svc in cfg.SERVICES.items():
        color   = SERVICE_COLOR[svc_name]
        emoji   = svc["emoji"]
        label   = SLICE_LABEL[svc_name]
        gbr     = f"{svc['gbr_mbps']}Mbps"
        mbps    = throughput.get(svc_name, 0)
        sla_icon, sla_color = sla_status(svc_name, mbps)
        bw_str  = f"{mbps:.2f}" if mbps > 0 else "-"

        table.add_row(
            f"[{color}]{emoji} {label}[/{color}]",
            f"[dim]{gbr}[/dim]",
            f"[{color}]{bw_str}[/{color}]",
            f"[{sla_color}]{sla_icon}[/{sla_color}]",
        )

    return table


def build_log_panel() -> Panel:
    with _log_lock:
        lines = list(agent_log)
    body = "\n".join(lines) if lines else "[dim]에이전트 대기 중...[/dim]"
    return Panel(body,
                 title="[bold yellow]Agent / Request Log[/bold yellow]",
                 border_style="yellow", expand=True)


def build_help_panel(any_traffic: bool) -> Panel:
    if not any_traffic:
        notice = (
            "[bold yellow]⚡ 트래픽 없음 — Mininet CLI에서:[/bold yellow]\n"
            "  [white]autodrive    iperf3 -s -p 5001 &[/white]\n"
            "  [white]entertainport iperf3 -s -p 5002 &[/white]\n"
            "  [white]citypulse    iperf3 -s -p 5003 &[/white]\n"
            "  [white]vehicle_01   iperf3 -c 10.0.0.4 -p 5001 -u -b 15M -t 999 &[/white]\n"
            "  [white]camera_01    iperf3 -c 10.0.0.5 -p 5002 -u -b 30M -t 999 &[/white]\n"
            "  [white]sensor_01    iperf3 -c 10.0.0.6 -p 5003 -u -b 5M  -t 999 &[/white]\n\n"
        )
    else:
        notice = ""

    txt = (
        notice +
        "[bold cyan]SFC 체인 확인[/bold cyan]\n"
        "  URLLC: S1 → [nfv_fw]                → score → AutoDrive Hub\n"
        "  eMBB:  S1 → [nfv_fw] → [nfv_cache]  → score → EntertainPort\n"
        "  mMTC:  S1 → [nfv_fw] → [nfv_aggr]   → score → CityPulse Hub\n\n"
        "[bold cyan]동적 클라이언트 (Mininet CLI)[/bold cyan]\n"
        "  [white]py vehicle_02 = add_client(net, 'vehicle_02', s1)[/white]\n"
        "  [white]py camera_02  = add_client(net, 'camera_02',  s1)[/white]"
    )
    border = "yellow" if not any_traffic else "dim"
    return Panel(txt, title="[bold]안내[/bold]", border_style=border, expand=True)


def build_layout(throughput: dict, ctrl_state: dict, conn_ok: bool) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="main"),
        Layout(name="bottom", size=10),
    )
    layout["main"].split_row(
        Layout(name="left",  ratio=3),
        Layout(name="right", ratio=2),
    )
    layout["left"].split_column(
        Layout(name="connections", ratio=3),
        Layout(name="log",         ratio=2),
    )

    ts      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn_str = "[green]●[/green] 컨트롤러 연결됨" if conn_ok else "[red]●[/red] 컨트롤러 오프라인"
    layout["header"].update(Panel(
        Text.from_markup(
            f"[bold bright_blue]SDN Smart City SFC Slicing Dashboard[/bold bright_blue]"
            f"  [dim]{ts}[/dim]   {conn_str}"),
        border_style="bright_blue"))
    layout["connections"].update(build_connections_table(ctrl_state))
    layout["log"].update(build_log_panel())
    layout["right"].update(build_slice_table(throughput, ctrl_state))
    layout["bottom"].update(
        build_help_panel(any(v > 0 for v in throughput.values())))

    return layout


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main():
    console = Console()
    add_log("[bold green]대시보드 시작[/bold green]")

    with Live(console=console, refresh_per_second=0.5, screen=True) as live:
        time.sleep(BW_SAMPLE_SEC)

        while True:
            try:
                throughput = measure_throughput()
                ctrl_state = fetch_state()
                conn_ok    = bool(ctrl_state)
                live.update(build_layout(throughput, ctrl_state, conn_ok))
                time.sleep(max(0, REFRESH_INTERVAL - BW_SAMPLE_SEC))
            except KeyboardInterrupt:
                break
            except Exception as e:
                add_log(f"[red]오류: {e}[/red]")
                time.sleep(REFRESH_INTERVAL)


if __name__ == "__main__":
    main()
