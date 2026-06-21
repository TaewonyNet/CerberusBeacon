"""
src/files.py — _safe_path, FileTreeHandler, FileDownloadHandler, FileUploadHandler
SPEC.md §3
"""
from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING

import tornado.web

from src.auth import AuthMixin
from src.config import CERBERUS_DIR, Config


def _safe_path(requested: str, root: Path) -> Path:
    """경로 traversal 방지. ~/.cerberus 항상 차단."""
    real = Path(os.path.realpath(requested))
    cerberus_real = Path(os.path.realpath(str(CERBERUS_DIR)))
    # ~/.cerberus 하드코딩 차단
    if str(real).startswith(str(cerberus_real)):
        raise ValueError("~/.cerberus 접근 차단")
    root_real = Path(os.path.realpath(str(root)))
    if not str(real).startswith(str(root_real)):
        raise ValueError("path outside FILES_ROOT")
    return real


class FileTreeHandler(AuthMixin, tornado.web.RequestHandler):
    """GET /api/tree?path=<path>[&hidden=1]"""

    def initialize(self, files_root: Path, hidden: bool, cfg: Config) -> None:
        self._root = files_root
        self._hidden = hidden
        self._cfg = cfg
        self._exclude = [
            Path(os.path.realpath(os.path.expanduser(p)))
            for p in cfg.files_exclude
        ]

    def get(self):
        req_path = self.get_argument("path", str(self._root))
        show_hidden = bool(self.get_argument("hidden", "0") not in ("0", "", "false", "False"))

        try:
            real = _safe_path(req_path, self._root)
        except ValueError as e:
            self.set_status(403)
            self.write(json.dumps({"error": str(e)}))
            return

        if not real.exists():
            self.set_status(404)
            self.write(json.dumps({"error": "not found"}))
            return

        if not real.is_dir():
            self.set_status(400)
            self.write(json.dumps({"error": "not a directory"}))
            return

        dirs = []
        files = []
        try:
            with os.scandir(real) as it:
                for entry in it:
                    name = entry.name
                    if not show_hidden and not self._hidden and name.startswith("."):
                        continue
                    # 제외 경로 확인
                    entry_real = Path(os.path.realpath(entry.path))
                    excluded = any(
                        str(entry_real).startswith(str(ex))
                        for ex in self._exclude
                    )
                    if excluded:
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({"name": name, "path": entry.path})
                    else:
                        try:
                            size = entry.stat().st_size
                        except OSError:
                            size = 0
                        files.append({"name": name, "path": entry.path, "size": size})
        except PermissionError as e:
            self.set_status(403)
            self.write(json.dumps({"error": str(e)}))
            return

        dirs.sort(key=lambda x: x["name"])
        files.sort(key=lambda x: x["name"])

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"path": str(real), "dirs": dirs, "files": files}))


class FileDownloadHandler(AuthMixin, tornado.web.RequestHandler):
    """GET /api/download?path=<path>"""

    def initialize(self, files_root: Path) -> None:
        self._root = files_root

    async def get(self):
        req_path = self.get_argument("path", "")
        if not req_path:
            self.set_status(400)
            self.write(json.dumps({"error": "path required"}))
            return

        try:
            real = _safe_path(req_path, self._root)
        except ValueError as e:
            self.set_status(403)
            self.write(json.dumps({"error": str(e)}))
            return

        if not real.exists() or not real.is_file():
            self.set_status(404)
            self.write(json.dumps({"error": "not found"}))
            return

        filename = real.name
        self.set_header("Content-Type", "application/octet-stream")
        self.set_header(
            "Content-Disposition",
            f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
        )
        self.set_header("Content-Length", str(real.stat().st_size))

        chunk_size = 64 * 1024
        with open(real, "rb") as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                self.write(chunk)
                await self.flush()


class FileUploadHandler(AuthMixin, tornado.web.RequestHandler):
    """POST /api/upload?path=<dir>"""

    def initialize(self, files_root: Path, max_upload_bytes: int) -> None:
        self._root = files_root
        self._max_bytes = max_upload_bytes

    def post(self):
        req_path = self.get_argument("path", str(self._root))
        try:
            real_dir = _safe_path(req_path, self._root)
        except ValueError as e:
            self.set_status(403)
            self.write(json.dumps({"error": str(e)}))
            return

        if not real_dir.exists() or not real_dir.is_dir():
            self.set_status(404)
            self.write(json.dumps({"error": "directory not found"}))
            return

        files = self.request.files.get("file", [])
        if not files:
            self.set_status(400)
            self.write(json.dumps({"error": "no file uploaded"}))
            return

        saved = []
        for f in files:
            filename = os.path.basename(f["filename"])
            if not filename:
                continue
            body = f["body"]
            if len(body) > self._max_bytes:
                self.set_status(413)
                self.write(json.dumps({"error": f"파일 크기 초과: {filename}"}))
                return
            dest = real_dir / filename
            with open(dest, "wb") as out:
                out.write(body)
            saved.append(filename)

        self.set_header("Content-Type", "application/json")
        self.write(json.dumps({"saved": saved}))
