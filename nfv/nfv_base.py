"""
nfv/nfv_base.py — NFV 경유 노드 공통 로직
EC5209 Advanced Computer Networking, Spring 2026

동작:
  1. 인터페이스를 promiscuous 모드로 설정
  2. scapy로 IP 패킷 sniff (자신이 보낸 패킷은 필터링)
  3. 슬라이스 식별 후 로그 출력
  4. 동일 인터페이스로 패킷 재전송 (S_edge가 in_port로 다음 홉 결정)
"""

import sys
import os
import subprocess
import logging
from datetime import datetime

try:
    from scapy.all import sniff, sendp, IP, Ether, get_if_hwaddr
except ImportError:
    log.error("[%s] scapy not installed. Run: pip install scapy", nfv_name)
    sys.exit(1)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config as cfg

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def run_nfv(nfv_name: str):
    """NFV 경유 노드 메인 루프."""

    iface    = f"{nfv_name}-eth0"
    own_mac  = get_if_hwaddr(iface)

    # promiscuous 모드 활성화 (dst MAC 불문하고 모든 패킷 수신)
    subprocess.run(["ip", "link", "set", iface, "promisc", "on"],
                   capture_output=True)

    log.info("[%s] started on %s (%s) — promiscuous mode ON", nfv_name, iface, own_mac)

    def handle(pkt):
        if IP not in pkt:
            return

        src_ip = pkt[IP].src
        dst_ip = pkt[IP].dst

        # 서비스 식별 (목적지 IP로 슬라이스 판단)
        service = "unknown"
        server_name = dst_ip
        for srv in cfg.SERVERS.values():
            if dst_ip == srv["ip"]:
                service = srv["service"].upper()
                server_name = srv["name"]
                break

        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        log.info("[%s] %s  %s → %s  (%s → %s)",
                 nfv_name, ts, src_ip, dst_ip, service, server_name)
        sys.stdout.flush()

        pkt[Ether].src = own_mac  # src MAC을 자신의 인터페이스 MAC으로 변경
        # 동일 인터페이스로 재전송 — S_edge가 in_port=nfv 포트로 다음 홉 결정
        sendp(pkt, iface=iface, verbose=False)

    # own_mac 필터: 자신이 보낸 패킷(eth src = own_mac)은 재수신하지 않음
    sniff(
        iface=iface,
        prn=handle,
        filter=f"ip and not ether src {own_mac}",
        store=False,
    )
