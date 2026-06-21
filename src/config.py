"""
src/config.py — Config dataclass, load_config, parse_args
우선순위: CLI 인자 > OS 환경변수 > .env 파일 > 기본값
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

CERBERUS_DIR = Path.home() / ".cerberus"


def _load_dotenv() -> None:
    """.env(없으면 무시)를 환경변수로 로드.
    이미 설정된 OS 환경변수가 우선(.env 는 미설정 키만 채움).
    경로는 CB_ENV_FILE 로 변경 가능. 외부 의존성 없는 최소 파서."""
    path = Path(os.getenv("CB_ENV_FILE", ".env"))
    if not path.is_file():
        return
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:
        pass


_load_dotenv()


@dataclass
class Config:
    # [server]
    port: int = 8765
    idle_timeout: int = 5           # 분, 0=비활성

    # [auth]
    session_hours: int = 1
    max_attempts: int = 5
    window_minutes: int = 5
    lockout_minutes: int = 15

    # [tunnel]
    tunnel_mode: str = "quick"
    tunnel_name: str = ""
    tunnel_domain: str = ""

    # [agent]
    agent_enabled: bool = True

    # [files]
    files_root: Path = field(default_factory=lambda: Path.home())
    files_exclude: list = field(default_factory=lambda: ["~/.cerberus", "~/.ssh"])
    files_hidden: bool = False
    max_upload_bytes: int = 104857600  # 100MB

    # [terminal]
    terminal_enabled: bool = False
    terminal_shell: str = "/bin/bash"
    max_sessions: int = 50

    # [session_log]
    session_log_enabled: bool = True
    session_log_dir: str = "~/.cerberus/sessions"
    session_log_filename: str = "{session_id}_{date}.log"
    session_log_max_size_mb: int = 50
    session_log_max_age_days: int = 7

    # [metrics]
    metrics_disk_path: str = ""

    # [telegram]
    tg_token: str = ""
    tg_chat_id: int = 0

    # [slack]
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_channel: str = "#general"

    # [ui]
    lang: str = ""                   # "" = OS 자동 감지, "ko"|"en" 명시 가능
    server_idle_minutes: int = 5     # 서버 자동 종료 유휴 임계값 (분, 0=비활성)

    # [macros] — list of {"label": str, "send": str}
    macros: list = field(default_factory=lambda: [
        {"label": "ls -la",     "send": "ls -la\n"},
        {"label": "pwd",        "send": "pwd\n"},
        {"label": "git status", "send": "git status\n"},
        {"label": "git log -10","send": "git log --oneline -10\n"},
        {"label": "ps aux",     "send": "ps aux | head -20\n"},
        {"label": "df -h",      "send": "df -h\n"},
        {"label": "free -h",    "send": "free -h\n"},
        {"label": "python3",    "send": "python3\n"},
        {"label": "C-c",        "send": "\x03"},
        {"label": "C-d",        "send": "\x04"},
    ])


def save_config(cfg: "Config") -> None:
    """변경된 설정을 .env 에 저장 (기존 .env 의 주석·순서 유지, 없으면 생성)."""
    env_path = Path(os.getenv("CB_ENV_FILE", ".env"))
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []

    updates = {
        "CB_PORT": str(cfg.port),
        "CB_IDLE_TIMEOUT": str(cfg.idle_timeout),
        "CB_SESSION_HOURS": str(cfg.session_hours),
        "CB_LANG": cfg.lang,
        "CB_TG_TOKEN": cfg.tg_token,
        "CB_TG_CHAT_ID": str(cfg.tg_chat_id),
        "CB_TERMINAL_SHELL": cfg.terminal_shell,
        "CB_SERVER_IDLE_MINUTES": str(cfg.server_idle_minutes),
    }

    updated: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.partition("=")[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            updated.add(key)
        else:
            new_lines.append(line)

    for key, val in updates.items():
        if key not in updated:
            new_lines.append(f"{key}={val}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def load_config(cli_args: argparse.Namespace) -> Config:
    """CLI > OS env (+ .env) > 기본값 우선순위로 Config 반환.
    .env 는 모듈 로드 시 _load_dotenv() 가 이미 os.environ 에 반영한 상태."""
    cfg = Config()

    e = os.environ.get

    # server
    if e("CB_PORT"):           cfg.port            = int(e("CB_PORT"))
    if e("CB_IDLE_TIMEOUT"):   cfg.idle_timeout     = int(e("CB_IDLE_TIMEOUT"))
    # auth
    if e("CB_SESSION_HOURS"):  cfg.session_hours    = int(e("CB_SESSION_HOURS"))
    if e("CB_MAX_ATTEMPTS"):   cfg.max_attempts     = int(e("CB_MAX_ATTEMPTS"))
    if e("CB_WINDOW_MINUTES"): cfg.window_minutes   = int(e("CB_WINDOW_MINUTES"))
    if e("CB_LOCKOUT_MINUTES"):cfg.lockout_minutes  = int(e("CB_LOCKOUT_MINUTES"))
    # tunnel
    if e("CB_TUNNEL_MODE"):    cfg.tunnel_mode      = e("CB_TUNNEL_MODE")
    if e("CB_TUNNEL_NAME"):    cfg.tunnel_name      = e("CB_TUNNEL_NAME")
    if e("CB_TUNNEL_DOMAIN"):  cfg.tunnel_domain    = e("CB_TUNNEL_DOMAIN")
    # files
    if e("CB_FILES_ROOT"):     cfg.files_root       = Path(os.path.expanduser(e("CB_FILES_ROOT")))
    if e("CB_FILES_HIDDEN") is not None:
        cfg.files_hidden = e("CB_FILES_HIDDEN") not in ("0", "false", "")
    if e("CB_FILES_EXCLUDE"):
        cfg.files_exclude = [p.strip() for p in e("CB_FILES_EXCLUDE").split(",") if p.strip()]
    if e("CB_MAX_UPLOAD_BYTES"):cfg.max_upload_bytes= int(e("CB_MAX_UPLOAD_BYTES"))
    # terminal
    if e("CB_TERMINAL") is not None:
        cfg.terminal_enabled = e("CB_TERMINAL") not in ("0", "false", "")
    if e("CB_TERMINAL_SHELL"): cfg.terminal_shell   = e("CB_TERMINAL_SHELL")
    if e("CB_MAX_SESSIONS"):   cfg.max_sessions     = int(e("CB_MAX_SESSIONS"))
    # agent
    if e("CB_AGENT") is not None:
        cfg.agent_enabled = e("CB_AGENT") not in ("0", "false", "")
    # session_log
    if e("CB_SESSION_LOG") is not None:
        cfg.session_log_enabled = e("CB_SESSION_LOG") not in ("0", "false", "")
    if e("CB_SESSION_LOG_DIR"):     cfg.session_log_dir         = e("CB_SESSION_LOG_DIR")
    if e("CB_SESSION_LOG_MAX_MB"):  cfg.session_log_max_size_mb = int(e("CB_SESSION_LOG_MAX_MB"))
    if e("CB_SESSION_LOG_MAX_DAYS"):cfg.session_log_max_age_days= int(e("CB_SESSION_LOG_MAX_DAYS"))
    # metrics
    if e("CB_METRICS_DISK_PATH"): cfg.metrics_disk_path = e("CB_METRICS_DISK_PATH")
    # telegram
    if e("CB_TG_TOKEN"):       cfg.tg_token         = e("CB_TG_TOKEN")
    if e("CB_TG_CHAT_ID"):     cfg.tg_chat_id       = int(e("CB_TG_CHAT_ID"))
    # slack
    if e("CB_SLACK_BOT"):      cfg.slack_bot_token  = e("CB_SLACK_BOT")
    if e("CB_SLACK_APP"):      cfg.slack_app_token  = e("CB_SLACK_APP")
    if e("CB_SLACK_CHANNEL"):  cfg.slack_channel    = e("CB_SLACK_CHANNEL")
    # ui
    if e("CB_LANG"):           cfg.lang             = e("CB_LANG")
    if e("CB_SERVER_IDLE_MINUTES"): cfg.server_idle_minutes = int(e("CB_SERVER_IDLE_MINUTES"))

    # CLI 인자 오버라이드
    a = cli_args
    if getattr(a, 'port',          None) is not None: cfg.port            = a.port
    if getattr(a, 'idle_timeout',  None) is not None: cfg.idle_timeout    = a.idle_timeout
    if getattr(a, 'session_hours', None) is not None: cfg.session_hours   = a.session_hours
    if getattr(a, 'files_root',    None) is not None: cfg.files_root      = a.files_root
    if getattr(a, 'terminal',      False):             cfg.terminal_enabled = True
    if getattr(a, 'tg_token',      None) is not None: cfg.tg_token        = a.tg_token
    if getattr(a, 'tg_chat_id',    None) is not None: cfg.tg_chat_id      = a.tg_chat_id
    if getattr(a, 'slack_bot_token',None) is not None:cfg.slack_bot_token = a.slack_bot_token
    if getattr(a, 'slack_app_token',None) is not None:cfg.slack_app_token = a.slack_app_token
    if getattr(a, 'slack_channel', None) is not None: cfg.slack_channel   = a.slack_channel
    if getattr(a, 'lang',          None) is not None: cfg.lang            = a.lang

    return cfg


def parse_args() -> argparse.Namespace:
    """CLI 인자 파싱."""
    p = argparse.ArgumentParser(description="Cerberus Beacon")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--idle-timeout", type=int, default=None, metavar="MINUTES")
    p.add_argument("--session-hours", type=int, default=None)
    p.add_argument("--files-root", type=Path, default=None)
    p.add_argument("--terminal", action="store_true", default=False)
    p.add_argument("--rotate-token", action="store_true", default=False,
                   help="API 토큰 재생성 (기존 토큰 즉시 무효화)")
    p.add_argument("--show-token", action="store_true", default=False,
                   help="현재 API 토큰 출력")
    p.add_argument("--init", action="store_true", default=False,
                   help=".env.sample → .env 복사 (이미 있으면 건너뜀)")
    p.add_argument("--tg-token", metavar="BOT_TOKEN", default=None)
    p.add_argument("--tg-chat-id", type=int, metavar="CHAT_ID", default=None)
    p.add_argument("--slack-bot-token", metavar="xoxb-...", default=None)
    p.add_argument("--slack-app-token", metavar="xapp-...", default=None)
    p.add_argument("--slack-channel", default=None)
    p.add_argument("--lang", choices=["ko", "en"], default=None,
                   help="UI 언어 (ko|en). 생략 시 OS 로케일 자동 감지")
    return p.parse_args()
