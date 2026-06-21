"""
tests/test_agent.py — API 토큰, 인증
SPEC.md §7
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.agent as agent
import src.auth as auth


# ══════════════════════════════════════════════════════════════════════════════
# 11. 에이전트 API (SPEC.md §7)
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentApi:
    def setup_method(self):
        agent._agent_api_token = "test-token-abc"

    def test_ensure_api_token_creates_file(self, tmp_path):
        """파일 없으면 생성, 0600 권한."""
        token_file = tmp_path / "api_token"
        with patch.object(agent, "CERBERUS_DIR", tmp_path), \
             patch.object(agent, "_API_TOKEN_FILE", token_file):
            token = agent.ensure_api_token()
        assert token_file.exists()
        assert len(token) > 0
        mode = oct(token_file.stat().st_mode)[-3:]
        assert mode == "600"

    def test_ensure_api_token_reads_existing(self, tmp_path):
        """파일 있으면 그대로 읽기."""
        token_file = tmp_path / "api_token"
        existing = "existing-token-xyz"
        token_file.write_text(existing)
        token_file.chmod(0o600)
        with patch.object(agent, "CERBERUS_DIR", tmp_path), \
             patch.object(agent, "_API_TOKEN_FILE", token_file):
            result = agent.ensure_api_token()
        assert result == existing

    def test_invalid_token_returns_401(self):
        """AgentAuthMixin: 잘못된 토큰 → 401."""
        agent._agent_api_token = "correct-token"
        mock_handler = MagicMock()
        mock_handler.request.headers.get.return_value = "wrong-token"
        mock_handler.get_argument.return_value = ""
        mock_handler.set_status = MagicMock()
        mock_handler.write = MagicMock()
        mock_handler.finish = MagicMock()
        mock_handler.set_header = MagicMock()

        mixin = agent.AgentAuthMixin()
        mixin.request = mock_handler.request
        mixin.get_argument = mock_handler.get_argument
        mixin.set_status = mock_handler.set_status
        mixin.set_header = mock_handler.set_header
        mixin.write = mock_handler.write
        mixin.finish = mock_handler.finish
        # 세션 쿠키 없음
        mixin.get_secure_cookie = MagicMock(return_value=None)

        mixin.prepare()
        mock_handler.set_status.assert_called_with(401)

    def test_valid_token_passes(self):
        """AgentAuthMixin: 올바른 토큰+OTP → prepare()가 finish() 호출 안 함."""
        agent._agent_api_token = "correct-token"
        agent._agent_otp_secret = "JBSWY3DPEHPK3PXP"

        mixin = agent.AgentAuthMixin()
        mixin.request = MagicMock()
        mixin.request.headers.get.side_effect = lambda k, d="": {
            "X-API-Token": "correct-token",
            "X-OTP": "000000",
        }.get(k, d)
        mixin.get_argument = MagicMock(return_value="")
        mixin.set_status = MagicMock()
        mixin.set_header = MagicMock()
        mixin.write = MagicMock()
        mixin.finish = MagicMock()

        with patch("src.agent.verify_agent_totp", return_value=True):
            mixin.prepare()

        mixin.finish.assert_not_called()

    def test_make_session_id_format(self):
        """세션 ID가 sess_ + 16자리 hex 형식 (64비트 공간)."""
        from src.terminal import _make_session_id
        sid = _make_session_id()
        assert sid.startswith("sess_")
        suffix = sid[len("sess_"):]
        assert len(suffix) == 16
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_session_id_unique(self):
        """여러 번 생성해도 세션 ID 중복 없음."""
        from src.terminal import _make_session_id
        ids = {_make_session_id() for _ in range(100)}
        assert len(ids) == 100
