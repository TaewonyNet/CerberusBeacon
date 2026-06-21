#!/usr/bin/env python3
"""
cerberus_ctl — Cerberus Beacon 에이전트 CLI 클라이언트
실행 중인 Cerberus 서버에 HTTP로 명령을 전송하고 출력을 받는다.

사전 준비:
  서버가 실행 중이어야 하며 아래 두 파일이 있어야 한다.
    ~/.cerberus/api_token        API 토큰
    ~/.cerberus/agent_otp_secret 에이전트 전용 OTP 시크릿

환경변수:
  CERBERUS_URL    서버 주소 (기본: http://127.0.0.1:8765)
  CERBERUS_TOKEN  API 토큰 (기본: ~/.cerberus/api_token 파일에서 읽음)

의존성: websockets>=12 (exec 명령 전용). OTP 계산은 stdlib만 사용.

사용 예:
  python3 cerberus_ctl.py health
  python3 cerberus_ctl.py tunnel status
  python3 cerberus_ctl.py tunnel open 8765
  python3 cerberus_ctl.py tunnel close 8765
  python3 cerberus_ctl.py tunnel lock 8765
  python3 cerberus_ctl.py idle
  python3 cerberus_ctl.py idle 10
  python3 cerberus_ctl.py metrics
  python3 cerberus_ctl.py sessions
  python3 cerberus_ctl.py exec "ls -la"
  python3 cerberus_ctl.py exec --session sess_abc123 "git status" --timeout 5
  python3 cerberus_ctl.py new-session
  python3 cerberus_ctl.py delete-session sess_abc123
  python3 cerberus_ctl.py macros
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── 설정 ──────────────────────────────────────────────────────────────────────

_DEFAULT_URL = "http://127.0.0.1:8765"
_TOKEN_FILE = Path.home() / ".cerberus" / "api_token"
_AGENT_OTP_FILE = Path.home() / ".cerberus" / "agent_otp_secret"


def _get_url() -> str:
    return os.environ.get("CERBERUS_URL", _DEFAULT_URL).rstrip("/")


def _get_token() -> str:
    t = os.environ.get("CERBERUS_TOKEN", "")
    if t:
        return t
    if _TOKEN_FILE.exists():
        return _TOKEN_FILE.read_text().strip()
    sys.exit(
        "❌  API 토큰을 찾을 수 없습니다.\n"
        "    환경변수 CERBERUS_TOKEN 또는 ~/.cerberus/api_token 파일을 확인하세요."
    )


def _calc_totp(secret_b32: str) -> str:
    """stdlib만으로 RFC 6238 TOTP 계산 (SHA-1, 30초, 6자리)."""
    # Base32 디코딩 — 패딩 보정
    secret_b32 = secret_b32.upper().strip()
    pad = (8 - len(secret_b32) % 8) % 8
    key = base64.b32decode(secret_b32 + "=" * pad)
    counter = int(time.time() // 30)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


def _get_otp() -> str:
    """~/.cerberus/agent_otp_secret으로 현재 OTP 코드 반환."""
    if not _AGENT_OTP_FILE.exists():
        sys.exit(
            "❌  ~/.cerberus/agent_otp_secret 없음.\n"
            "    서버를 먼저 실행하면 자동 생성됩니다."
        )
    return _calc_totp(_AGENT_OTP_FILE.read_text().strip())


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """urllib.request로 HTTP 요청 + X-API-Token + X-OTP 2FA 헤더 전송."""
    url = _get_url() + path
    token = _get_token()
    otp = _get_otp()
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "X-API-Token": token,
        "X-OTP": otp,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        sys.exit(f"❌  HTTP {e.code}: {body_text}")
    except urllib.error.URLError as e:
        sys.exit(f"❌  연결 실패 ({_get_url()}): {e.reason}")


# ── 커맨드 핸들러 ──────────────────────────────────────────────────────────────

def cmd_tunnel(args: argparse.Namespace) -> None:
    """tunnel status / open [port] / close <port|all> / lock <port>"""
    sub = args.tunnel_cmd

    if sub == "status":
        result = _request("GET", "/api/tunnel/status")
        tunnels = result.get("tunnels", [])
        if not tunnels:
            print("🔴 활성 터널 없음")
            return
        for t in tunnels:
            lock_icon = "🔒" if not t.get("track_activity", True) else "⏱"
            print(f"🟢 포트 {t['port']}: {t['url']} {lock_icon} (유휴 {t['idle_sec']}초)")

    elif sub == "open":
        port = args.port
        body = {"port": port} if port is not None else {}
        result = _request("POST", "/api/tunnel/open", body)
        print(result.get("message", ""))

    elif sub == "close":
        target = args.target
        if target == "all":
            body = {"all": True}
        else:
            body = {"port": int(target)}
        result = _request("POST", "/api/tunnel/close", body)
        print(result.get("message", ""))

    elif sub == "lock":
        result = _request("POST", "/api/tunnel/lock", {"port": int(args.port)})
        print(result.get("message", ""))

    else:
        print(f"알 수 없는 tunnel 서브커맨드: {sub}")
        sys.exit(1)


def cmd_idle(args: argparse.Namespace) -> None:
    """idle → 조회, idle <분> → 변경."""
    if args.minutes is None:
        result = _request("GET", "/api/idle-timeout")
        mins = result.get("idle_timeout_minutes", "?")
        label = f"{mins}분" if mins != 0 else "비활성"
        print(f"유휴 타임아웃: {label}")
    else:
        minutes = max(0, int(args.minutes))
        result = _request("POST", "/api/idle-timeout", {"idle_timeout": minutes})
        mins = result.get("idle_timeout_minutes", minutes)
        label = f"{mins}분" if mins != 0 else "비활성"
        print(f"✅ 유휴 타임아웃: {label}")


def cmd_metrics(_args: argparse.Namespace) -> None:
    """GET /api/metrics → 사람이 읽기 쉬운 형식 출력."""
    m = _request("GET", "/api/metrics")
    print(f"CPU  {m.get('cpu_pct', 0):.1f}%")
    mem_used = m.get("mem_used_gb", 0)
    mem_total = m.get("mem_total_gb", 0)
    print(f"MEM  {m.get('mem_pct', 0):.1f}%  ({mem_used:.1f} GB / {mem_total:.1f} GB)")
    disk_used = m.get("disk_used_gb", 0)
    disk_total = m.get("disk_total_gb", 0)
    print(f"DISK {m.get('disk_pct', 0):.1f}%  ({disk_used:.1f} GB / {disk_total:.1f} GB)")
    load = m.get("load_avg", [0, 0, 0])
    print(f"LOAD {load[0]:.2f} {load[1]:.2f} {load[2]:.2f}")
    uptime = m.get("uptime_sec", 0)
    print(f"UP   {uptime}s")


def cmd_sessions(_args: argparse.Namespace) -> None:
    """GET /api/agent/sessions → 세션 목록 출력."""
    result = _request("GET", "/api/agent/sessions")
    sessions = result.get("sessions", [])
    if not sessions:
        print("(활성 세션 없음)")
    for s in sessions:
        print(s)


def cmd_new_session(_args: argparse.Namespace) -> None:
    """POST /api/agent/session → 새 세션 ID 출력."""
    result = _request("POST", "/api/agent/session")
    print(result.get("session_id", ""))


def cmd_delete_session(args: argparse.Namespace) -> None:
    """DELETE /api/agent/session/<id>."""
    result = _request("DELETE", f"/api/agent/session/{args.session_id}")
    print(result.get("message", "삭제됨"))


async def _exec_ws(base_url: str, token: str, session_id: str, cmd: str, timeout: float) -> None:
    """websockets로 WS 연결 → stdin 전송 → stdout 수신 → 출력."""
    try:
        import websockets  # type: ignore
    except ImportError:
        sys.exit("❌  pip install 'websockets>=12'")

    # http:// → ws:// 변환
    ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
    otp = _get_otp()
    ws_url = f"{ws_url}/ws/{session_id}?token={token}&otp={otp}"

    collected = []

    try:
        async with websockets.connect(ws_url) as ws:
            # stdin 전송
            await ws.send(json.dumps(["stdin", cmd]))

            # stdout 수신: 200ms 무출력 또는 timeout 초과 시 종료
            deadline = asyncio.get_event_loop().time() + timeout
            last_recv = asyncio.get_event_loop().time()

            while True:
                now = asyncio.get_event_loop().time()
                if now >= deadline:
                    break
                # 남은 시간과 200ms 중 짧은 쪽으로 대기
                remaining = deadline - now
                idle_gap = 0.2  # 200ms

                try:
                    wait_time = min(remaining, idle_gap)
                    msg_raw = await asyncio.wait_for(ws.recv(), timeout=wait_time)
                    msg = json.loads(msg_raw)
                    if isinstance(msg, list) and msg[0] == "stdout":
                        collected.append(msg[1])
                        last_recv = asyncio.get_event_loop().time()
                except asyncio.TimeoutError:
                    # 200ms 경과 — 마지막 수신 이후 200ms 무출력이면 종료
                    now2 = asyncio.get_event_loop().time()
                    if now2 - last_recv >= idle_gap:
                        break
                    # deadline 도달 체크
                    if now2 >= deadline:
                        break
                except Exception:
                    break
    except Exception as e:
        sys.exit(f"❌  WS 연결 실패: {e}")

    output = "".join(collected)
    sys.stdout.write(output)
    if output and not output.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()


def cmd_exec(args: argparse.Namespace) -> None:
    """SPEC.md §7.6 exec 동작: WS 연결로 명령 실행."""
    timeout = args.timeout if args.timeout is not None else 3.0

    # 1. 세션 결정
    session_id = args.session
    if not session_id:
        result = _request("GET", "/api/agent/sessions")
        sessions = result.get("sessions", [])
        if sessions:
            session_id = sessions[0]
        else:
            # 세션 없으면 자동 생성
            r = _request("POST", "/api/agent/session")
            session_id = r.get("session_id", "")
            if not session_id:
                sys.exit("❌  세션 생성 실패")

    token = _get_token()
    base_url = _get_url()

    # 2-5. WS 연결 + stdin 전송 + stdout 수신
    asyncio.run(_exec_ws(base_url, token, session_id, args.cmd, timeout))


def cmd_macros(_args: argparse.Namespace) -> None:
    """GET /api/macros → 매크로 목록 출력."""
    url = _get_url() + "/api/macros"
    token = _get_token()
    otp = _get_otp()
    req = urllib.request.Request(
        url,
        headers={"X-API-Token": token, "X-OTP": otp, "Accept": "application/json"},
        method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        sys.exit(f"❌  HTTP {e.code}: {body_text}")
    except urllib.error.URLError as e:
        sys.exit(f"❌  연결 실패 ({_get_url()}): {e.reason}")

    if not isinstance(result, list):
        print(json.dumps(result, indent=2))
        return
    for m in result:
        send_repr = repr(m.get("send", ""))
        print(f"  [{m.get('label', '')}]  →  {send_repr}")


def cmd_health(_args: argparse.Namespace) -> None:
    """GET /health → 서버 연결 상태 출력."""
    url = _get_url() + "/health"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        print(f"✅  연결됨 — {_get_url()} (uptime {data.get('uptime_sec', '?')}s)")
    except urllib.error.URLError as e:
        sys.exit(f"❌  연결 실패 ({_get_url()}): {e.reason}")


# ── CLI 파서 ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="cerberus_ctl — Cerberus Beacon CLI 클라이언트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    # ── 터널 제어 ──────────────────────────────────────────────────────────────
    p_tunnel = sub.add_parser("tunnel", help="터널 제어")
    tunnel_sub = p_tunnel.add_subparsers(dest="tunnel_cmd", required=True)

    tunnel_sub.add_parser("status", help="활성 터널 목록")

    p_open = tunnel_sub.add_parser("open", help="터널 개통")
    p_open.add_argument("port", type=int, nargs="?", default=None, help="포트 (생략 시 서버 기본)")

    p_close = tunnel_sub.add_parser("close", help="터널 종료")
    p_close.add_argument("target", help="포트 번호 또는 'all'")

    p_lock = tunnel_sub.add_parser("lock", help="잠금 토글 (영구↔자동)")
    p_lock.add_argument("port", type=int, help="포트 번호")

    # ── 설정 / 상태 ────────────────────────────────────────────────────────────
    p_idle = sub.add_parser("idle", help="유휴 타임아웃 조회/변경")
    p_idle.add_argument("minutes", type=int, nargs="?", default=None,
                        help="타임아웃 분 (생략 시 조회, 0=비활성)")

    sub.add_parser("metrics", help="CPU / MEM / DISK 현황")
    sub.add_parser("macros", help="서버 매크로 목록 출력")
    sub.add_parser("health", help="서버 연결 상태 확인")

    # ── 터미널 세션 ────────────────────────────────────────────────────────────
    sub.add_parser("sessions", help="활성 터미널 세션 목록")
    sub.add_parser("new-session", help="새 터미널 세션 생성")

    p_del = sub.add_parser("delete-session", help="세션 종료")
    p_del.add_argument("session_id", help="종료할 세션 ID")

    p_exec = sub.add_parser("exec", help="세션에 명령 전송 후 출력 수신")
    p_exec.add_argument("cmd", help="전송할 명령 (개행 미포함 시 자동 추가)")
    p_exec.add_argument("--session", "-s", default=None, help="세션 ID (생략 시 첫 번째 세션)")
    p_exec.add_argument("--timeout", "-t", type=float, default=None, help="출력 대기 초 (기본 3)")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # 'exec' 명령: cmd에 개행이 없으면 자동 추가
    if args.command == "exec" and not args.cmd.endswith("\n"):
        args.cmd += "\n"

    dispatch = {
        "tunnel": cmd_tunnel,
        "idle": cmd_idle,
        "metrics": cmd_metrics,
        "sessions": cmd_sessions,
        "new-session": cmd_new_session,
        "delete-session": cmd_delete_session,
        "exec": cmd_exec,
        "macros": cmd_macros,
        "health": cmd_health,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
