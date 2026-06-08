"""
nfv/nfv_base.py — NFV 경유 노드 공통 로직 (line-rate raw 소켓 포워더)
EC5209 Advanced Computer Networking, Spring 2026

동작:
  1. 인터페이스를 promiscuous 모드로 설정
  2. AF_PACKET raw 소켓으로 프레임 수신 (자신이 보낸 프레임은 src MAC으로 필터)
  3. SFC 경유 사실을 1/N 샘플링 로깅 (패킷마다 로깅하던 구버전은 line-rate 불가)
  4. src MAC을 자신의 것으로 바꿔 동일 인터페이스로 재전송
     (S_edge가 in_port=nfv 포트로 다음 홉 결정)

설계 메모:
  구버전은 scapy sniff/sendp + 패킷마다 log.info()+flush() 라서 수천 pps에서
  포화되어 부하 시 대량 손실(병목)이 발생했다. 본 버전은 파싱·로깅을 핫패스에서
  걷어내고 raw 소켓 recv/send 루프만 돌려 수만 pps를 처리한다 → NFV가 더 이상
  손실 원인이 되지 않으므로, 종단(서버측) 손실이 슬라이싱의 실제 결과를 반영한다.
"""

import os
import sys
import socket
import logging
import subprocess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import config as cfg

ETH_P_ALL = 0x0003
ETH_P_IP = b"\x08\x00"      # IPv4 ethertype
SAMPLE_EVERY = 500          # 1/N 패킷만 로깅 (SFC 경유 증명용)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def _own_mac(iface: str) -> bytes:
    with open(f"/sys/class/net/{iface}/address") as f:
        return bytes.fromhex(f.read().strip().replace(":", ""))


def _service_of(dst_ip: str) -> tuple[str, str]:
    for srv in cfg.SERVERS.values():
        if dst_ip == srv["ip"]:
            return srv["service"].upper(), srv["name"]
    return "unknown", dst_ip


def run_nfv(nfv_name: str, sample_every: int = SAMPLE_EVERY):
    """NFV 경유 노드 메인 루프 (raw 소켓 line-rate 포워딩)."""
    iface = f"{nfv_name}-eth0"
    own_mac = _own_mac(iface)

    # promiscuous 모드 (dst MAC 불문 모든 프레임 수신)
    subprocess.run(["ip", "link", "set", iface, "promisc", "on"],
                   capture_output=True)

    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW,
                      socket.htons(ETH_P_ALL))
    s.bind((iface, 0))

    log.info("[%s] started on %s (%s) — raw-socket line-rate forwarder, "
             "log 1/%d", nfv_name, iface, own_mac.hex(":"), sample_every)
    sys.stdout.flush()

    count = 0
    while True:
        try:
            frame = s.recv(65535)
        except OSError:
            continue
        if len(frame) < 34:                      # eth(14)+최소 IP 헤더
            continue
        # frame = dst(0:6) src(6:12) ethertype(12:14) payload(14:)
        if frame[6:12] == own_mac:               # 자신이 재전송한 프레임 → 무시 (루프 방지)
            continue
        if frame[12:14] != ETH_P_IP:             # IPv4 만 처리
            continue

        count += 1
        if count % sample_every == 1:            # 샘플 로깅 (SFC 경유 증명)
            src_ip = socket.inet_ntoa(frame[26:30])
            dst_ip = socket.inet_ntoa(frame[30:34])
            service, server_name = _service_of(dst_ip)
            log.info("[%s] %s → %s  (%s → %s)  [pkt #%d]",
                     nfv_name, src_ip, dst_ip, service, server_name, count)
            sys.stdout.flush()

        # src MAC을 자신의 것으로 바꿔 동일 인터페이스로 재전송
        out = frame[0:6] + own_mac + frame[12:]
        try:
            s.send(out)
        except OSError:
            pass
