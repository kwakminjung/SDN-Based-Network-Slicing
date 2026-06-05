#!/usr/bin/env python3
"""
nfv/nfv_aggr.py — 데이터 집계 NFV (mMTC 전용)
mMTC 트래픽(sensor_* → CityPulse Hub)만 이 노드를 통과한다.
실제 집계 로직은 없으며, 경유 사실 로깅 후 재전송한다.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from nfv.nfv_base import run_nfv

if __name__ == "__main__":
    run_nfv("nfv_aggr")
