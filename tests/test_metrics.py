"""
tests/test_metrics.py — psutil/proc 두 경로
SPEC.md §4
"""
from unittest.mock import patch

import pytest

import src.metrics as metrics


# ══════════════════════════════════════════════════════════════════════════════
# 6. 시스템 메트릭 (SPEC.md §4)
# ══════════════════════════════════════════════════════════════════════════════

class TestMetrics:
    def test_psutil_path_returns_required_keys(self):
        """psutil mock → collect_metrics() JSON 스키마 검증."""
        required = {"cpu_pct", "mem_pct", "mem_used_gb", "mem_total_gb",
                    "disk_pct", "disk_used_gb", "disk_total_gb", "load_avg", "uptime_sec"}
        if not metrics._HAS_PSUTIL:
            pytest.skip("psutil not installed")
        m = metrics.collect_metrics()
        assert required <= set(m.keys())

    def test_proc_fallback_returns_required_keys(self):
        """psutil 없을 때 /proc 경로도 동일 스키마 반환."""
        required = {"cpu_pct", "mem_pct", "mem_used_gb", "mem_total_gb",
                    "disk_pct", "disk_used_gb", "disk_total_gb", "load_avg", "uptime_sec"}
        with patch.object(metrics, "_HAS_PSUTIL", False):
            m = metrics.collect_metrics()
        assert required <= set(m.keys())

    def test_metrics_all_numeric(self):
        """collect_metrics() 결과의 모든 값이 숫자형."""
        if not metrics._HAS_PSUTIL:
            pytest.skip("psutil not installed")
        m = metrics.collect_metrics()
        assert isinstance(m["cpu_pct"], (int, float))
        assert isinstance(m["mem_pct"], (int, float))
        assert isinstance(m["uptime_sec"], int)
        assert isinstance(m["load_avg"], list)
        assert len(m["load_avg"]) == 3
