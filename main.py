#!/usr/bin/env python3
"""
Cerberus Beacon v2 — 멀티파일 프로젝트 진입점

실행:
  python3 main.py
  python3 main.py --terminal --port 8765 --idle-timeout 10
"""
from __future__ import annotations

import secrets
import sys

from src.config import load_config, parse_args, CERBERUS_DIR


def main() -> None:
    args = parse_args()

    # --init 처리
    if hasattr(args, 'init') and args.init:
        from src.web import _init_config_template
        _init_config_template()
        sys.exit(0)

    # --show-token 처리
    if hasattr(args, 'show_token') and args.show_token:
        from src.agent import _API_TOKEN_FILE
        if _API_TOKEN_FILE.exists():
            print(_API_TOKEN_FILE.read_text().strip())
        else:
            print("토큰 파일 없음. 서버를 먼저 실행하세요.")
        sys.exit(0)

    # --rotate-token 처리
    if hasattr(args, 'rotate_token') and args.rotate_token:
        from src.agent import _API_TOKEN_FILE
        CERBERUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        token = secrets.token_hex(32)
        _API_TOKEN_FILE.write_text(token)
        _API_TOKEN_FILE.chmod(0o600)
        print(f"새 토큰: {token}")
        sys.exit(0)

    cfg = load_config(args)

    # 최초 실행 또는 lang 미설정 시 OS 언어 자동 감지 + config 저장
    from src.i18n import detect_lang, resolve_lang, SUPPORTED_LANGS
    if not cfg.lang:
        cfg.lang = detect_lang()
        lang_name = {"ko": "한국어", "en": "English"}.get(cfg.lang, cfg.lang)
        print(f"[i18n] 언어 감지: {lang_name} (.env CB_LANG=ko|en 으로 고정 가능)", flush=True)

    from src.web import run_server
    run_server(cfg)


if __name__ == "__main__":
    main()
