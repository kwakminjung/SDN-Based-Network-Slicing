#!/usr/bin/env python3
"""
nfv/nfv_cache.py — 콘텐츠 캐시 NFV (eMBB 전용)
eMBB 트래픽(camera_* → EntertainPort)만 이 노드를 통과한다.
실제 캐시 로직은 없으며, 경유 사실 로깅 후 재전송한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from nfv.nfv_base import run_nfv

if __name__ == "__main__":
    run_nfv("nfv_cache")
