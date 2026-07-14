import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import psutil

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from node.gpu_monitor import GPUMonitor


def test_collect_reports_machine_and_backend_process_metrics():
    monitor = GPUMonitor(interval=1.5)
    monitor._process = Mock(
        pid=4321,
        cpu_percent=Mock(return_value=7.5),
        memory_percent=Mock(return_value=1.25),
        memory_info=Mock(return_value=SimpleNamespace(rss=2 * 1024**3)),
        num_threads=Mock(return_value=9),
    )
    memory = SimpleNamespace(
        percent=50.0,
        used=8 * 1024**3,
        available=7 * 1024**3,
        total=16 * 1024**3,
    )

    with (
        patch("node.gpu_monitor.psutil.virtual_memory", return_value=memory),
        patch("node.gpu_monitor.psutil.cpu_percent", return_value=12.5),
        patch("node.gpu_monitor.psutil.cpu_count", return_value=8),
        patch("node.gpu_monitor.psutil.getloadavg", return_value=(0.5, 0.4, 0.3)),
        patch("node.gpu_monitor.torch.cuda.is_available", return_value=False),
    ):
        stats = monitor._collect()

    assert stats["sampled_at"].endswith("+00:00")
    assert stats["sample_interval_seconds"] == 1.5
    assert stats["cpu_percent"] == 12.5
    assert stats["cpu_count"] == 8
    assert stats["load_average_1m"] == 0.5
    assert stats["ram_available_gb"] == 7.0
    assert stats["process"] == {
        "pid": 4321,
        "cpu_percent": 7.5,
        "memory_percent": 1.25,
        "rss_gb": 2.0,
        "threads": 9,
    }
    assert stats["gpu"] is None


def test_collect_process_stats_returns_none_when_process_disappears():
    monitor = GPUMonitor()
    monitor._process = Mock()
    monitor._process.memory_info.side_effect = psutil.NoSuchProcess(1234)

    assert monitor._collect_process_stats() is None


def test_gpu_utilization_is_unavailable_when_nvml_cannot_initialize():
    monitor = GPUMonitor()

    with patch("node.gpu_monitor.pynvml.nvmlInit", side_effect=RuntimeError("no NVML")):
        assert monitor._get_gpu_utilization(0) is None
