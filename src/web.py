"""
src/web.py — MainHandler, *Handler, build_tornado_app, run_server
UI는 templates/{main.html,main.js,terminal.js}에서 로드.
SPEC.md §8
"""
from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

import tornado.ioloop
import tornado.web

from src.auth import (
    AuthMixin,
    HealthHandler,
    LoginHandler,
    LogoutHandler,
    _COOKIE_SECRET,
    ensure_otp_secret,
    ensure_agent_otp_secret,
    ensure_device_key,
    print_qr,
    print_agent_qr,
)
from src.config import CERBERUS_DIR, Config
from src.agent import (
    AgentAuthMixin,
    AgentDeleteSessionHandler,
    AgentNewSessionHandler,
    AgentSessionsHandler,
    ensure_api_token,
)
import src.agent as _agent_mod
from src.files import FileDownloadHandler, FileTreeHandler, FileUploadHandler
from src.macros import MacrosHandler
import src.macros as _macros_mod
from src.metrics import MetricsHandler
import src.metrics as _metrics_mod
from src.terminal import _ActivityTermSocket, _Disabled404Handler, _HAS_TERMINADO, _cleanup_old_logs
from src.tunnel import (
    open_tunnel,
    restore_tunnels,
    tunnel_status_data,
    toggle_tunnel_lock,
    _idle_watchdog,
)
import src.tunnel as _tunnel_mod

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(name: str) -> str:
    return (_TEMPLATES_DIR / name).read_text(encoding="utf-8")

try:
    from terminado import NamedTermManager  # type: ignore
except ImportError:
    NamedTermManager = None  # type: ignore




class MainHandler(AuthMixin, tornado.web.RequestHandler):
    """GET / → 메인 UI"""

    def initialize(self, cfg: Config) -> None:
        self._cfg = cfg

    def get(self):
        # 페이지 로드를 활동으로 간주하여 last_activity 갱신
        import src.tunnel as _tm
        _now = time.time()
        with _tm._tlock:
            for _ti in _tm._tunnels.values():
                if _ti.track_activity:
                    _ti.last_activity = _now

        te = self._cfg.terminal_enabled
        files_root_js = json.dumps(str(self._cfg.files_root))
        hidden_flag = "true" if self._cfg.files_hidden else "false"

        if te:
            xterm_css = (
                '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm@5.3.0/css/xterm.min.css">'
            )
            xterm_js = (
                '<script src="https://cdn.jsdelivr.net/npm/xterm@5.3.0/lib/xterm.min.js"></script>'
                '<script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.min.js"></script>'
            )
            terminal_section = """
    <div class="acc-section">
      <div class="acc-header" onclick="toggleAcc('acc-session-list')">
        <span class="acc-arrow" id="arr-acc-session-list">▶</span> [[sec_terminal]]
      </div>
      <div class="acc-body open" id="acc-session-list">
        <div id="session-list">[[loading]]</div>
      </div>
    </div>"""
            term_tabs = '<div id="term-tabs"></div>'
            term_display = "flex"
            newtab_display = "inline"
            terminal_js = _load_template("terminal.js")
        else:
            xterm_css = ""
            xterm_js = ""
            terminal_section = ""
            term_tabs = ""
            term_display = "none"
            newtab_display = "none"
            terminal_js = "// 터미널 비활성"

        from src.i18n import resolve_lang, get_strings, get_js_const, apply_html
        lang = resolve_lang(self._cfg.lang)
        strings = get_strings(lang)

        # main.js를 파일에서 읽고 terminal_js 삽입
        main_js = _load_template("main.js").replace("%(terminal_js)s", terminal_js)

        subs = {
            "xterm_css": xterm_css,
            "xterm_js": xterm_js,
            "macros_section": "",
            "terminal_section": terminal_section,
            "term_tabs": term_tabs,
            "term_display": term_display,
            "newtab_display": newtab_display,
            "main_js": main_js,
            "terminal_enabled_js": "true" if te else "false",
            "server_port_js": str(self._cfg.port),
            "files_root_js": files_root_js,
            "hidden_flag": hidden_flag,
            "lang": lang,
            "i18n_js": get_js_const(lang),
        }
        html = _load_template("main.html")
        for k, v in subs.items():
            html = html.replace(f"%({k})s", v)
        html = apply_html(html, strings)
        self.write(html)


