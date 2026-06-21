"""
tests/test_config.py — 기본값, 환경변수, CLI 오버라이드
SPEC.md §7
"""
import argparse
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import src.config as config
from src.config import Config, load_config


# ══════════════════════════════════════════════════════════════════════════════
# 9. Config 로드
# ══════════════════════════════════════════════════════════════════════════════

class TestConfig:
    def _make_args(self, **kwargs):
        defaults = dict(
            port=None, idle_timeout=None, session_hours=None,
            files_root=None, terminal=False,
            tg_token=None, tg_chat_id=None,
            slack_bot_token=None, slack_app_token=None, slack_channel=None,
        )
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_defaults(self):
        """환경변수 없음 + CLI 없음 → 기본값 확인."""
        args = self._make_args()
        env_vars = ["CB_PORT", "CB_IDLE_TIMEOUT", "CB_SESSION_HOURS", "CB_FILES_ROOT",
                    "CB_TG_TOKEN", "CB_TG_CHAT_ID", "CB_SLACK_BOT", "CB_SLACK_APP", "CB_SLACK_CHANNEL"]
        clean_env = {k: v for k, v in os.environ.items() if k not in env_vars}

        with patch.dict(os.environ, clean_env, clear=True), \
             patch.object(config, "CERBERUS_DIR", Path("/nonexistent/cerberus")):
            cfg = load_config(args)

        assert cfg.port == 8765
        assert cfg.idle_timeout == 5
        assert cfg.session_hours == 1

    def test_env_vars_override_defaults(self):
        """CB_TG_TOKEN 환경변수 → cfg.tg_token 반영."""
        args = self._make_args()
        with patch.dict(os.environ, {"CB_TG_TOKEN": "test-bot-token"}), \
             patch.object(config, "CERBERUS_DIR", Path("/nonexistent/cerberus")):
            cfg = load_config(args)

        assert cfg.tg_token == "test-bot-token"

    def test_cli_overrides_env(self):
        """--port 9000 → cfg.port == 9000."""
        args = self._make_args(port=9000)
        with patch.dict(os.environ, {"CB_PORT": "8000"}), \
             patch.object(config, "CERBERUS_DIR", Path("/nonexistent/cerberus")):
            cfg = load_config(args)

        assert cfg.port == 9000

    def test_terminal_flag_sets_enabled(self):
        """--terminal → cfg.terminal_enabled == True."""
        args = self._make_args(terminal=True)
        with patch.object(config, "CERBERUS_DIR", Path("/nonexistent/cerberus")):
            cfg = load_config(args)
        assert cfg.terminal_enabled is True

    def test_new_env_vars_applied(self):
        """신규 환경변수(agent/세션로그/max_sessions) load_config 반영."""
        args = self._make_args()
        env = {
            "CB_AGENT": "0",
            "CB_MAX_SESSIONS": "10",
            "CB_SESSION_LOG": "0",
            "CB_SESSION_LOG_DIR": "/tmp/cb_logs",
            "CB_SESSION_LOG_MAX_MB": "20",
            "CB_SESSION_LOG_MAX_DAYS": "3",
            "CB_SERVER_IDLE_MINUTES": "7",
        }
        with patch.dict(os.environ, env), \
             patch.object(config, "CERBERUS_DIR", Path("/nonexistent/cerberus")):
            cfg = load_config(args)
        assert cfg.agent_enabled is False
        assert cfg.max_sessions == 10
        assert cfg.session_log_enabled is False
        assert cfg.session_log_dir == "/tmp/cb_logs"
        assert cfg.session_log_max_size_mb == 20
        assert cfg.session_log_max_age_days == 3
        assert cfg.server_idle_minutes == 7

    def test_default_macros_present(self):
        """Config 기본 매크로 존재 확인."""
        cfg = Config()
        assert len(cfg.macros) > 0
        labels = [m["label"] for m in cfg.macros]
        assert "ls -la" in labels
        assert "C-c" in labels
