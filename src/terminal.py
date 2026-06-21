"""
src/terminal.py — _ActivityTermSocket, _Disabled404Handler, 세션 관리
공유 상태: _sessions, _sessions_lock
SPEC.md §5
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
import tornado.web

from src.auth import _COOKIE_NAME
from src.config import Config
from src.tunnel import _tunnels, _tlock

# ── 세션 저장소: session_id → {"term": terminal_obj, "clients": list[ws], "log_file": file} ──
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()

# terminado 로드 시도
_HAS_TERMINADO = False
try:
    from terminado import TermSocket, NamedTermManager  # type: ignore
    _HAS_TERMINADO = True
except ImportError:
    pass


def _make_session_id() -> str:
    """sess_ + uuid4 hex 앞 16자리 (64비트 공간, 충돌 위험 최소화)."""
    import uuid
    return "sess_" + uuid.uuid4().hex[:16]


def _get_session_log_path(cfg: Config, session_id: str) -> Path:
    log_dir = Path(os.path.expanduser(cfg.session_log_dir))
    log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    filename = cfg.session_log_filename.format(session_id=session_id, date=date_str)
    return log_dir / filename


def _cleanup_old_logs(cfg: Config) -> None:
    """max_age_days 초과 로그 파일 삭제."""
    log_dir = Path(os.path.expanduser(cfg.session_log_dir))
    if not log_dir.exists():
        return
    cutoff = time.time() - cfg.session_log_max_age_days * 86400
    for p in log_dir.iterdir():
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except Exception:
            pass


if _HAS_TERMINADO:
    class _ActivityTermSocket(TermSocket):
        """TermSocket 서브클래스: 다중 클라이언트 지원 + 활동 추적 + 세션 로그."""

        def initialize(self, term_manager, cfg: Config) -> None:
            super().initialize(term_manager=term_manager)
            self._cb_cfg = cfg

        def open(self, term_name: str) -> None:
            # 인증: cb_session 쿠키(브라우저) 또는 X-API-Token + X-OTP 2FA(에이전트)
            # SPEC §7.2 — 에이전트 경로는 토큰+OTP 둘 다 필수
            cookie = self.get_secure_cookie(_COOKIE_NAME)
            if cookie:
                authed = True
            else:
                from src.agent import _agent_api_token, _agent_otp_secret
                from src.auth import verify_agent_totp
                # 에이전트 토큰 경로는 agent_enabled 시에만 허용
                if not getattr(self._cb_cfg, "agent_enabled", False):
                    self.close(code=401, reason="unauthorized")
                    return
                token = (
                    self.request.headers.get("X-API-Token", "")
                    or self.get_argument("token", "")
                )
                otp = (
                    self.request.headers.get("X-OTP", "")
                    or self.get_argument("otp", "")
                )
                authed = (
                    bool(token) and token == _agent_api_token
                    and bool(otp) and bool(_agent_otp_secret)
                    and verify_agent_totp(otp, _agent_otp_secret)
                )
            if not authed:
                self.close(code=401, reason="unauthorized")
                return
            super().open(term_name)
            with _sessions_lock:
                session = _sessions.get(term_name)
                if session is not None:
                    if self not in session["clients"]:
                        session["clients"].append(self)

        def on_close(self) -> None:
            # term_name은 url 경로에서 추출
            term_name = self.term_name if hasattr(self, 'term_name') else None
            if term_name:
                with _sessions_lock:
                    session = _sessions.get(term_name)
                    if session and self in session["clients"]:
                        session["clients"].remove(self)
            super().on_close()

        def on_message(self, message) -> None:
            import src.activity as _activity
            _activity.touch()
            super().on_message(message)

        def on_pty_read(self, text: str) -> None:
            """pty stdout → 모든 클라이언트에 브로드캐스트 + 로그 기록."""
            term_name = self.term_name if hasattr(self, 'term_name') else None
            if term_name:
                with _sessions_lock:
                    session = _sessions.get(term_name)
                if session:
                    # 모든 연결된 클라이언트에 브로드캐스트
                    for client in list(session.get("clients", [])):
                        if client is not self:
                            try:
                                client.write_message(json.dumps(["stdout", text]))
                            except Exception:
                                pass
                    # 세션 로그 기록
                    log_file = session.get("log_file")
                    if log_file and not log_file.closed:
                        try:
                            raw = text.encode("utf-8", errors="replace")
                            log_file.write(raw)
                            log_file.flush()
                            # rotate 체크
                            cfg = self._cb_cfg
                            if cfg.session_log_enabled:
                                pos = log_file.tell()
                                if pos > cfg.session_log_max_size_mb * 1024 * 1024:
                                    log_path = Path(log_file.name)
                                    log_file.close()
                                    rotated = Path(str(log_path) + ".1")
                                    if rotated.exists():
                                        rotated.unlink()
                                    log_path.rename(rotated)
                                    new_log = open(log_path, "ab")
                                    session["log_file"] = new_log
                        except Exception:
                            pass
            # 활동 갱신
            with _tlock:
                for info in _tunnels.values():
                    if info.track_activity:
                        info.last_activity = time.time()
            super().on_pty_read(text)
else:
    class _ActivityTermSocket:  # type: ignore
        """terminado 미설치 시 더미 클래스."""
        pass


class _Disabled404Handler(tornado.web.RequestHandler):
    """terminal_enabled=False 일 때 /ws/* 에 404 반환."""
    def get(self, *args, **kwargs):
        self.set_status(404)
        self.write(json.dumps({"error": "terminal not enabled"}))