# ── Tunnel API handlers (defined here to avoid circular imports) ───────────────

class TunnelOpenHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """POST /api/tunnel/open  body: {"port": 8765}"""

    def post(self):
        try:
            body = json.loads(self.request.body)
            port = int(body.get("port", _tunnel_mod._current_cfg_port))
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "invalid body"}))
            return
        msg = open_tunnel(port)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"message": msg}))


class TunnelCloseHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """POST /api/tunnel/close  body: {"port": 8765} or {"all": true}"""

    def post(self):
        try:
            body = json.loads(self.request.body)
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "invalid body"}))
            return
        from src.tunnel import close_all_tunnels as _close_all, close_tunnel as _close
        if body.get("all"):
            msg = _close_all()
        else:
            port = int(body.get("port", 0))
            msg = _close(port)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"message": msg}))


class TunnelStatusHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """GET /api/tunnel/status"""

    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"tunnels": tunnel_status_data()}))


class TunnelLockHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """POST /api/tunnel/lock  body: {"port": 8765}  — track_activity 토글"""

    def post(self):
        try:
            body = json.loads(self.request.body)
            port = int(body.get("port", 0))
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "invalid body"}))
            return
        msg = toggle_tunnel_lock(port)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"message": msg}))


class SettingsHandler(AuthMixin, tornado.web.RequestHandler):
    """GET /api/settings → 현재 설정 반환
    POST /api/settings → .env 저장"""

    def initialize(self, cfg: Config) -> None:
        self._cfg = cfg

    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({
            "tg_token":      self._cfg.tg_token,
            "tg_chat_id":    self._cfg.tg_chat_id,
            "idle_timeout":  self._cfg.idle_timeout,
            "terminal_shell": self._cfg.terminal_shell,
            "port":          self._cfg.port,
            "lang":          self._cfg.lang,
        }))

    def post(self):
        from src.config import save_config
        try:
            body = json.loads(self.request.body)
        except Exception:
            self.set_status(400); self.write(json.dumps({"error": "invalid body"})); return
        if "tg_token" in body:
            self._cfg.tg_token = str(body["tg_token"])
        if "tg_chat_id" in body:
            self._cfg.tg_chat_id = int(body["tg_chat_id"])
        if "idle_timeout" in body:
            v = max(0, int(body["idle_timeout"]))
            self._cfg.idle_timeout = v
            _tunnel_mod._idle_timeout_minutes = v
        if "terminal_shell" in body:
            self._cfg.terminal_shell = str(body["terminal_shell"])
        if "lang" in body and body["lang"] in ("", "ko", "en"):
            self._cfg.lang = str(body["lang"])
        try:
            save_config(self._cfg)
        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({"error": str(e)}))
            return
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"ok": True}))


class IdleTimeoutApiHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """GET/POST /api/idle-timeout (봇/에이전트 전용 — 웹 UI는 /api/settings 사용)"""

    def initialize(self, cfg: Config) -> None:
        self._cfg = cfg

    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"idle_timeout_minutes": self._cfg.idle_timeout}))

    def post(self):
        try:
            body = json.loads(self.request.body)
            minutes = int(body.get("idle_timeout", self._cfg.idle_timeout))
            minutes = max(0, min(1440, minutes))
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "invalid body"}))
            return
        self._cfg.idle_timeout = minutes
        _tunnel_mod._idle_timeout_minutes = minutes
        # 봇/CLI 경유 변경도 .env에 영속화 (웹 /api/settings와 일관)
        try:
            from src.config import save_config
            save_config(self._cfg)
        except Exception:
            pass
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"idle_timeout_minutes": minutes}))


