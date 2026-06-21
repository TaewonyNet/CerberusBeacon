"""
src/metrics.py — collect_metrics, MetricsHandler
SPEC.md §4
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import tornado.web

from src.agent import AgentAuthMixin

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# 런타임 설정 (run_server에서 주입)
_metrics_disk_path: str = ""


def collect_metrics() -> dict:
    """psutil 있으면 psutil 사용, 없으면 /proc fallback."""
    disk_path = _metrics_disk_path or str(Path.home())

    if _HAS_PSUTIL:
        cpu_pct = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        du = psutil.disk_usage(disk_path)
        load_avg = list(os.getloadavg())
        uptime_sec = int(time.time() - psutil.boot_time())

        return {
            "cpu_pct": cpu_pct,
            "cpu_available": True,
            "mem_pct": vm.percent,
            "mem_used_gb": round(vm.used / 1e9, 2),
            "mem_total_gb": round(vm.total / 1e9, 2),
            "disk_pct": du.percent,
            "disk_used_gb": round(du.used / 1e9, 2),
            "disk_total_gb": round(du.total / 1e9, 2),
            "load_avg": load_avg,
            "uptime_sec": uptime_sec,
        }
    else:
        # /proc fallback (Linux 전용)
        # Memory
        mem_info = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        mem_info[parts[0].rstrip(":")] = int(parts[1])
            mem_total_kb = mem_info.get("MemTotal", 1)
            mem_available_kb = mem_info.get("MemAvailable", 0)
            mem_used_kb = mem_total_kb - mem_available_kb
            mem_pct = round(mem_used_kb / mem_total_kb * 100, 1) if mem_total_kb else 0.0
            mem_used_gb = round(mem_used_kb / 1e6, 2)
            mem_total_gb = round(mem_total_kb / 1e6, 2)
        except Exception:
            mem_pct = 0.0
            mem_used_gb = 0.0
            mem_total_gb = 0.0

        # Disk
        try:
            sv = os.statvfs(disk_path)
            disk_total = sv.f_blocks * sv.f_frsize
            disk_free = sv.f_bavail * sv.f_frsize
            disk_used = disk_total - disk_free
            disk_pct = round(disk_used / disk_total * 100, 1) if disk_total else 0.0
            disk_used_gb = round(disk_used / 1e9, 2)
            disk_total_gb = round(disk_total / 1e9, 2)
        except Exception:
            disk_pct = 0.0
            disk_used_gb = 0.0
            disk_total_gb = 0.0

        # Load avg
        try:
            load_avg = list(os.getloadavg())
        except Exception:
            try:
                with open("/proc/loadavg") as f:
                    parts = f.read().split()
                load_avg = [float(parts[0]), float(parts[1]), float(parts[2])]
            except Exception:
                load_avg = [0.0, 0.0, 0.0]

        # Uptime
        try:
            with open("/proc/uptime") as f:
                uptime_sec = int(float(f.read().split()[0]))
        except Exception:
            uptime_sec = 0

        return {
            "cpu_pct": 0.0,  # /proc/stat 비블로킹 CPU는 불가, 0.0 반환
            "cpu_available": False,
            "mem_pct": mem_pct,
            "mem_used_gb": mem_used_gb,
            "mem_total_gb": mem_total_gb,
            "disk_pct": disk_pct,
            "disk_used_gb": disk_used_gb,
            "disk_total_gb": disk_total_gb,
            "load_avg": load_avg,
            "uptime_sec": uptime_sec,
        }


class MetricsHandler(AgentAuthMixin, tornado.web.RequestHandler):
    """GET /api/metrics → collect_metrics() JSON"""

    def get(self):
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(collect_metrics()))
