"""
src/auth.py — TOTP, brute-force, AuthMixin, LoginHandler, LogoutHandler, HealthHandler
SPEC.md §1
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from pathlib import Path
import tornado.web

try:
    import pyotp
except ImportError:
    import sys
    sys.exit("pip install pyotp")

try:
    import qrcode
    _HAS_QRCODE = True
except ImportError:
    _HAS_QRCODE = False

from src.config import CERBERUS_DIR, Config

# ── 파일 경로 ────────────────────────────────────────────────────────────────
_OTP_SECRET_FILE = CERBERUS_DIR / "otp_secret"
_AGENT_OTP_SECRET_FILE = CERBERUS_DIR / "agent_otp_secret"

# ── 쿠키 설정 ─────────────────────────────────────────────────────────────────
_COOKIE_SECRET: str = secrets.token_hex(32)   # 런타임에만 보관
_COOKIE_NAME = "cb_session"
_DEVICE_COOKIE_NAME = "cb_device"
_SERVER_START_TIME: float = time.time()

# ── Brute-force 방지: IP별 (attempts, window_start, locked_until) ─────────────
_bf_counters: dict[str, dict] = {}
_bf_lock = threading.Lock()


def ensure_otp_secret() -> str:
    """~/.cerberus/otp_secret 읽기 또는 신규 생성 (권한 0600)."""
    CERBERUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _OTP_SECRET_FILE.exists():
        return _OTP_SECRET_FILE.read_text().strip()
    secret = pyotp.random_base32()
    _OTP_SECRET_FILE.write_text(secret)
    _OTP_SECRET_FILE.chmod(0o600)
    return secret


def ensure_agent_otp_secret() -> str:
    """~/.cerberus/agent_otp_secret 읽기 또는 신규 생성 (권한 0600).
    브라우저 로그인용 otp_secret과 분리 — 에이전트 전용 2FA 시크릿."""
    CERBERUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _AGENT_OTP_SECRET_FILE.exists():
        return _AGENT_OTP_SECRET_FILE.read_text().strip()
    secret = pyotp.random_base32()
    _AGENT_OTP_SECRET_FILE.write_text(secret)
    _AGENT_OTP_SECRET_FILE.chmod(0o600)
    return secret


def print_agent_qr(secret: str) -> None:
    """에이전트 OTP 시크릿 QR 출력 (브라우저용과 별도 등록 필요)."""
    uri = pyotp.TOTP(secret).provisioning_uri(
        name="CerberusBeacon-Agent", issuer_name="CerberusBeacon"
    )
    print(f"\n🤖 Agent OTP URI: {uri}")
    if _HAS_QRCODE:
        qr = qrcode.QRCode()
        qr.add_data(uri)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    print()


def verify_agent_totp(code: str, secret: str) -> bool:
    """에이전트 OTP 코드 검증."""
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def ensure_device_key() -> str:
    """~/.cerberus/device_key 읽기 또는 신규 생성 (권한 0600)."""
    CERBERUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    device_key_file = CERBERUS_DIR / "device_key"
    if device_key_file.exists():
        return device_key_file.read_text().strip()
    key = secrets.token_hex(32)
    device_key_file.write_text(key)
    device_key_file.chmod(0o600)
    return key


def print_qr(secret: str) -> None:
    """otpauth:// URI 출력. qrcode 있으면 ASCII QR도 출력."""
    uri = pyotp.TOTP(secret).provisioning_uri(name="CerberusBeacon", issuer_name="CerberusBeacon")
    print(f"\n🔑 OTP URI: {uri}")
    if _HAS_QRCODE:
        qr = qrcode.QRCode()
        qr.add_data(uri)
        qr.make(fit=True)
        qr.print_ascii(invert=True)
    print()


def verify_totp(code: str, secret: str) -> bool:
    """pyotp.TOTP(secret).verify(code, valid_window=1) 반환."""
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def _check_brute_force(ip: str, cfg: Config) -> tuple[bool, int]:
    """(잠금중, 남은 초) 반환. 잠금 중이면 True."""
    now = time.time()
    with _bf_lock:
        rec = _bf_counters.get(ip)
        if rec is None:
            return False, 0
        locked_until = rec.get("locked_until", 0)
        if locked_until and now < locked_until:
            return True, int(locked_until - now)
        # window 만료 시 초기화
        window_start = rec.get("window_start", 0)
        if now - window_start > cfg.window_minutes * 60:
            _bf_counters.pop(ip, None)
            return False, 0
        return False, 0


