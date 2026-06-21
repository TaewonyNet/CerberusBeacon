"""
src/bots/slack_bot.py — _run_slack_bot
slack_bolt SocketModeHandler 사용
SPEC.md §2
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Config

_SL_STRINGS: dict[str, dict[str, str]] = {
    "ko": {
        "usage":        "사용법: /cerberus open|close|status|idle [값]",
        "idle_status":  "유휴 타임아웃: {label}",
        "idle_unit":    "{n}분",
        "idle_disabled":"비활성",
        "idle_set":     "✅ 유휴 타임아웃: {label}",
        "unknown_cmd":  "알 수 없는 명령",
    },
    "en": {
        "usage":        "Usage: /cerberus open|close|status|idle [value]",
        "idle_status":  "Idle timeout: {label}",
        "idle_unit":    "{n} min",
        "idle_disabled":"disabled",
        "idle_set":     "✅ Idle timeout: {label}",
        "unknown_cmd":  "Unknown command",
    },
}


def _run_slack_bot(cfg: "Config") -> None:
    """slack_bolt SocketModeHandler 사용."""
    try:
        from slack_bolt import App as SlackApp  # type: ignore
        from slack_bolt.adapter.socket_mode import SocketModeHandler  # type: ignore
    except ImportError:
        sys.exit("uv sync --extra slack  (또는 pip install slack-bolt slack-sdk)")

    from src.i18n import resolve_lang
    lang = resolve_lang(getattr(cfg, "lang", ""))
    T = _SL_STRINGS.get(lang, _SL_STRINGS["en"])

    # 런타임에 import해서 순환 참조 회피
    from src.tunnel import (
        open_tunnel,
        close_tunnel,
        close_all_tunnels,
        tunnel_status_text,
        _notify_all,
        _notifiers,
        _notifiers_lock,
    )
    import src.tunnel as _tunnel_mod

    slack_app = SlackApp(token=cfg.slack_bot_token)

    def _reply(say, msg: str):
        say(msg)
        _notify_all(msg, exclude="slack")

    @slack_app.command("/cerberus")
    def handle_cerberus(ack, say, command):
        ack()
        text = command.get("text", "").strip()
        parts = text.split()
        if not parts:
            say(T["usage"])
            return
        sub = parts[0]
        args = parts[1:]
        if sub == "open":
            port = int(args[0]) if args else cfg.port
            _reply(say, open_tunnel(port))
        elif sub == "close":
            if args and args[0] == "all":
                _reply(say, close_all_tunnels("Slack"))
            elif args:
                _reply(say, close_tunnel(int(args[0]), "Slack"))
            else:
                _reply(say, close_all_tunnels("Slack"))
        elif sub == "status":
            say(tunnel_status_text())
        elif sub == "idle":
            if args:
                minutes = max(0, min(1440, int(args[0])))
                _tunnel_mod._idle_timeout_minutes = minutes
                cfg.idle_timeout = minutes
                try:
                    from src.config import save_config
                    save_config(cfg)
                except Exception:
                    pass
                label = T["idle_unit"].format(n=minutes) if minutes > 0 else T["idle_disabled"]
                say(T["idle_set"].format(label=label))
            else:
                minutes = _tunnel_mod._idle_timeout_minutes
                label = T["idle_unit"].format(n=minutes) if minutes > 0 else T["idle_disabled"]
                say(T["idle_status"].format(label=label))
        else:
            say(T["unknown_cmd"])

    with _notifiers_lock:
        def slack_notify(msg):
            try:
                from slack_sdk import WebClient  # type: ignore
                WebClient(token=cfg.slack_bot_token).chat_postMessage(
                    channel=cfg.slack_channel, text=msg
                )
            except Exception:
                pass
        _notifiers.append(("slack", slack_notify))

    handler = SocketModeHandler(slack_app, cfg.slack_app_token)
    handler.start()
