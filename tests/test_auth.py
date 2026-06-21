"""
tests/test_auth.py — TOTP, brute-force, 세션, 기기 쿠키
SPEC.md §1
"""
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.auth as auth
from src.config import Config


# ══════════════════════════════════════════════════════════════════════════════
# 1. TOTP 인증 (SPEC.md §1)
# ══════════════════════════════════════════════════════════════════════════════

class TestTotp:
    def test_valid_code_passes(self):
        """pyotp.TOTP로 현재 OTP 생성 → verify_totp 통과."""
        import pyotp
        secret = pyotp.random_base32()
        code = pyotp.TOTP(secret).now()
        assert auth.verify_totp(code, secret) is True

    def test_expired_code_fails(self):
        """31초 전 코드는 valid_window=1 밖 → 거부."""
        import pyotp
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        old_code = totp.at(time.time() - 60)
        assert auth.verify_totp(old_code, secret) is False

    def test_valid_window_boundary(self):
        """±1 스텝(±30초) 내 코드는 통과."""
        import pyotp
        secret = pyotp.random_base32()
        totp = pyotp.TOTP(secret)
        prev_code = totp.at(time.time() - 29)
        assert auth.verify_totp(prev_code, secret) is True

    def test_ensure_otp_secret_creates_file(self, tmp_path):
        """파일 없으면 생성, 권한 0600 확인."""
        with patch.object(auth, "CERBERUS_DIR", tmp_path), \
             patch.object(auth, "_OTP_SECRET_FILE", tmp_path / "otp_secret"):
            secret = auth.ensure_otp_secret()
        secret_file = tmp_path / "otp_secret"
        assert secret_file.exists()
        assert len(secret) > 0
        mode = oct(secret_file.stat().st_mode)[-3:]
        assert mode == "600"

    def test_ensure_otp_secret_reads_existing(self, tmp_path):
        """파일 있으면 그대로 읽기."""
        existing = "JBSWY3DPEHPK3PXP"
        secret_file = tmp_path / "otp_secret"
        secret_file.write_text(existing)
        secret_file.chmod(0o600)
        with patch.object(auth, "CERBERUS_DIR", tmp_path), \
             patch.object(auth, "_OTP_SECRET_FILE", secret_file):
            result = auth.ensure_otp_secret()
        assert result == existing


# ══════════════════════════════════════════════════════════════════════════════
# 2. 세션 미들웨어 (SPEC.md §1)
# ══════════════════════════════════════════════════════════════════════════════

class TestSession:
    def test_no_cookie_redirects_to_login(self):
        """세션 쿠키 없는 요청 → /login 리다이렉트."""
        mock_handler = MagicMock()
        mock_handler.get_secure_cookie.return_value = None
        mock_handler.request.headers.get.return_value = ""

        mixin = auth.AuthMixin()
        mixin.get_secure_cookie = mock_handler.get_secure_cookie
        mixin.redirect = MagicMock()
        mixin.request = MagicMock()
        mixin.request.headers.get.return_value = ""
        mixin.set_status = MagicMock()
        mixin.finish = MagicMock()

        result = mixin.get_current_user()
        assert result is None

        mixin.prepare()
        mixin.redirect.assert_called_with("/login")

    def test_valid_cookie_allows_access(self):
        """유효한 cb_session 쿠키 → get_current_user()가 truthy 반환."""
        mixin = auth.AuthMixin()
        mixin.get_secure_cookie = MagicMock(return_value=b"1")
        result = mixin.get_current_user()
        assert result  # truthy


# ══════════════════════════════════════════════════════════════════════════════
# 3. 브루트포스 방지 (SPEC.md §1.2)
# ══════════════════════════════════════════════════════════════════════════════

class TestBruteForce:
    def setup_method(self):
        with auth._bf_lock:
            auth._bf_counters.clear()

    def test_lockout_after_max_attempts(self):
        """max_attempts 회 실패 후 잠금."""
        cfg = Config()
        ip = "192.168.1.1"
        for _ in range(cfg.max_attempts - 1):
            locked = auth._record_failure(ip, cfg)
            assert not locked
        locked = auth._record_failure(ip, cfg)
        assert locked

        is_locked, remaining = auth._check_brute_force(ip, cfg)
        assert is_locked
        assert remaining > 0

    def test_reset_clears_counter(self):
        """성공 로그인 시 카운터 초기화."""
        cfg = Config()
        ip = "10.0.0.1"
        for _ in range(3):
            auth._record_failure(ip, cfg)
        auth._reset_failure(ip)
        is_locked, _ = auth._check_brute_force(ip, cfg)
        assert not is_locked

    def test_window_expiry_resets_counter(self):
        """window_minutes 경과 시 카운터 리셋."""
        cfg = Config()
        cfg.window_minutes = 0
        ip = "172.16.0.1"
        for _ in range(cfg.max_attempts):
            auth._record_failure(ip, cfg)

        with auth._bf_lock:
            auth._bf_counters[ip]["window_start"] = time.time() - 3600
            auth._bf_counters[ip]["locked_until"] = 0

        is_locked, _ = auth._check_brute_force(ip, cfg)
        assert not is_locked


# ══════════════════════════════════════════════════════════════════════════════
# 4. device_key (SPEC.md §1.4)
# ══════════════════════════════════════════════════════════════════════════════

class TestDeviceKey:
    def test_creates_device_key(self, tmp_path):
        """device_key 파일 없으면 생성."""
        with patch.object(auth, "CERBERUS_DIR", tmp_path):
            key = auth.ensure_device_key()
        key_file = tmp_path / "device_key"
        assert key_file.exists()
        assert len(key) > 0
        mode = oct(key_file.stat().st_mode)[-3:]
        assert mode == "600"

    def test_reads_existing_device_key(self, tmp_path):
        """device_key 파일 있으면 그대로 읽기."""
        key_file = tmp_path / "device_key"
        existing = "existing-device-key-12345"
        key_file.write_text(existing)
        key_file.chmod(0o600)
        with patch.object(auth, "CERBERUS_DIR", tmp_path):
            result = auth.ensure_device_key()
        assert result == existing