def _prune_bf_counters(now: float, cfg: Config) -> None:
    """만료된 IP 카운터 제거 (잠금 해제 + window 경과). _bf_lock 보유 상태에서 호출.
    실패 후 다시 접속하지 않는 IP의 무한 누적 방지."""
    window_sec = cfg.window_minutes * 60
    expired = [
        ip for ip, rec in _bf_counters.items()
        if now >= rec.get("locked_until", 0)
        and now - rec.get("window_start", 0) > window_sec
    ]
    for ip in expired:
        _bf_counters.pop(ip, None)


def _record_failure(ip: str, cfg: Config) -> bool:
    """실패 기록. 잠금 발생 시 True 반환."""
    now = time.time()
    with _bf_lock:
        _prune_bf_counters(now, cfg)
        rec = _bf_counters.setdefault(ip, {"attempts": 0, "window_start": now, "locked_until": 0})
        if now - rec["window_start"] > cfg.window_minutes * 60:
            rec["attempts"] = 0
            rec["window_start"] = now
        rec["attempts"] += 1
        if rec["attempts"] >= cfg.max_attempts:
            rec["locked_until"] = now + cfg.lockout_minutes * 60
            return True
        return False


def _reset_failure(ip: str) -> None:
    """로그인 성공 시 실패 기록 초기화."""
    with _bf_lock:
        _bf_counters.pop(ip, None)


# ── LOGIN PAGE HTML ────────────────────────────────────────────────────────────
_LOGIN_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Cerberus — Login</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#c9d1d9;font-family:'Segoe UI',system-ui,sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;
      padding:2rem;width:340px;max-width:95vw}
