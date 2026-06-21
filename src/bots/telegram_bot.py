"""
src/bots/telegram_bot.py — Telegram 봇 데몬 (독립 프로세스, src.tunnel 직접 제어)
터널 제어: src.tunnel 직접 임포트 (공유 상태: tunnels.json)
터미널 제어: 웹서버 API + WS 경유 (Token + OTP 2FA)
SPEC.md §2.4
"""
from __future__ import annotations

import asyncio as _asyncio
import base64
import hashlib
import hmac
import json
import struct
import sys
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Config


def _sync_from_file() -> None:
    """tunnels.json → _tunnels 동기화 (봇 프로세스의 in-memory 상태 갱신)."""
    import src.tunnel as _t
    with _t._tlock:
        _t._tunnels.clear()
    _t.restore_tunnels()


def _read_file_tunnels() -> list:
    """tunnels.json을 직접 읽어 현재 살아있는 터널 목록 반환."""
    import src.tunnel as _t
    if not _t._CF_STATE_FILE.exists():
        return []
    try:
        items = json.loads(_t._CF_STATE_FILE.read_text())
        return [i for i in items if _t._is_alive(i["pid"])]
    except Exception:
        return []


def _calc_totp(secret_b32: str) -> str:
    """stdlib RFC 6238 TOTP (SHA-1, 30초, 6자리)."""
    secret_b32 = secret_b32.upper().strip()
    pad = (8 - len(secret_b32) % 8) % 8
    key = base64.b32decode(secret_b32 + "=" * pad)
    counter = int(time.time() // 30)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


def _agent_headers(port: int) -> tuple[str, str, dict]:
    """(base_url, otp, headers) 반환. api_token + agent_otp_secret 2FA."""
    base_url = f"http://127.0.0.1:{port}"
    token_f = Path.home() / ".cerberus" / "api_token"
    otp_f = Path.home() / ".cerberus" / "agent_otp_secret"
    token = token_f.read_text().strip() if token_f.exists() else ""
    otp_secret = otp_f.read_text().strip() if otp_f.exists() else ""
    otp = _calc_totp(otp_secret) if otp_secret else ""
    headers = {
        "X-API-Token": token,
        "X-OTP": otp,
        "Content-Type": "application/json",
    }
    return base_url, otp, headers


def _http_request(method: str, url: str, headers: dict, body: dict | None = None) -> dict:
    """동기 HTTP 요청 헬퍼 (asyncio.to_thread에서 호출)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


async def _exec_terminal(port: int, cmd: str, timeout: float = 5.0, T: dict | None = None) -> str:
    """웹서버 WS 경유로 터미널 명령 실행 → stdout 반환."""
    _T = T or {}
    try:
        import websockets  # type: ignore
    except ImportError:
        return "❌ pip install 'websockets>=12'"

    base_url, otp, headers = _agent_headers(port)

    # 1. 세션 가져오기 또는 생성
    try:
        result = await _asyncio.to_thread(
            _http_request, "GET", f"{base_url}/api/agent/sessions", headers
        )
        sessions = result.get("sessions", [])
        if sessions:
            session_id = sessions[0]
        else:
            r = await _asyncio.to_thread(
                _http_request, "POST", f"{base_url}/api/agent/session", headers, {}
            )
            session_id = r.get("session_id", "")
            if not session_id:
                return _T.get("exec_session_fail", "❌ Failed to create session")
    except Exception as e:
        return _T.get("exec_server_fail", "❌ Server connection failed: {err}").format(err=e)

    # 30초 window 경계를 넘을 수 있으므로 WS 접속 직전 OTP 재계산
    # token은 _agent_headers()에서 이미 읽은 값 재사용
    _otp_f = Path.home() / ".cerberus" / "agent_otp_secret"
    _otp_secret = _otp_f.read_text().strip() if _otp_f.exists() else ""
    fresh_otp = _calc_totp(_otp_secret) if _otp_secret else ""
    token = headers.get("X-API-Token", "")

    # 2. WS 연결 + stdin 전송 + stdout 수신
    ws_url = f"ws://127.0.0.1:{port}/ws/{session_id}?token={token}&otp={fresh_otp}"
    collected: list[str] = []
    try:
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps(["stdin", cmd + "\n"]))
            deadline = _asyncio.get_event_loop().time() + timeout
            last_recv = _asyncio.get_event_loop().time()
            while True:
                now = _asyncio.get_event_loop().time()
                if now >= deadline:
                    break
                try:
                    raw = await _asyncio.wait_for(ws.recv(), timeout=0.2)
                    msg = json.loads(raw)
                    if isinstance(msg, list) and msg[0] == "stdout":
                        collected.append(msg[1])
                        last_recv = _asyncio.get_event_loop().time()
                except _asyncio.TimeoutError:
                    if _asyncio.get_event_loop().time() - last_recv >= 0.2:
                        break
                except Exception:
                    break
    except Exception as e:
        return _T.get("exec_ws_fail", "❌ WebSocket connection failed: {err}").format(err=e)

    output = "".join(collected).strip()
    # 텔레그램 메시지 최대 4096자
    if len(output) > 3900:
        output = _T.get("exec_truncated", "…(truncated)…\n") + output[-3900:]
    return output or _T.get("exec_no_output", "(no output)")


_TG_STRINGS: dict[str, dict[str, str]] = {
    "ko": {
        "idle_status":        "유휴 타임아웃: {label}\n변경: /idle <분> (0=비활성)",
        "idle_unit":          "{n}분",
        "idle_disabled":      "비활성",
        "idle_set_ok":        "✅ 유휴 타임아웃: {label}",
        "idle_query_fail":    "⚠️ 조회 실패: {err}",
        "idle_set_fail":      "⚠️ 서버 반영 실패: {err}",
        "lock_usage":         "사용: /lock <port>\n🔒=영구 유지, ⏱=유휴 자동 종료",
        "exec_usage":         "사용: /exec <명령>",
        "exec_running":       "⏳ 실행 중: `{cmd}` _(5초 제한)_",
        "exec_no_output":     "(출력 없음)",
        "sessions_none":      "(활성 세션 없음)\n생성: /new",
        "sessions_footer":    "\n\n종료: /kill <세션ID>",
        "new_ok":             "✅ 새 세션 생성: {sid}",
        "new_fail":           "⚠️ 세션 생성 실패",
        "kill_usage":         "사용: /kill <세션ID>\n목록: /sessions",
        "kill_ok":            "✅ 세션 종료: {sid}",
        "kill_remain":        " (남은 세션: {n}개)",
        "kill_all_done":      " (모든 세션 종료)",
        "kill_tunnel_prompt": "✅ 세션 종료: {sid}\n\n터미널 세션이 모두 종료됐습니다.\n활성 터널 (포트 {ports})도 함께 종료할까요?",
        "btn_close_tunnels":  "🔒 터널도 종료",
        "btn_keep_tunnels":   "✅ 터널 유지",
        "cb_tunnels_closed":  "🔒 터널 종료됨\n{msg}",
        "cb_tunnels_kept":    "✅ 터널 유지 (포트 {ports})\n/close 로 나중에 종료 가능",
        "status_tunnel_hdr":  "📡 [터널]",
        "status_offline":     "  오프라인",
        "status_term_hdr":    "💻 [터미널]",
        "status_no_session":  "  (세션 없음)",
        "status_server_fail": "  (서버 연결 실패)",
        "status_local":       "내부",
        "exec_session_fail":  "❌ 세션 생성 실패",
        "exec_server_fail":   "❌ 서버 연결 실패: {err}",
        "exec_ws_fail":       "❌ WS 연결 실패: {err}",
        "exec_truncated":     "…(생략)…\n",
        "poll_opened":        "🔓 터널 개통 (포트 {port})\n{url}",
        "poll_closed":        "🔒 터널 종료 (포트 {port})",
        "startup":            "🐺 CerberusBeacon 온라인\n서버: http://127.0.0.1:{port}/login",
        "err":                "⚠️ 오류: {err}",
        "cmd_status":         "📡 터널+세션 통합 상태",
        "cmd_open":           "[터널] 개통 [port]",
        "cmd_close":          "[터널] 종료 [port|all]",
        "cmd_lock":           "[터널] 잠금 토글 <port>",
        "cmd_idle":           "[터널] 유휴 타임아웃 [분]",
        "cmd_sessions":       "[터미널] 세션 목록",
        "cmd_exec":           "[터미널] 명령 실행 <cmd>",
        "cmd_new":            "[터미널] 새 세션 생성",
        "cmd_kill":           "[터미널] 세션 종료 <sid>",
    },
    "en": {
        "idle_status":        "Idle timeout: {label}\nChange: /idle <minutes> (0=disabled)",
        "idle_unit":          "{n} min",
        "idle_disabled":      "disabled",
        "idle_set_ok":        "✅ Idle timeout: {label}",
        "idle_query_fail":    "⚠️ Query failed: {err}",
        "idle_set_fail":      "⚠️ Server update failed: {err}",
        "lock_usage":         "Usage: /lock <port>\n🔒=permanent, ⏱=auto-close on idle",
        "exec_usage":         "Usage: /exec <command>",
        "exec_running":       "⏳ Running: `{cmd}` _(5s limit)_",
        "exec_no_output":     "(no output)",
        "sessions_none":      "(no active sessions)\nCreate: /new",
        "sessions_footer":    "\n\nClose: /kill <session-id>",
        "new_ok":             "✅ New session created: {sid}",
        "new_fail":           "⚠️ Failed to create session",
        "kill_usage":         "Usage: /kill <session-id>\nList: /sessions",
        "kill_ok":            "✅ Session closed: {sid}",
        "kill_remain":        " ({n} session(s) remaining)",
        "kill_all_done":      " (all sessions closed)",
        "kill_tunnel_prompt": "✅ Session closed: {sid}\n\nAll terminal sessions closed.\nClose active tunnels (port {ports}) as well?",
        "btn_close_tunnels":  "🔒 Close tunnels",
        "btn_keep_tunnels":   "✅ Keep tunnels",
        "cb_tunnels_closed":  "🔒 Tunnels closed\n{msg}",
        "cb_tunnels_kept":    "✅ Tunnels kept (port {ports})\nUse /close to close later",
        "status_tunnel_hdr":  "📡 [Tunnel]",
        "status_offline":     "  offline",
        "status_term_hdr":    "💻 [Terminal]",
        "status_no_session":  "  (no sessions)",
        "status_server_fail": "  (server connection failed)",
        "status_local":       "local",
        "exec_session_fail":  "❌ Failed to create session",
        "exec_server_fail":   "❌ Server connection failed: {err}",
        "exec_ws_fail":       "❌ WebSocket connection failed: {err}",
        "exec_truncated":     "…(truncated)…\n",
        "poll_opened":        "🔓 Tunnel opened (port {port})\n{url}",
        "poll_closed":        "🔒 Tunnel closed (port {port})",
        "startup":            "🐺 CerberusBeacon online\nServer: http://127.0.0.1:{port}/login",
        "err":                "⚠️ Error: {err}",
        "cmd_status":         "📡 Tunnel + session overview",
        "cmd_open":           "[Tunnel] Open [port]",
        "cmd_close":          "[Tunnel] Close [port|all]",
        "cmd_lock":           "[Tunnel] Toggle lock <port>",
        "cmd_idle":           "[Tunnel] Idle timeout [min]",
        "cmd_sessions":       "[Terminal] Session list",
        "cmd_exec":           "[Terminal] Run command <cmd>",
        "cmd_new":            "[Terminal] New session",
        "cmd_kill":           "[Terminal] Kill session <sid>",
    },
}


def _run_telegram_bot(cfg: "Config") -> None:
    """python-telegram-bot v21 — 독립 프로세스, src.tunnel 직접 제어."""
    try:
        from telegram.ext import ApplicationBuilder, CommandHandler  # type: ignore
    except ImportError:
        sys.exit("uv sync --extra telegram  (또는 pip install python-telegram-bot[all])")

    from src.i18n import resolve_lang
    lang = resolve_lang(getattr(cfg, "lang", ""))
    T = _TG_STRINGS.get(lang, _TG_STRINGS["en"])

    import src.tunnel as _tunnel
    _tunnel._current_cfg_port = cfg.port
    _tunnel._lang = lang

    def _check_chat(update) -> bool:
        return cfg.tg_chat_id == 0 or update.effective_chat.id == cfg.tg_chat_id

    async def cmd_open(update, context):
        if not _check_chat(update):
            return
        port = int(context.args[0]) if context.args else cfg.port
        _sync_from_file()
        msg = _tunnel.open_tunnel(port)
        # URL을 클릭 가능한 Markdown 링크로 변환
        import re as _re
        m = _re.search(r'(https://\S+)', msg)
        if m:
            url = m.group(1)
            msg_linked = msg.replace(url, f'[{url}]({url})')
            try:
                await update.message.reply_text(msg_linked, parse_mode="Markdown")
                return
            except Exception:
                pass
        await update.message.reply_text(msg)

    async def cmd_close(update, context):
        if not _check_chat(update):
            return
        _sync_from_file()
        if not context.args or context.args[0] == "all":
            msg = _tunnel.close_all_tunnels("Telegram")
        else:
            msg = _tunnel.close_tunnel(int(context.args[0]), "Telegram")
        await update.message.reply_text(msg)

    async def cmd_status(update, context):
        """터널 + 터미널 세션 통합 상태."""
        if not _check_chat(update):
            return
        tunnels = _read_file_tunnels()
        base_url, _, headers = _agent_headers(cfg.port)
        lines = [T["status_tunnel_hdr"]]
        if tunnels:
            for t in tunnels:
                lock_icon = "🔒" if not t.get("track_activity", True) else "⏱"
                lines.append(f"  🟢 {T['status_local']}:{t['port']} → {t['url']} {lock_icon}")
        else:
            lines.append(T["status_offline"])
        lines.append("")
        lines.append(T["status_term_hdr"])
        try:
            result = await _asyncio.to_thread(
                _http_request, "GET", f"{base_url}/api/agent/sessions", headers
            )
            sessions = result.get("sessions", [])
            if sessions:
                for s in sessions:
                    lines.append(f"  • {s}")
            else:
                lines.append(T["status_no_session"])
        except Exception:
            lines.append(T["status_server_fail"])
        await update.message.reply_text("\n".join(lines))

    async def cmd_idle(update, context):
        if not _check_chat(update):
            return
        base_url, otp, headers = _agent_headers(cfg.port)
        if not context.args:
            try:
                result = await _asyncio.to_thread(
                    _http_request, "GET", f"{base_url}/api/idle-timeout", headers
                )
                mins = result.get("idle_timeout_minutes", cfg.idle_timeout)
                label = T["idle_unit"].format(n=mins) if mins else T["idle_disabled"]
                await update.message.reply_text(T["idle_status"].format(label=label))
            except Exception as e:
                await update.message.reply_text(T["idle_query_fail"].format(err=e))
            return
        minutes = max(0, min(1440, int(context.args[0])))
        try:
            result = await _asyncio.to_thread(
                _http_request, "POST", f"{base_url}/api/idle-timeout", headers,
                {"idle_timeout": minutes}
            )
            cfg.idle_timeout = minutes
            label = T["idle_unit"].format(n=minutes) if minutes > 0 else T["idle_disabled"]
            await update.message.reply_text(T["idle_set_ok"].format(label=label))
        except Exception as e:
            await update.message.reply_text(T["idle_set_fail"].format(err=e))

    async def cmd_lock(update, context):
        if not _check_chat(update):
            return
        if not context.args:
            await update.message.reply_text(T["lock_usage"])
            return
        port = int(context.args[0])
        _sync_from_file()
        msg = _tunnel.toggle_tunnel_lock(port)
        await update.message.reply_text(msg)

    async def cmd_exec(update, context):
        if not _check_chat(update):
            return
        if not context.args:
            await update.message.reply_text(T["exec_usage"])
            return
        cmd = " ".join(context.args)
        msg = await update.message.reply_text(T["exec_running"].format(cmd=cmd), parse_mode="Markdown")
        output = await _exec_terminal(cfg.port, cmd, T=T)
        await msg.edit_text(f"```\n{output}\n```", parse_mode="Markdown")

    async def cmd_sessions(update, context):
        if not _check_chat(update):
            return
        base_url, _, headers = _agent_headers(cfg.port)
        try:
            result = await _asyncio.to_thread(
                _http_request, "GET", f"{base_url}/api/agent/sessions", headers
            )
            sessions = result.get("sessions", [])
            if not sessions:
                await update.message.reply_text(T["sessions_none"])
            else:
                text = "\n".join(f"• {s}" for s in sessions)
                text += T["sessions_footer"]
                await update.message.reply_text(text)
        except Exception as e:
            await update.message.reply_text(T["err"].format(err=e))

    async def cmd_new(update, context):
        """새 터미널 세션 생성."""
        if not _check_chat(update):
            return
        base_url, _, headers = _agent_headers(cfg.port)
        try:
            result = await _asyncio.to_thread(
                _http_request, "POST", f"{base_url}/api/agent/session", headers, {}
            )
            sid = result.get("session_id", "")
            if sid:
                await update.message.reply_text(T["new_ok"].format(sid=sid))
            else:
                await update.message.reply_text(T["new_fail"])
        except Exception as e:
            await update.message.reply_text(T["err"].format(err=e))

    async def cmd_kill(update, context):
        """터미널 세션 강제 종료. 마지막 세션이면 터널 종료 여부를 인라인 버튼으로 제안."""
        if not _check_chat(update):
            return
        if not context.args:
            await update.message.reply_text(T["kill_usage"])
            return
        sid = context.args[0]
        base_url, _, headers = _agent_headers(cfg.port)
        try:
            await _asyncio.to_thread(
                _http_request, "DELETE", f"{base_url}/api/agent/session/{sid}", headers
            )
            result2 = await _asyncio.to_thread(
                _http_request, "GET", f"{base_url}/api/agent/sessions", headers
            )
            sessions = result2.get("sessions", [])
            tunnels = _read_file_tunnels()
            if not sessions and tunnels:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup  # type: ignore
                port_str = ", ".join(str(t["port"]) for t in tunnels)
                keyboard = [
                    [InlineKeyboardButton(T["btn_close_tunnels"], callback_data="tg_close_all_tunnels")],
                    [InlineKeyboardButton(T["btn_keep_tunnels"], callback_data="tg_keep_tunnels")],
                ]
                await update.message.reply_text(
                    T["kill_tunnel_prompt"].format(sid=sid, ports=port_str),
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
            else:
                remain_msg = T["kill_remain"].format(n=len(sessions)) if sessions else T["kill_all_done"]
                await update.message.reply_text(T["kill_ok"].format(sid=sid) + remain_msg)
        except Exception as e:
            await update.message.reply_text(T["err"].format(err=e))

    async def callback_tunnel_choice(update, context):
        """인라인 버튼 콜백 — 터널 종료 여부."""
        query = update.callback_query
        if not (cfg.tg_chat_id == 0 or query.message.chat_id == cfg.tg_chat_id):
            await query.answer()
            return
        await query.answer()
        if query.data == "tg_close_all_tunnels":
            _sync_from_file()
            msg = _tunnel.close_all_tunnels("Telegram")
            await query.edit_message_text(T["cb_tunnels_closed"].format(msg=msg))
        else:
            tunnels = _read_file_tunnels()
            port_str = ", ".join(str(t["port"]) for t in tunnels) if tunnels else "-"
            await query.edit_message_text(T["cb_tunnels_kept"].format(ports=port_str))

    async def _poll_status(app):
        """30초 간격으로 tunnels.json 변화 감지 → Telegram 알림."""
        prev: set[int] = {t["port"] for t in _read_file_tunnels()}
        while True:
            await _asyncio.sleep(30)
            if not cfg.tg_chat_id:
                continue
            curr_list = _read_file_tunnels()
            curr = {t["port"]: t["url"] for t in curr_list}
            curr_ports = set(curr.keys())
            for p in curr_ports - prev:
                try:
                    await app.bot.send_message(
                        chat_id=cfg.tg_chat_id,
                        text=T["poll_opened"].format(port=p, url=curr[p]),
                    )
                except Exception:
                    pass
            for p in prev - curr_ports:
                try:
                    await app.bot.send_message(
                        chat_id=cfg.tg_chat_id,
                        text=T["poll_closed"].format(port=p),
                    )
                except Exception:
                    pass
            prev = curr_ports

    async def _bot_main():
        from telegram import BotCommand  # type: ignore
        from telegram.ext import CallbackQueryHandler  # type: ignore
        app = ApplicationBuilder().token(cfg.tg_token).build()
        # ── 터널 제어 ──────────────────────────────────────────────────
        app.add_handler(CommandHandler("open",     cmd_open))
        app.add_handler(CommandHandler("close",    cmd_close))
        app.add_handler(CommandHandler("lock",     cmd_lock))
        app.add_handler(CommandHandler("idle",     cmd_idle))
        # ── 터미널 제어 ────────────────────────────────────────────────
        app.add_handler(CommandHandler("exec",     cmd_exec))
        app.add_handler(CommandHandler("sessions", cmd_sessions))
        app.add_handler(CommandHandler("new",      cmd_new))
        app.add_handler(CommandHandler("kill",     cmd_kill))
        # ── 통합 상태 / 인라인 콜백 ────────────────────────────────────
        app.add_handler(CommandHandler("status",   cmd_status))
        app.add_handler(CallbackQueryHandler(
            callback_tunnel_choice,
            pattern="^(tg_close_all_tunnels|tg_keep_tunnels)$"
        ))

        await app.bot.set_my_commands([
            BotCommand("status",   T["cmd_status"]),
            BotCommand("open",     T["cmd_open"]),
            BotCommand("close",    T["cmd_close"]),
            BotCommand("lock",     T["cmd_lock"]),
            BotCommand("idle",     T["cmd_idle"]),
            BotCommand("sessions", T["cmd_sessions"]),
            BotCommand("exec",     T["cmd_exec"]),
            BotCommand("new",      T["cmd_new"]),
            BotCommand("kill",     T["cmd_kill"]),
        ])

        async with app:
            await app.start()
            await app.updater.start_polling()
            if cfg.tg_chat_id:
                try:
                    await app.bot.send_message(
                        chat_id=cfg.tg_chat_id,
                        text=T["startup"].format(port=cfg.port),
                    )
                except Exception:
                    pass
            print("[TG] bot started", flush=True)
            _asyncio.create_task(_poll_status(app))
            await _asyncio.Event().wait()

    loop = _asyncio.new_event_loop()
    _asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_bot_main())
    except Exception as e:
        print(f"[TG] 봇 오류: {e}", file=sys.stderr, flush=True)
    finally:
        loop.close()
