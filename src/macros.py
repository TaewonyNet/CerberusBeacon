"""
src/macros.py — _macros, _macros_lock, MacrosHandler
SPEC.md §6
"""
from __future__ import annotations

import json
import threading

import tornado.web

from src.agent import AgentAuthMixin
from src.auth import _COOKIE_NAME
from src.config import CERBERUS_DIR

# ── 공유 상태 ──────────────────────────────────────────────────────────────────
_macros: list = []   # [{"label": str, "send": str}, ...]
_macros_lock = threading.Lock()

_MACROS_FILE = CERBERUS_DIR / "macros.json"


def load_macros() -> list | None:
    """~/.cerberus/macros.json 에서 매크로 로드. 파일 없으면 None 반환."""
    if not _MACROS_FILE.exists():
        return None
    try:
        return json.loads(_MACROS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_macros(macros: list) -> None:
    """매크로를 ~/.cerberus/macros.json 에 저장."""
    try:
        CERBERUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        _MACROS_FILE.write_text(json.dumps(macros, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


class MacrosHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """GET /api/macros → 현재 매크로 목록 JSON
    POST /api/macros → 목록 전체 교체 + ~/.cerberus/macros.json 에 저장"""

    def get(self):
        with _macros_lock:
            data = list(_macros)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(data))

    def post(self):
        # POST는 세션 전용 — 에이전트 토큰으로는 변경 불가 (스팩 §10)
        if not self.get_secure_cookie(_COOKIE_NAME):
            self.set_status(403)
            self.write(json.dumps({"error": "session required"}))
            return
        global _macros
        try:
            body = json.loads(self.request.body)
            if not isinstance(body, list):
                raise ValueError("expected list")
            for item in body:
                if not isinstance(item.get("label"), str) or not isinstance(item.get("send"), str):
                    raise ValueError("invalid macro item")
        except Exception as e:
            self.set_status(400)
            self.write(json.dumps({"error": str(e)}))
            return
        with _macros_lock:
            _macros = body
        save_macros(body)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"ok": True, "count": len(body)}))