h1{font-size:1.4rem;margin-bottom:1.5rem;text-align:center;color:#58a6ff}
.logo{text-align:center;font-size:2.5rem;margin-bottom:0.5rem}
label{display:block;font-size:0.85rem;color:#8b949e;margin-bottom:0.3rem}
input[type=text]{width:100%;padding:0.6rem 0.8rem;background:#0d1117;
                  border:1px solid #30363d;border-radius:6px;color:#c9d1d9;
                  font-size:1.2rem;letter-spacing:0.3rem;text-align:center;outline:none}
input[type=text]:focus{border-color:#58a6ff}
button{width:100%;padding:0.7rem;margin-top:1rem;background:#238636;
       border:none;border-radius:6px;color:#fff;font-size:1rem;cursor:pointer}
button:hover{background:#2ea043}
.error{background:#3d1a1a;border:1px solid #f85149;color:#f85149;
       border-radius:6px;padding:0.6rem;margin-bottom:1rem;font-size:0.9rem;text-align:center}
.lockout{background:#2d2a1a;border:1px solid #d29922;color:#d29922;
         border-radius:6px;padding:0.6rem;margin-bottom:1rem;font-size:0.9rem;text-align:center}
.hint{font-size:0.8rem;color:#6e7681;text-align:center;margin-top:1rem}
</style>
</head>
<body>
<div class="card">
  <div class="logo">🐺</div>
  <h1>Cerberus Beacon</h1>
  {{message}}
  <form method="POST" action="/login">
    <label for="code">OTP 6자리</label>
    <input type="text" id="code" name="code" maxlength="6" pattern="[0-9]{6}"
           autocomplete="one-time-code" inputmode="numeric" autofocus
           placeholder="000000" {{disabled}}>
    <button type="submit" {{disabled}}>로그인</button>
  </form>
  <p class="hint">Google Authenticator / Authy 앱에서 코드를 입력하세요</p>
</div>
</body>
</html>"""


class AuthMixin:
    """Tornado handler mixin: 세션 없으면 /login 리다이렉트."""

    def get_current_user(self):
        return self.get_secure_cookie(_COOKIE_NAME)

    def prepare(self):
        if not self.get_current_user():
            if self.request.headers.get("Upgrade", "").lower() == "websocket":
                self.set_status(401)
                self.finish()
            else:
                self.redirect("/login")
            return
        import src.activity as _activity
        _activity.touch()
        from src.tunnel import touch_tunnels
        touch_tunnels()


class LoginHandler(tornado.web.RequestHandler):
    """GET /login → OTP 입력 폼. POST /login → 검증 후 쿠키 발급."""

    def initialize(self, otp_secret: str, cfg: Config, device_key: str) -> None:
        self._secret = otp_secret
        self._cfg = cfg
        self._device_key = device_key

    def _render(self, message_html: str = "", disabled: str = "") -> None:
        html = _LOGIN_HTML.replace("{{message}}", message_html).replace("{{disabled}}", disabled)
        self.write(html)

    def get(self):
        ip = self.request.remote_ip
        locked, remaining = _check_brute_force(ip, self._cfg)
        if locked:
            mins = remaining // 60
            secs = remaining % 60
            msg = f'<div class="lockout">🔒 잠금 중 — {mins}분 {secs}초 후 해제됩니다</div>'
            self._render(msg, "disabled")
            return
        # cb_device 쿠키 확인 (Named Tunnel / 로컬)
        if self._should_skip_totp():
            self._issue_session()
            self.redirect("/")
            return
        error = self.get_argument("error", "")
        msg = ""
        if error == "1":
            msg = '<div class="error">❌ OTP 코드가 올바르지 않습니다</div>'
        self._render(msg)

    def post(self):
        ip = self.request.remote_ip
        locked, remaining = _check_brute_force(ip, self._cfg)
        if locked:
            mins = remaining // 60
            secs = remaining % 60
            msg = f'<div class="lockout">🔒 잠금 중 — {mins}분 {secs}초 후 해제됩니다</div>'
            self._render(msg, "disabled")
            return

        code = self.get_argument("code", "").strip()
        if verify_totp(code, self._secret):
            _reset_failure(ip)
            self._issue_session()
            # Named Tunnel 또는 로컬 접속 시 cb_device 발급
            if self._is_named_or_local():
                self._issue_device_cookie()
            self.redirect("/")
        else:
            _record_failure(ip, self._cfg)
            self.redirect("/login?error=1")

    def _should_skip_totp(self) -> bool:
        """cb_device 쿠키가 유효하면 TOTP 스킵 (Named Tunnel/로컬만)."""
        if not self._is_named_or_local():
            return False
        device_cookie = self.get_cookie(_DEVICE_COOKIE_NAME, "")
        if not device_cookie:
            return False
        return self._verify_device_cookie(device_cookie)

    def _is_named_or_local(self) -> bool:
        """Named Tunnel 모드 또는 로컬 접속이면 True."""
        if self._cfg.tunnel_mode == "named":
            return True
        ip = self.request.remote_ip
        return ip in ("127.0.0.1", "::1", "localhost")

    def _verify_device_cookie(self, value: str) -> bool:
        """cb_device 쿠키 서명 검증."""
        try:
            parts = value.split(".")
            if len(parts) != 2:
                return False
            payload_b64, sig = parts
            expected = hashlib.sha256(
                (self._device_key + "." + payload_b64).encode()
            ).hexdigest()[:32]
            if not secrets.compare_digest(sig, expected):
                return False
            payload = json.loads(base64.b64decode(payload_b64 + "==").decode())
            return payload.get("exp", 0) > time.time()
        except Exception:
            return False

    def _issue_session(self) -> None:
        expires_days = self._cfg.session_hours / 24.0
        self.set_secure_cookie(
            _COOKIE_NAME, "1",
            expires_days=expires_days,
            httponly=True,
            samesite="Strict",
        )

    def _issue_device_cookie(self) -> None:
        exp = time.time() + 30 * 24 * 3600  # 30일
        payload = base64.b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        sig = hashlib.sha256(
            (self._device_key + "." + payload).encode()
        ).hexdigest()[:32]
        value = f"{payload}.{sig}"
        self.set_cookie(
            _DEVICE_COOKIE_NAME, value,
            expires_days=30,
            httponly=True,
            samesite="Strict",
        )


class LogoutHandler(tornado.web.RequestHandler):
    """GET /logout → cb_session + cb_device 삭제 → /login 리다이렉트."""

    def get(self):
        self.clear_cookie(_COOKIE_NAME)
        self.clear_cookie(_DEVICE_COOKIE_NAME)
        self.redirect("/login")


class HealthHandler(tornado.web.RequestHandler):
    """GET /health → {"status":"ok","uptime_sec":N} (인증 없음)."""

    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({
            "status": "ok",
            "uptime_sec": int(time.time() - _SERVER_START_TIME)
        }))
