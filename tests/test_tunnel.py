"""
tests/test_tunnel.py — 터널 open/close, thread-safety, cloudflared, status, notify
SPEC.md §2
"""
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.tunnel as tunnel


# ══════════════════════════════════════════════════════════════════════════════
# 3. 멀티 터널 (SPEC.md §2)
# ══════════════════════════════════════════════════════════════════════════════

class TestTunnel:
    def setup_method(self):
        with tunnel._tlock:
            tunnel._tunnels.clear()

    def test_open_tunnel_creates_entry(self):
        """open_tunnel(8765) → _tunnels[8765] 존재."""
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        with patch("src.tunnel._ensure_cloudflared"), \
             patch("src.tunnel._start_cloudflared",
                   return_value=(fake_proc, "https://x.trycloudflare.com", 0)):
            msg = tunnel.open_tunnel(8765)
        assert "trycloudflare.com" in msg
        assert 8765 in tunnel._tunnels

    def test_open_tunnel_already_open_returns_existing(self):
        """같은 포트 재호출 → 기존 URL 반환."""
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        with patch("src.tunnel._ensure_cloudflared"), \
             patch("src.tunnel._start_cloudflared",
                   return_value=(fake_proc, "https://first.trycloudflare.com", 0)):
            tunnel.open_tunnel(8765)

        with patch("src.tunnel._ensure_cloudflared") as mock_ensure, \
             patch("src.tunnel._start_cloudflared") as mock_start:
            msg = tunnel.open_tunnel(8765)
            mock_start.assert_not_called()

        assert "first.trycloudflare.com" in msg or "이미 열려 있음" in msg

    def test_close_tunnel_removes_entry(self):
        """open → close → _tunnels[port] 없음."""
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        with patch("src.tunnel._ensure_cloudflared"), \
             patch("src.tunnel._start_cloudflared",
                   return_value=(fake_proc, "https://x.trycloudflare.com", 0)):
            tunnel.open_tunnel(9000)

        assert 9000 in tunnel._tunnels
        msg = tunnel.close_tunnel(9000)
        assert 9000 not in tunnel._tunnels
        assert "🔒" in msg

    def test_close_all_tunnels(self):
        """여러 포트 open → close_all → _tunnels 비어있음."""
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        for port in [8001, 8002, 8003]:
            with patch("src.tunnel._ensure_cloudflared"), \
                 patch("src.tunnel._start_cloudflared",
                       return_value=(fake_proc, f"https://p{port}.trycloudflare.com", 0)):
                tunnel.open_tunnel(port)

        assert len(tunnel._tunnels) == 3
        msg = tunnel.close_all_tunnels()
        assert len(tunnel._tunnels) == 0

    def test_close_idle_tunnels_keeps_permanent(self):
        """close_idle_tunnels: 자동종료(⏱) 터널만 닫고 영구(🔒) 터널은 유지."""
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        for port in [7001, 7002]:
            with patch("src.tunnel._ensure_cloudflared"), \
                 patch("src.tunnel._start_cloudflared",
                       return_value=(fake_proc, f"https://p{port}.trycloudflare.com", 0)):
                tunnel.open_tunnel(port)
        # 7002를 영구(track_activity=False)로 전환
        with tunnel._tlock:
            tunnel._tunnels[7002].track_activity = False
        with patch("src.tunnel._kill_cf"), patch("src.tunnel._save_tunnel_state"):
            closed = tunnel.close_idle_tunnels("test")
        assert closed == [7001]
        assert 7001 not in tunnel._tunnels
        assert 7002 in tunnel._tunnels  # 영구 터널 유지

    def test_close_idle_tunnels_empty(self):
        """활성 터널 없거나 전부 영구면 빈 리스트 반환."""
        with tunnel._tlock:
            tunnel._tunnels.clear()
        with patch("src.tunnel._kill_cf"), patch("src.tunnel._save_tunnel_state"):
            assert tunnel.close_idle_tunnels() == []

    def test_thread_safety_open_close(self):
        """동시에 여러 스레드에서 open/close 호출 → 데이터 정합성.
        patch는 스레드 비안전이므로 스레드 시작 전 메인 스레드에서 적용."""
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        errors = []

        def worker(port):
            try:
                tunnel.open_tunnel(port)
                time.sleep(0.01)
                tunnel.close_tunnel(port)
            except Exception as e:
                errors.append(e)

        # patch는 스레드 외부에서 일괄 적용
        with patch("src.tunnel._ensure_cloudflared"), \
             patch("src.tunnel._start_cloudflared",
                   side_effect=lambda port: (fake_proc, f"https://p{port}.trycloudflare.com", 0)):
            threads = [threading.Thread(target=worker, args=(8100 + i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors

    def test_tunnel_status_offline(self):
        assert "🔴" in tunnel.tunnel_status()

    def test_tunnel_status_shows_active(self):
        """터널 열린 상태에서 status → 포트/URL 포함."""
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        with patch("src.tunnel._ensure_cloudflared"), \
             patch("src.tunnel._start_cloudflared",
                   return_value=(fake_proc, "https://abc.trycloudflare.com", 0)):
            tunnel.open_tunnel(7777)

        status = tunnel.tunnel_status()
        assert "7777" in status
        assert "abc.trycloudflare.com" in status


# ══════════════════════════════════════════════════════════════════════════════
# 7. cloudflared 유틸리티
# ══════════════════════════════════════════════════════════════════════════════

class TestEnsureCloudflared:
    def test_skips_download_when_binary_exists(self):
        with patch("os.path.exists", return_value=True), \
             patch("src.tunnel.urllib.request.urlopen") as mock_urlopen:
            tunnel._ensure_cloudflared()
            mock_urlopen.assert_not_called()

    def test_raises_on_sha256_mismatch(self):
        fake_binary = b"\x7fELF" + b"\x00" * 100

        def _side(url):
            ctx = MagicMock()
            if "sha256" in url:
                ctx.__enter__ = MagicMock(return_value=MagicMock(
                    read=MagicMock(return_value=b"deadbeef" * 8 + b"  cloudflared-linux-amd64\n")
                ))
            else:
                ctx.__enter__ = MagicMock(return_value=MagicMock(
                    read=MagicMock(return_value=fake_binary)
                ))
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        with patch("os.path.exists", return_value=False), \
             patch("src.tunnel.urllib.request.urlopen", side_effect=_side):
            with pytest.raises(RuntimeError, match="SHA256 불일치"):
                tunnel._ensure_cloudflared()


# ══════════════════════════════════════════════════════════════════════════════
# 8. 알림 콜백
# ══════════════════════════════════════════════════════════════════════════════

class TestNotifyAll:
    def setup_method(self):
        with tunnel._notifiers_lock:
            tunnel._notifiers.clear()

    def test_calls_all_notifiers(self):
        called = []
        with tunnel._notifiers_lock:
            tunnel._notifiers.append(("a", lambda m: called.append(("a", m))))
            tunnel._notifiers.append(("b", lambda m: called.append(("b", m))))
        tunnel._notify_all("hello")
        assert ("a", "hello") in called
        assert ("b", "hello") in called

    def test_exclude_skips_named(self):
        called = []
        with tunnel._notifiers_lock:
            tunnel._notifiers.append(("telegram", lambda m: called.append("tg")))
            tunnel._notifiers.append(("slack", lambda m: called.append("sl")))
        tunnel._notify_all("msg", exclude="telegram")
        assert "sl" in called
        assert "tg" not in called

    def test_exception_does_not_stop_others(self):
        called = []
        with tunnel._notifiers_lock:
            tunnel._notifiers.append(("bad", lambda m: (_ for _ in ()).throw(RuntimeError("boom"))))
            tunnel._notifiers.append(("good", lambda m: called.append(m)))
        tunnel._notify_all("test")
        assert "test" in called


# ══════════════════════════════════════════════════════════════════════════════
# 13. tunnel_status_data (SPEC.md §2.2)
# ══════════════════════════════════════════════════════════════════════════════

class TestTunnelStatusData:
    def setup_method(self):
        with tunnel._tlock:
            tunnel._tunnels.clear()
        # 다중 프로세스 동기화 파일도 초기화
        if tunnel._CF_STATE_FILE.exists():
            tunnel._CF_STATE_FILE.unlink()

    def test_empty_returns_empty_list(self):
        result = tunnel.tunnel_status_data()
        assert result == []

    def test_returns_correct_schema(self):
        """터널 있을 때 정확한 스키마 반환."""
        import os
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.pid = os.getpid()  # 실제 정수 PID로 _is_alive() 통과
        with patch("src.tunnel._ensure_cloudflared"), \
             patch("src.tunnel._start_cloudflared",
                   return_value=(fake_proc, "https://test.trycloudflare.com", 0)):
            tunnel.open_tunnel(9999)

        data = tunnel.tunnel_status_data()
        assert len(data) == 1
        item = data[0]
        assert item["port"] == 9999
        assert "url" in item
        assert "idle_sec" in item
        assert "opened_at" in item
        assert "track_activity" in item
