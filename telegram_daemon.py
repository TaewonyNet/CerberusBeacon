#!/usr/bin/env python3
"""
telegram_daemon.py — Cerberus Telegram 봇 독립 실행 진입점

실행:
  python3 telegram_daemon.py --tg-token <TOKEN> --tg-chat-id <ID> [--port 8766]

서버(main.py)와 별개 프로세스로 실행. 서버가 꺼져도 봇은 유지됨.
"""
from __future__ import annotations

import argparse
import sys


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Cerberus Telegram Bot Daemon")
    p.add_argument("--tg-token",   required=True, help="Telegram 봇 토큰")
    p.add_argument("--tg-chat-id", type=int, default=0, help="허용 채팅 ID (0=전체)")
    p.add_argument("--port",       type=int, default=8766, help="Cerberus 서버 포트 (기본 8766)")
    p.add_argument("--lang",       default="", help="언어 코드 (ko/en, 기본값=OS 자동감지)")
    return p.parse_args()


def main() -> None:
    args = _parse()

    # 최소한의 Config-like 객체 (서버 config.toml 불필요)
    class _Cfg:
        tg_token = args.tg_token
        tg_chat_id = args.tg_chat_id
        port = args.port
        lang = args.lang
        idle_timeout = 5  # /idle 명령 기본값; API 조회 성공 시 갱신됨

    from src.bots.telegram_bot import _run_telegram_bot
    try:
        _run_telegram_bot(_Cfg())
    except KeyboardInterrupt:
        print("\n[TG] 종료", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
