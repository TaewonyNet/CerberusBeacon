"""
tests/conftest.py — 공통 pytest 설정
tornado / terminado mock — 미설치 환경에서도 import 가능하도록
"""
import sys
from unittest.mock import MagicMock

# tornado, terminado를 mock으로 미리 등록 (미설치 환경 대비)
for _m in ("terminado",):
    if _m not in sys.modules:
        sys.modules[_m] = MagicMock()
