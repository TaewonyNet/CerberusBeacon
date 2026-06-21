"""
tests/test_files.py — safe_path, traversal, upload
SPEC.md §3
"""
import os
from pathlib import Path
from unittest.mock import patch

import pytest

import src.files as files
import src.auth as auth


# ══════════════════════════════════════════════════════════════════════════════
# 4. 파일 트리 (SPEC.md §3)
# ══════════════════════════════════════════════════════════════════════════════

class TestFileTree:
    def test_safe_path_allows_valid(self, tmp_path):
        """정상 경로는 _safe_path 통과."""
        sub = tmp_path / "subdir"
        sub.mkdir()
        result = files._safe_path(str(sub), tmp_path)
        assert result == sub.resolve()

    def test_safe_path_rejects_traversal(self, tmp_path):
        """../etc/passwd 등 시도 → ValueError."""
        with pytest.raises(ValueError):
            files._safe_path(str(tmp_path / ".." / "etc"), tmp_path)

    def test_safe_path_rejects_cerberus_dir(self, tmp_path):
        """~/.cerberus 접근 → ValueError (하드코딩 차단)."""
        cerberus_real = Path(os.path.realpath(str(auth.CERBERUS_DIR)))
        with pytest.raises(ValueError, match="cerberus"):
            files._safe_path(str(cerberus_real), cerberus_real.parent)

    def test_hidden_files_excluded_by_default(self, tmp_path):
        """숨김 파일(.)은 기본 제외."""
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("public")

        shown = []
        with os.scandir(tmp_path) as it:
            for entry in it:
                if not entry.name.startswith("."):
                    shown.append(entry.name)
        assert "visible.txt" in shown
        assert ".hidden" not in shown

    def test_hidden_files_included_when_flag_set(self, tmp_path):
        """show_hidden=True 시 숨김 파일 포함."""
        (tmp_path / ".hidden").write_text("secret")
        (tmp_path / "visible.txt").write_text("public")

        shown = []
        with os.scandir(tmp_path) as it:
            for entry in it:
                shown.append(entry.name)
        assert "visible.txt" in shown
        assert ".hidden" in shown

    def test_traversal_attempt_rejected(self, tmp_path):
        """../etc/passwd 등 시도 → ValueError."""
        with pytest.raises(ValueError):
            files._safe_path(str(tmp_path / ".." / "other"), tmp_path)


# ══════════════════════════════════════════════════════════════════════════════
# 5. 파일 업로드 (SPEC.md §3)
# ══════════════════════════════════════════════════════════════════════════════

class TestFileUpload:
    def test_safe_path_allows_subdir(self, tmp_path):
        """FILES_ROOT 내부 경로는 허용."""
        sub = tmp_path / "uploads"
        sub.mkdir()
        result = files._safe_path(str(sub), tmp_path)
        assert result == sub.resolve()

    def test_upload_outside_root_rejected(self, tmp_path):
        """FILES_ROOT 범위 밖 path → ValueError."""
        other = tmp_path.parent / "other_dir"
        other.mkdir(exist_ok=True)
        with pytest.raises(ValueError):
            files._safe_path(str(other), tmp_path)