def build_tornado_app(cfg: Config, otp_secret: str, device_key: str, term_manager=None) -> tornado.web.Application:
    """Tornado Application 생성 + 라우팅 설정."""

    handlers = [
        (r"/health", HealthHandler),
        (r"/login", LoginHandler, {"otp_secret": otp_secret, "cfg": cfg, "device_key": device_key}),
        (r"/logout", LogoutHandler),
        (r"/", MainHandler, {"cfg": cfg}),
        (r"/api/tunnel/open", TunnelOpenHandler),
        (r"/api/tunnel/close", TunnelCloseHandler),
        (r"/api/tunnel/status", TunnelStatusHandler),
        (r"/api/tunnel/lock", TunnelLockHandler),
        (r"/api/settings", SettingsHandler, {"cfg": cfg}),
        (r"/api/tree", FileTreeHandler, {"files_root": cfg.files_root, "hidden": cfg.files_hidden, "cfg": cfg}),
        (r"/api/download", FileDownloadHandler, {"files_root": cfg.files_root}),
        (r"/api/upload", FileUploadHandler, {"files_root": cfg.files_root, "max_upload_bytes": cfg.max_upload_bytes}),
        (r"/api/metrics", MetricsHandler),
        (r"/api/idle-timeout", IdleTimeoutApiHandler, {"cfg": cfg}),
        (r"/api/macros", MacrosHandler),
    ]

    if cfg.terminal_enabled:
        handlers.append(
            (r"/ws/([^/]+)", _ActivityTermSocket, {"term_manager": term_manager, "cfg": cfg})
        )
    else:
        handlers.append((r"/ws/([^/]+)", _Disabled404Handler))

    # 에이전트 API
    if cfg.agent_enabled:
        agent_init = {"term_manager": term_manager, "cfg": cfg}
        handlers += [
            (r"/api/agent/sessions", AgentSessionsHandler, agent_init),
            (r"/api/agent/session", AgentNewSessionHandler, agent_init),
            (r"/api/agent/session/([^/]+)", AgentDeleteSessionHandler, agent_init),
        ]
    else:
        class _Disabled503Handler(tornado.web.RequestHandler):
            def prepare(self):
                self.set_status(503)
                self.write(json.dumps({"error": "agent not enabled"}))
                self.finish()
        handlers += [
            (r"/api/agent/sessions", _Disabled503Handler),
            (r"/api/agent/session", _Disabled503Handler),
            (r"/api/agent/session/([^/]+)", _Disabled503Handler),
        ]

    return tornado.web.Application(
        handlers,
        cookie_secret=_COOKIE_SECRET,
        xsrf_cookies=False,
    )


def _init_config_template():
    """--init: .env.sample → .env 복사 (이미 있으면 건너뜀)."""
    import os as _os
    src = Path(".env.sample")
    dst = Path(_os.getenv("CB_ENV_FILE", ".env"))
    if dst.exists():
        print(f"이미 존재: {dst}")
        return
    if not src.exists():
        print(f".env.sample 파일이 없습니다. 저장소 루트에서 실행하세요.")
        return
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    print(f".env 생성됨: {dst}  — 값을 채운 뒤 서버를 시작하세요.")


