"""
src/activity.py — 서버 전체 활동 시각 추적 (HTTP + WS 통합)
web.py / terminal.py / agent.py 에서 공통 임포트
"""
from __future__ import annotations
import time

_last_activity: float = time.time()


def touch() -> None:
    global _last_activity
    _last_activity = time.time()


def idle_seconds() -> float:
    return time.time() - _last_activity
