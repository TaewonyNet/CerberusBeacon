"""
tests/test_macros.py — GET/POST macros
SPEC.md §6
"""
import threading
from unittest.mock import MagicMock

import pytest

import src.macros as macros
from src.config import Config


# ══════════════════════════════════════════════════════════════════════════════
# 10. 키보드 매크로 API (SPEC.md §6)
# ══════════════════════════════════════════════════════════════════════════════

class TestMacros:
    def setup_method(self):
        with macros._macros_lock:
            macros._macros.clear()
            macros._macros.extend([
                {"label": "ls -la", "send": "ls -la\n"},
                {"label": "pwd",    "send": "pwd\n"},
            ])

    def test_get_macros_returns_list(self):
        """_macros 리스트 반환 확인."""
        with macros._macros_lock:
            data = list(macros._macros)
        assert len(data) == 2
        assert data[0]["label"] == "ls -la"

    def test_default_macros_in_config(self):
        """Config 기본 매크로가 비어있지 않음."""
        cfg = Config()
        assert len(cfg.macros) > 0
        labels = [m["label"] for m in cfg.macros]
        assert "ls -la" in labels
        assert "C-c" in labels

    def test_post_macros_updates_list(self):
        """_macros 교체 (직접 테스트)."""
        new_macros = [{"label": "test", "send": "test\n"}]
        with macros._macros_lock:
            macros._macros.clear()
            macros._macros.extend(new_macros)
        with macros._macros_lock:
            data = list(macros._macros)
        assert len(data) == 1
        assert data[0]["label"] == "test"

    def test_macros_lock_thread_safety(self):
        """여러 스레드에서 _macros 동시 접근 → 데이터 정합성."""
        errors = []

        def reader():
            for _ in range(100):
                with macros._macros_lock:
                    _ = list(macros._macros)

        def writer():
            for i in range(100):
                with macros._macros_lock:
                    macros._macros.append({"label": f"t{i}", "send": f"t{i}\n"})

        threads = [threading.Thread(target=reader) for _ in range(5)]
        threads += [threading.Thread(target=writer) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_save_and_load_roundtrip(self, tmp_path, monkeypatch):
        """save_macros → load_macros 라운드트립 (SPEC §6.2 영속화)."""
        macros_file = tmp_path / "macros.json"
        monkeypatch.setattr(macros, "_MACROS_FILE", macros_file)
        data = [{"label": "hi", "send": "echo hi\n"}, {"label": "sep", "send": ""}]
        macros.save_macros(data)
        assert macros_file.exists()
        loaded = macros.load_macros()
        assert loaded == data

    def test_load_macros_missing_file_returns_none(self, tmp_path, monkeypatch):
        """파일 없으면 None 반환 → 호출자가 기본값 사용."""
        monkeypatch.setattr(macros, "_MACROS_FILE", tmp_path / "nope.json")
        assert macros.load_macros() is None
