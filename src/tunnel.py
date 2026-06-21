"""
src/tunnel.py — TunnelInfo, open_tunnel, close_tunnel, watchdog, _ensure_cloudflared
공유 상태: _tunnels, _tlock, _current_cfg_port, _notifiers, _notifiers_lock, _idle_timeout_minutes
SPEC.md §2
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.config import CERBERUS_DIR, Config

# ── cloudflared 경로 ──────────────────────────────────────────────────────────
_CF_BINARY = str(CERBERUS_DIR / "cloudflared")
_CF_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
_CF_SHA256_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.sha256sum"
_CF_LOG_DIR = CERBERUS_DIR / "tunnel_logs"
_CF_STATE_FILE = CERBERUS_DIR / "tunnels.json"


@dataclass
class TunnelInfo:
    pid: int                       # cloudflared PID (서버 재시작 후에도 유효)
    url: str                       # https://xxx.trycloudflare.com
    port: int
    proc: Optional[subprocess.Popen] = field(default=None, repr=False)  # 이번 세션에서 기동한 경우만
    opened_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    track_activity: bool = True
    metrics_port: int = 0          # cloudflared 자동 기동 메트릭 서버 포트 (0=미확인)
    metrics_requests: int = field(default=0, repr=False)  # 마지막 폴링 시점 total_requests


# ── 공유 상태 ──────────────────────────────────────────────────────────────────
_tunnels: dict[int, TunnelInfo] = {}
_tlock = threading.Lock()
_idle_timeout_minutes: int = 5
_current_cfg_port: int = 8765
_lang: str = ""  # 런타임에 cfg.lang으로 설정

# 봇 알림 콜백
_notifiers: list[tuple[str, Any]] = []
_notifiers_lock = threading.Lock()

# ── 다국어 문자열 ──────────────────────────────────────────────────────────────
_TN: dict[str, dict[str, str]] = {
    "ko": {
        "invalid_port":   "잘못된 포트: {port}",
        "already_open":   "이미 열려 있음 (포트 {port})\n{url}/login",
        "open_fail_429":  "❌ 터널 개통 실패 (포트 {port})\nCloudflare 요청 횟수 초과 (429)\n잠시 후 다시 시도하세요.",
        "open_fail":      "❌ 터널 개통 실패 (포트 {port})\n{reason}",
        "open_ok":        "🔓 터널 개통\n내부: http://127.0.0.1:{port}\n외부: {url}/login",
        "port_not_found": "해당 포트 터널 없음",
        "no_tunnels":     "활성 터널 없음",
        "close_ok":       "🔒 터널 차단 ({reason})",
        "close_all_ok":   "🔒 전체 터널 차단 ({reason}): {ports}",
        "reason_manual":  "수동",
        "offline":        "🔴 오프라인",
        "status_item":    "🟢 포트 {port}: {url}/login {lock} (유휴 {idle}초)",
        "lock_auto":      "유휴 자동 종료 활성",
        "lock_permanent": "영구 유지 (자동 종료 없음)",
        "lock_ok":        "포트 {port} 터널: {label}",
        "timeout_reason": "유휴 {n}분 타임아웃",
        "proc_died":      "프로세스 종료",
        "watchdog_msg":   "포트 {port} 터널 {reason}",
    },
    "en": {
        "invalid_port":   "Invalid port: {port}",
        "already_open":   "Already open (port {port})\n{url}/login",
        "open_fail_429":  "❌ Tunnel open failed (port {port})\nCloudflare rate limit (429)\nPlease try again later.",
        "open_fail":      "❌ Tunnel open failed (port {port})\n{reason}",
        "open_ok":        "🔓 Tunnel opened\nLocal: http://127.0.0.1:{port}\nExternal: {url}/login",
        "port_not_found": "No tunnel on that port",
        "no_tunnels":     "No active tunnels",
        "close_ok":       "🔒 Tunnel closed ({reason})",
        "close_all_ok":   "🔒 All tunnels closed ({reason}): {ports}",
        "reason_manual":  "manual",
        "offline":        "🔴 Offline",
        "status_item":    "🟢 Port {port}: {url}/login {lock} (idle {idle}s)",
        "lock_auto":      "auto-close active",
        "lock_permanent": "permanent (no auto-close)",
        "lock_ok":        "Port {port} tunnel: {label}",
        "timeout_reason": "idle {n}min timeout",
        "proc_died":      "process exited",
        "watchdog_msg":   "Tunnel port {port}: {reason}",
    },
}


def _S() -> dict[str, str]:
    """현재 언어 문자열 딕셔너리 반환."""
    return _TN.get(_lang, _TN["en"])


def touch_tunnels() -> None:
    """브라우저 HTTP 요청(클릭·폴링 포함) 시 track_activity 터널의 last_activity 갱신."""
    now = time.time()
    with _tlock:
        for info in _tunnels.values():
            if info.track_activity:
                info.last_activity = now


def _poll_tunnel_metrics(info: TunnelInfo) -> int:
    """cloudflared 메트릭에서 total_requests 읽기. 실패 시 -1.
    cloudflared가 자동으로 127.0.0.1:{metrics_port}/metrics 를 Prometheus 형식으로 노출."""
    if info.metrics_port <= 0:
        return -1
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{info.metrics_port}/metrics", timeout=2
        ) as resp:
            for line in resp.read().decode().splitlines():
                if line.startswith("cloudflared_tunnel_total_requests "):
                    return int(float(line.split()[-1]))
    except Exception:
        pass
    return -1


def _notify_all(msg: str, exclude: str = "") -> None:
    with _notifiers_lock:
        fns = list(_notifiers)
    for name, fn in fns:
        if name != exclude:
            try:
                fn(msg)
            except Exception as e:
                print(f"[NOTIFY ERR:{name}] {e}")


def _ensure_cloudflared() -> None:
    """바이너리 없으면 다운로드 + SHA256 검증(가능 시) 후 저장 (chmod 0o755)."""
    CERBERUS_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.path.exists(_CF_BINARY):
        return
    print("[TUNNEL] cloudflared 다운로드 중...", flush=True)

    # SHA256 체크섬 — 릴리즈에 없으면 검증 스킵
    expected_sha = None
    try:
        import socket as _socket
        _old_timeout = _socket.getdefaulttimeout()
        _socket.setdefaulttimeout(10)
        try:
            with urllib.request.urlopen(_CF_SHA256_URL) as resp:
                sha_line = resp.read().decode().strip()
        finally:
            _socket.setdefaulttimeout(_old_timeout)
        expected_sha = sha_line.split()[0]
    except Exception:
        print("[TUNNEL] sha256sum 파일 없음 — 검증 스킵", flush=True)

    # 바이너리 다운로드
    import socket as _socket
    _old_timeout = _socket.getdefaulttimeout()
    _socket.setdefaulttimeout(60)
    try:
        with urllib.request.urlopen(_CF_URL) as resp:
            binary_data = resp.read()
    finally:
        _socket.setdefaulttimeout(_old_timeout)

    # SHA256 검증 (체크섬 파일이 있을 때만)
    if expected_sha:
        actual_sha = hashlib.sha256(binary_data).hexdigest()
        if actual_sha != expected_sha:
            raise RuntimeError(f"SHA256 불일치: expected={expected_sha} actual={actual_sha}")

    # 저장 + 권한 설정
    tmp_path = _CF_BINARY + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(binary_data)
    os.chmod(tmp_path, 0o755)
    os.replace(tmp_path, _CF_BINARY)
    print(f"[TUNNEL] cloudflared 설치 완료: {_CF_BINARY}", flush=True)


def _cf_log_path(port: int) -> Path:
    return _CF_LOG_DIR / f"cf_{port}.log"


def _is_alive(pid: int) -> bool:
    """PID가 살아있으면 True. 좀비(defunct) 프로세스는 False 처리."""
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False
    # kill -0 성공해도 좀비면 실제로 죽은 것
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        if "\nState:\tZ" in status:
            return False
    except Exception:
        pass
    return True


def _save_tunnel_state() -> None:
    """현재 _tunnels 상태를 JSON으로 저장 (서버 재시작 후 복원용).
    원자적 쓰기(tmp+replace) — 서버/봇 다중 프로세스 동시 저장 시 파일 손상 방지."""
    with _tlock:
        items = list(_tunnels.items())
    data = []
    for port, info in items:
        if _is_alive(info.pid):
            data.append({"port": port, "pid": info.pid, "url": info.url,
                         "opened_at": info.opened_at, "track_activity": info.track_activity,
                         "metrics_port": info.metrics_port,
                         "metrics_requests": info.metrics_requests})
    tmp_path = f"{_CF_STATE_FILE}.{os.getpid()}.tmp"
    try:
        Path(tmp_path).write_text(json.dumps(data))
        os.replace(tmp_path, _CF_STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def restore_tunnels() -> int:
    """서버 재시작 시 살아있는 cloudflared 프로세스를 _tunnels에 복원."""
    if not _CF_STATE_FILE.exists():
        return 0
    try:
        data = json.loads(_CF_STATE_FILE.read_text())
    except Exception:
        return 0
    restored = 0
    for item in data:
        port, pid, url = item["port"], item["pid"], item["url"]
        if not _is_alive(pid):
            continue
        # metrics_port: JSON에 있으면 사용, 없으면 로그 재파싱
        metrics_port = item.get("metrics_port", 0)
        if not metrics_port:
            try:
                log_text = _cf_log_path(port).read_text(errors="replace")
                mp = re.search(r"Starting metrics server on 127\.0\.0\.1:(\d+)", log_text)
                if mp:
                    metrics_port = int(mp.group(1))
            except Exception:
                pass
        info = TunnelInfo(
            pid=pid, url=url, port=port, proc=None,
            opened_at=item.get("opened_at", time.time()),
            track_activity=item.get("track_activity", False),
            metrics_port=metrics_port,
            metrics_requests=item.get("metrics_requests", 0),
        )
        with _tlock:
            if port not in _tunnels:
                _tunnels[port] = info
                restored += 1
    if restored:
        print(f"[TUNNEL] 기존 터널 {restored}개 복원됨", flush=True)
    return restored


def sync_tunnels_from_file() -> None:
    """tunnels.json ↔ _tunnels 양방향 동기화.
    다중 프로세스(서버+봇) 환경에서 in-memory 상태를 파일 기준으로 맞춤.
    os.kill 호출(syscall)은 락 외부에서 수행하여 락 보유 시간 최소화."""
    if not _CF_STATE_FILE.exists():
        return
    try:
        file_data = {item["port"]: item
                     for item in json.loads(_CF_STATE_FILE.read_text())}
    except Exception:
        return

    # 1. 스냅샷 수집 (락 내, syscall 없음)
    with _tlock:
        current_snapshot = {p: info.pid for p, info in _tunnels.items()}

    # 2. 생사 확인 (os.kill syscall, 락 외부)
    to_remove = [p for p, pid in current_snapshot.items()
                 if p not in file_data or not _is_alive(pid)]
    to_add = {p: item for p, item in file_data.items()
              if p not in current_snapshot and _is_alive(item["pid"])}

    if not to_remove and not to_add:
        return

    # 3. 상태 반영 (락 내)
    with _tlock:
        for port in to_remove:
            _tunnels.pop(port, None)
        for port, item in to_add.items():
            if port not in _tunnels:
                metrics_port = item.get("metrics_port", 0)
                if not metrics_port:
                    try:
                        log_text = _cf_log_path(port).read_text(errors="replace")
                        mp = re.search(r"Starting metrics server on 127\.0\.0\.1:(\d+)", log_text)
                        if mp:
                            metrics_port = int(mp.group(1))
                    except Exception:
                        pass
                _tunnels[port] = TunnelInfo(
                    pid=item["pid"], url=item["url"], port=port, proc=None,
                    opened_at=item.get("opened_at", time.time()),
                    track_activity=item.get("track_activity", False),
                    metrics_port=metrics_port,
                    metrics_requests=item.get("metrics_requests", 0),
                )


def _start_cloudflared(port: int) -> tuple[Optional[subprocess.Popen], str, int]:
    """cloudflared를 독립 프로세스(새 세션)로 실행.
    로그 파일에서 trycloudflare.com URL + 메트릭 포트 추출 (최대 20초 대기).
    반환: (proc, url, metrics_port) — 메트릭 포트 미확인 시 0."""
    _CF_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _cf_log_path(port)
    log_path.write_bytes(b"")  # 초기화
    cmd = [_CF_BINARY, "tunnel", "--url", f"http://127.0.0.1:{port}"]
    with open(log_path, "wb") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,   # 서버 종료 시 cloudflared 유지
        )
    url = ""
    metrics_port = 0
    deadline = time.time() + 20
    while time.time() < deadline:
        time.sleep(0.3)
        try:
            text = log_path.read_text(errors="replace")
        except Exception:
            text = ""
        if not url:
            m = re.search(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com", text)
            if m:
                url = m.group(0)
        if not metrics_port:
            mp = re.search(r"Starting metrics server on 127\.0\.0\.1:(\d+)", text)
            if mp:
                metrics_port = int(mp.group(1))
        if url and metrics_port:
            break
        if not _is_alive(proc.pid):
            if "429" in text or "Too Many Requests" in text:
                url = "ERROR:429"
            else:
                err_lines = [l for l in text.splitlines()
                             if "ERR" in l or "error" in l.lower() or "failed" in l.lower()]
                url = "ERROR:" + (err_lines[-1].strip() if err_lines else "알 수 없는 오류")
            break
    if not url:
        url = "ERROR:타임아웃 (URL 미수신)"
    return proc, url, metrics_port


def open_tunnel(port: int) -> str:
    """해당 포트 터널 개통. 이미 열려 있으면 기존 URL 반환."""
    s = _S()
    if not (1 <= port <= 65535):
        return s["invalid_port"].format(port=port)
    with _tlock:
        if port in _tunnels:
            existing = _tunnels[port]
            return s["already_open"].format(port=port, url=existing.url)
    _ensure_cloudflared()
    proc, url, metrics_port = _start_cloudflared(port)
    if url.startswith("ERROR:"):
        reason = url[6:]
        if proc and _is_alive(proc.pid):
            try:
                os.kill(proc.pid, signal.SIGTERM)
            except Exception:
                pass
        if "429" in reason:
            return s["open_fail_429"].format(port=port)
        return s["open_fail"].format(port=port, reason=reason)
    pid = proc.pid
    # 메트릭 폴링 초기 기준값 설정
    initial_requests = 0
    if metrics_port > 0:
        temp_info = TunnelInfo(pid=pid, url=url, port=port, metrics_port=metrics_port)
        v = _poll_tunnel_metrics(temp_info)
        if v >= 0:
            initial_requests = v
    info = TunnelInfo(pid=pid, proc=proc, url=url, port=port,
                      track_activity=True,  # 기본: 유휴 타임아웃 적용 (🔒로 영구 고정 가능)
                      metrics_port=metrics_port,
                      metrics_requests=initial_requests)
    with _tlock:
        _tunnels[port] = info
    _save_tunnel_state()
    return s["open_ok"].format(port=port, url=url)


def _kill_cf(info: TunnelInfo) -> None:
    """cloudflared 프로세스를 PID로 종료."""
    try:
        os.kill(info.pid, signal.SIGTERM)
    except Exception:
        pass


def close_tunnel(port: int, reason: str = "") -> str:
    """특정 포트 터널 종료."""
    s = _S()
    with _tlock:
        info = _tunnels.pop(port, None)
    if info is None:
        return s["port_not_found"]
    _kill_cf(info)
    _save_tunnel_state()
    return s["close_ok"].format(reason=reason or s["reason_manual"])


def close_all_tunnels(reason: str = "") -> str:
    """모든 터널 종료. 닫은 포트 목록 반환."""
    s = _S()
    with _tlock:
        ports = list(_tunnels.keys())
        infos = dict(_tunnels)
        _tunnels.clear()
    if not ports:
        return s["no_tunnels"]
    for info in infos.values():
        _kill_cf(info)
    _save_tunnel_state()
    return s["close_all_ok"].format(reason=reason or s["reason_manual"], ports=ports)


def close_idle_tunnels(reason: str = "") -> list[int]:
    """track_activity=True(⏱ 자동 종료 대상) 터널만 종료. 영구(🔒) 터널은 유지.
    서버 유휴 자동 종료 시 호출 — 노출 터널을 닫아 보안 유지. 닫은 포트 목록 반환."""
    with _tlock:
        targets = {p: info for p, info in _tunnels.items() if info.track_activity}
        for p in targets:
            _tunnels.pop(p, None)
    if not targets:
        return []
    for info in targets.values():
        _kill_cf(info)
    _save_tunnel_state()
    return list(targets.keys())


def tunnel_status() -> str:
    """전체 활성 터널 목록 문자열 반환."""
    s = _S()
    with _tlock:
        items = list(_tunnels.items())
    if not items:
        return s["offline"]
    lines = []
    now = time.time()
    for port, info in items:
        idle_sec = int(now - info.last_activity)
        lock_icon = "🔒" if not info.track_activity else "⏱"
        lines.append(s["status_item"].format(port=port, url=info.url, lock=lock_icon, idle=idle_sec))
    return "\n".join(lines)


def tunnel_status_data() -> list:
    """활성 터널 구조화 데이터 (HTTP API용). 다중 프로세스 상태 동기화 포함."""
    sync_tunnels_from_file()
    with _tlock:
        items = list(_tunnels.items())
    now = time.time()
    result = []
    for port, info in items:
        result.append({
            "port": port,
            "url": info.url,
            "idle_sec": int(now - info.last_activity),
            "opened_at": info.opened_at,
            "track_activity": info.track_activity,
        })
    return result


def toggle_tunnel_lock(port: int) -> str:
    """track_activity 토글. True→유휴 자동 종료, False→영구 유지."""
    s = _S()
    with _tlock:
        info = _tunnels.get(port)
        if info is None:
            return s["port_not_found"]
        info.track_activity = not info.track_activity
        state = info.track_activity
    _save_tunnel_state()
    label = s["lock_auto"] if state else s["lock_permanent"]
    return s["lock_ok"].format(port=port, label=label)


def tunnel_status_text() -> str:
    """활성 터널 사람이 읽는 텍스트 (봇 메시지용)."""
    return tunnel_status()


def _idle_watchdog(cfg: Config) -> None:
    """5초 주기로 _tunnels 순회, idle_timeout 초과 포트 자동 종료 + _notify_all.
    os.kill 호출은 락 외부에서 수행. is_timeout 불리언으로 종류 구분."""
    while True:
        time.sleep(5)
        sync_tunnels_from_file()
        if cfg.idle_timeout <= 0:
            continue
        now = time.time()
        timeout_sec = cfg.idle_timeout * 60

        # 1. 스냅샷 수집 (락 내, syscall 없음)
        with _tlock:
            snapshot = list(_tunnels.items())

        # 1-b. 메트릭 폴링 — track_activity + metrics_port > 0 인 터널만
        #      total_requests 증가분이 있으면 실 사용자 트래픽 → last_activity 갱신.
        #      각 폴링은 HTTP(최대 2초)이므로 병렬 실행하여 루프 지연 최소화.
        pollable = [(p, info) for p, info in snapshot
                    if info.track_activity and info.metrics_port > 0]
        if pollable:
            max_workers = min(8, len(pollable))
            with ThreadPoolExecutor(max_workers=max_workers) as _ex:
                polled = list(_ex.map(
                    lambda pi: (pi[0], _poll_tunnel_metrics(pi[1])), pollable
                ))
            for _p, current in polled:
                if current < 0:
                    continue
                with _tlock:
                    live = _tunnels.get(_p)
                    if live is None:
                        continue
                    if current > live.metrics_requests:
                        live.last_activity = now
                        live.metrics_requests = current

        # 2. 생사 / 타임아웃 판별 (os.kill syscall, 락 외부)
        to_kill: list[tuple[int, bool, TunnelInfo]] = []  # (port, is_timeout, info)
        for port, info in snapshot:
            if not _is_alive(info.pid):
                to_kill.append((port, False, info))
            elif info.track_activity and (now - info.last_activity) > timeout_sec:
                to_kill.append((port, True, info))

        if not to_kill:
            continue

        # 3. 목록에서 제거 (락 내)
        with _tlock:
            for port, _, _ in to_kill:
                _tunnels.pop(port, None)

        # 4. 타임아웃 케이스만 SIGTERM (이미 죽은 프로세스는 스킵)
        for _port, is_timeout, info in to_kill:
            if is_timeout:
                _kill_cf(info)

        _save_tunnel_state()

        # 5. 알림 (언어 고려, 문자열 파싱 없음)
        s = _S()
        for port, is_timeout, _ in to_kill:
            reason = s["timeout_reason"].format(n=cfg.idle_timeout) if is_timeout else s["proc_died"]
            _notify_all(s["watchdog_msg"].format(port=port, reason=reason))