def run_server(cfg: Config) -> None:
    """서버 전체 시작."""
    # 1. CERBERUS_DIR 초기화
    CERBERUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    # 2. otp_secret 로드 + QR 출력
    otp_secret = ensure_otp_secret()
    print_qr(otp_secret)

    # 3. api_token + agent_otp_secret 로드
    _agent_mod._agent_api_token = ensure_api_token()
    agent_otp_file = CERBERUS_DIR / "agent_otp_secret"
    is_new_agent_otp = not agent_otp_file.exists()
    _agent_mod._agent_otp_secret = ensure_agent_otp_secret()
    if is_new_agent_otp:
        print_agent_qr(_agent_mod._agent_otp_secret)
        print("[AGENT] 위 QR을 인증 앱에 별도 등록하세요 (브라우저 로그인용과 구분)", flush=True)

    # 4. _macros 초기화 (저장된 파일 우선, 없으면 cfg 기본값)
    from src.macros import load_macros as _load_macros_file
    _saved_macros = _load_macros_file()
    with _macros_mod._macros_lock:
        _macros_mod._macros.clear()
        _macros_mod._macros.extend(_saved_macros if _saved_macros is not None else cfg.macros)

    # 5. 런타임 설정
    _tunnel_mod._idle_timeout_minutes = cfg.idle_timeout
    _tunnel_mod._current_cfg_port = cfg.port
    _tunnel_mod._lang = getattr(cfg, "lang", "")
    _metrics_mod._metrics_disk_path = cfg.metrics_disk_path or str(cfg.files_root)

    # 5a. 이전 서버 세션의 살아있는 cloudflared 복원
    restore_tunnels()

    # 터미널 활성화 시 terminado 확인
    term_manager = None
    if cfg.terminal_enabled:
        if not _HAS_TERMINADO:
            raise RuntimeError(
                "terminal_enabled=true 이지만 terminado가 설치되지 않았습니다.\n"
                "pip install terminado 를 실행하세요."
            )
        term_manager = NamedTermManager(
            shell_command=[cfg.terminal_shell],
            max_terminals=cfg.max_sessions,
        )

    # device_key 로드
    device_key = ensure_device_key()

    # 6. Tornado 앱 빌드 + listen
    app = build_tornado_app(cfg, otp_secret, device_key, term_manager)
    app.listen(cfg.port, address="127.0.0.1")
    print(f"[SERVER] http://127.0.0.1:{cfg.port}", flush=True)

    # 7. watchdog 데몬 스레드 시작
    watchdog_thread = threading.Thread(
        target=_idle_watchdog,
        args=(cfg,),
        daemon=True,
        name="idle-watchdog",
    )
    watchdog_thread.start()

    # 서버 유휴 자동 종료 (CB_SERVER_IDLE_MINUTES, 0=비활성)
    import src.activity as _activity_mod
    _activity_mod.touch()
    def _server_idle_shutdown():
        threshold = cfg.server_idle_minutes * 60
        if threshold <= 0:
            return
        while True:
            time.sleep(60)
            if _activity_mod.idle_seconds() > threshold:
                print(f"[SERVER] {cfg.server_idle_minutes}분 유휴 — 서버 자동 종료", flush=True)
                # 종료 전 자동종료 대상(⏱) 터널 차단 — 서버 부재 중 무감시 노출 방지.
                # 영구(🔒) 터널은 사용자 의도이므로 유지.
                from src.tunnel import close_idle_tunnels
                closed = close_idle_tunnels("서버 유휴 종료")
                if closed:
                    print(f"[SERVER] 유휴 터널 차단: {closed}", flush=True)
                iol = tornado.ioloop.IOLoop.current()
                iol.add_callback(iol.stop)
                break
    threading.Thread(target=_server_idle_shutdown, daemon=True, name="server-idle-shutdown").start()

    # 세션 로그 정리 스레드
    if cfg.session_log_enabled:
        def _log_cleanup_loop():
            while True:
                time.sleep(3600)
                _cleanup_old_logs(cfg)
        t = threading.Thread(target=_log_cleanup_loop, daemon=True, name="log-cleanup")
        t.start()

    # 8. Telegram/Slack 봇 — 독립 서브프로세스로 실행 (start_new_session=True)
    import subprocess as _subprocess
    _bot_daemon_script = Path(__file__).parent.parent / "telegram_daemon.py"
    _TG_PID_FILE = CERBERUS_DIR / "telegram.pid"
    if cfg.tg_token and _bot_daemon_script.exists():
        _should_spawn = True
        if _TG_PID_FILE.exists():
            try:
                _old_pid = int(_TG_PID_FILE.read_text().strip())
                from src.tunnel import _is_alive as _pid_alive
                if _pid_alive(_old_pid):
                    print(f"[TG] 텔레그램 봇 이미 실행 중 (PID {_old_pid})", flush=True)
                    _should_spawn = False
            except ValueError:
                pass  # 파일 내용 오류 → 새로 시작
        if _should_spawn:
            _proc = _subprocess.Popen(
                [
                    sys.executable, str(_bot_daemon_script),
                    "--tg-token", cfg.tg_token,
                    "--tg-chat-id", str(cfg.tg_chat_id),
                    "--port", str(cfg.port),
                    "--lang", getattr(cfg, "lang", ""),
                ],
                start_new_session=True,
            )
            _TG_PID_FILE.write_text(str(_proc.pid))
            print(f"[TG] 텔레그램 봇 서브프로세스 시작 (PID {_proc.pid})", flush=True)

    if cfg.slack_bot_token and cfg.slack_app_token:
        from src.bots.slack_bot import _run_slack_bot
        t = threading.Thread(target=_run_slack_bot, args=(cfg,), daemon=True, name="slack-bot")
        t.start()

    # 9. Ctrl+C 핸들링 — 터널/봇은 독립 프로세스이므로 서버 종료 시 닫지 않음
    def _shutdown(sig, frame):
        print("\n[SERVER] 종료 중...", flush=True)
        tornado.ioloop.IOLoop.current().add_callback_from_signal(
            tornado.ioloop.IOLoop.current().stop
        )

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    tornado.ioloop.IOLoop.current().start()
