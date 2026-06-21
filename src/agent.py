"""
src/agent.py — ensure_api_token, AgentAuthMixin, Agent*Handler (세션 관리 HTTP API)
SPEC.md §7
"""
from __future__ import annotations

import json
import os
import secrets

import tornado.web

from src.auth import _COOKIE_NAME, verify_agent_totp
from src.config import CERBERUS_DIR, Config
from src.terminal import (
    _HAS_TERMINADO,
    _make_session_id,
    _get_session_log_path,
    _sessions,
    _sessions_lock,
)

# ── 파일 경로 ──────────────────────────────────────────────────────────────────
_API_TOKEN_FILE = CERBERUS_DIR / "api_token"
_agent_api_token: str = ""
_agent_otp_secret: str = ""   # ensure_agent_otp_secret() 로 초기화


def ensure_api_token() -> str:
    """_API_TOKEN_FILE 읽기 또는 생성 (0600). 첫 생성 시 터미널 출력."""
    CERBERUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _API_TOKEN_FILE.exists():
        return _API_TOKEN_FILE.read_text().strip()
    token = secrets.token_hex(32)
    _API_TOKEN_FILE.write_text(token)
    _API_TOKEN_FILE.chmod(0o600)
    print(f"[AGENT] API 토큰 생성됨: {token}", flush=True)
    return token


class AgentAuthMixin:
    """에이전트 2FA (X-API-Token + X-OTP) 또는 cb_session 쿠키 인증.
    브라우저 세션은 OTP 불필요 (이미 TOTP 인증 완료).
    에이전트는 Token + OTP 둘 다 필수."""

    def _check_agent_token_otp(self) -> bool:
        token = (
            self.request.headers.get("X-API-Token", "")
            or self.get_argument("token", "")
        )
        if not (token == _agent_api_token and bool(_agent_api_token)):
            return False
        otp = (
            self.request.headers.get("X-OTP", "")
            or self.get_argument("otp", "")
        )
        if not otp or not _agent_otp_secret:
            return False
        return verify_agent_totp(otp, _agent_otp_secret)

    def _check_session_cookie(self) -> bool:
        getter = getattr(self, "get_secure_cookie", None)
        if getter is None:
            return False
        return bool(getter(_COOKIE_NAME))

    def prepare(self):
        if not (self._check_agent_token_otp() or self._check_session_cookie()):
            self.set_status(401)
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({"error": "unauthorized: token+otp required"}))
            self.finish()
            return
        import src.activity as _activity
        _activity.touch()
        from src.tunnel import touch_tunnels
        touch_tunnels()


class AgentSessionsHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """GET /api/agent/sessions → {"sessions": [...]}"""

    def initialize(self, term_manager, cfg: Config) -> None:
        self._tm = term_manager
        self._cfg = cfg

    def get(self):
        if not self._cfg.terminal_enabled or not self._cfg.agent_enabled:
            self.set_status(503)
            self.write(json.dumps({"error": "terminal/agent not enabled"}))
            return
        with _sessions_lock:
            session_ids = list(_sessions.keys())
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"sessions": session_ids}))


class AgentNewSessionHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """POST /api/agent/session → {"session_id": "sess_xxx"}"""

    def initialize(self, term_manager, cfg: Config) -> None:
        self._tm = term_manager
        self._cfg = cfg

    def post(self):
        if not self._cfg.terminal_enabled:
            self.set_status(503)
            self.write(json.dumps({"error": "terminal not enabled"}))
            return
        if not self._cfg.agent_enabled:
            self.set_status(503)
            self.write(json.dumps({"error": "agent not enabled"}))
            return
        with _sessions_lock:
            if len(_sessions) >= self._cfg.max_sessions:
                self.set_status(503)
                self.write(json.dumps({"error": "max sessions reached"}))
                return

        session_id = _make_session_id()

        # terminado term_manager로 새 터미널 생성
        if self._tm is not None and _HAS_TERMINADO:
            try:
                term = self._tm.new_named_terminal(name=session_id)
            except Exception:
                term = None
        else:
            term = None

        # 세션 로그 파일 오픈
        log_file = None
        if self._cfg.session_log_enabled:
            try:
                log_path = _get_session_log_path(self._cfg, session_id)
                log_file = open(log_path, "ab")
            except Exception:
                pass

        with _sessions_lock:
            _sessions[session_id] = {
                "term": term,
                "clients": [],
                "log_file": log_file,
            }

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"session_id": session_id}))


class AgentDeleteSessionHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """DELETE /api/agent/session/<id>"""

    def initialize(self, term_manager, cfg: Config) -> None:
        self._tm = term_manager
        self._cfg = cfg

    def delete(self, session_id: str):
        with _sessions_lock:
            session = _sessions.pop(session_id, None)
        if session is None:
            self.set_status(404)
            self.write(json.dumps({"error": "session not found"}))
            return
        # pty SIGTERM
        term = session.get("term")
        if term is not None and _HAS_TERMINADO:
            try:
                import signal
                if hasattr(term, 'ptyproc'):
                    os.kill(term.ptyproc.pid, signal.SIGTERM)
                elif hasattr(term, 'pid'):
                    os.kill(term.pid, signal.SIGTERM)
            except Exception:
                pass
        # 연결된 WS 닫기
        for client in list(session.get("clients", [])):
            try:
                client.close()
            except Exception:
                pass
        # 로그 파일 닫기
        log_file = session.get("log_file")
        if log_file and not log_file.closed:
            try:
                log_file.close()
            except Exception:
                pass
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"message": f"{session_id} 종료됨"}))

