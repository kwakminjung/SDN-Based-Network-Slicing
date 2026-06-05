#!/usr/bin/env python3
"""
nfv/nfv_fw.py — 방화벽 NFV (모든 슬라이스 공통 경유)
URLLC / eMBB / mMTC 트래픽이 모두 이 노드를 통과한다.
실제 방화벽 로직은 없으며, 경유 사실 로깅 후 재전송한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from nfv.nfv_base import run_nfv

if __name__ == "__main__":
    run_nfv("nfv_fw")
