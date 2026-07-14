"""
gpu_monitor.py
Collects GPU and CPU usage statistics for the dashboard.
Runs in a background thread and caches results so the
FastAPI endpoint can read them without blocking.
"""

import os
import threading
import time
from datetime import datetime, timezone
from typing import Optional
import pynvml
import psutil
import torch
from hivemind.utils.logging import get_logger

logger = get_logger(__name__)


class GPUMonitor:
    """
    Polls GPU and CPU stats every N seconds and caches them.

    Usage:
        monitor = GPUMonitor(interval=2.0)
        monitor.start()
        stats = monitor.get_stats()
        monitor.stop()
    """

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self._process = psutil.Process(os.getpid())
        self._stats: dict = self._empty_stats()
        self._lock   = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        self._thread  = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="gpu-monitor",
        )
        self._thread.start()
        logger.debug("GPUMonitor started.")

    def stop(self) -> None:
        self._running = False
        logger.debug("GPUMonitor stopped.")

    # ------------------------------------------------------------------
    # Stats access
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return the latest cached stats. Safe to call from any thread."""
        with self._lock:
            return dict(self._stats)

    # ------------------------------------------------------------------
    # Internal polling
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        while self._running:
            try:
                stats = self._collect()
                with self._lock:
                    self._stats = stats
            except Exception as e:
                logger.warning(f"GPUMonitor poll error: {e}")
            time.sleep(self.interval)

    def _collect(self) -> dict:
        virtual_memory = psutil.virtual_memory()
        process_stats = self._collect_process_stats()
        try:
            load_average_1m = round(psutil.getloadavg()[0], 2)
        except (AttributeError, OSError):
            load_average_1m = None

        stats = {
            "sampled_at": datetime.now(timezone.utc).isoformat(),
            "sample_interval_seconds": self.interval,
            "cpu_percent": psutil.cpu_percent(interval=None),
            "cpu_count": psutil.cpu_count(),
            "load_average_1m": load_average_1m,
            "ram_percent": virtual_memory.percent,
            "ram_used_gb": round(virtual_memory.used / 1024**3, 2),
            "ram_available_gb": round(virtual_memory.available / 1024**3, 2),
            "ram_total_gb": round(virtual_memory.total / 1024**3, 2),
            "process": process_stats,
            "gpu": None,
        }

        if torch.cuda.is_available():
            try:
                gpu_id    = torch.cuda.current_device()
                props     = torch.cuda.get_device_properties(gpu_id)
                mem_used  = torch.cuda.memory_allocated(gpu_id)
                mem_reserved = torch.cuda.memory_reserved(gpu_id)
                mem_total = props.total_memory

                stats["gpu"] = {
                    "name":        props.name,
                    "util_percent": self._get_gpu_utilization(gpu_id),
                    "vram_used_gb":  round(mem_used  / 1024**3, 2),
                    "vram_reserved_gb": round(mem_reserved / 1024**3, 2),
                    "vram_total_gb": round(mem_total / 1024**3, 2),
                    "vram_percent":  round(mem_used / mem_total * 100, 1),
                }
            except Exception as e:
                logger.warning(f"GPU stats collection failed: {e}")

        return stats

    def _collect_process_stats(self) -> Optional[dict]:
        """Collect backend-process usage separately from machine-wide usage."""
        try:
            memory_info = self._process.memory_info()
            return {
                "pid": self._process.pid,
                "cpu_percent": round(self._process.cpu_percent(interval=None), 1),
                "memory_percent": round(self._process.memory_percent(), 2),
                "rss_gb": round(memory_info.rss / 1024**3, 3),
                "threads": self._process.num_threads(),
            }
        except (psutil.Error, OSError) as exc:
            logger.warning(f"Backend process stats collection failed: {exc}")
            return None

    def _get_gpu_utilization(self, gpu_id: int) -> Optional[float]:
        """
        Try to get real GPU utilization % via pynvml.
        Returns None if NVML is unavailable so callers do not mistake missing
        telemetry for an idle GPU.
        """
        try:
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
            util   = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return float(util.gpu)
        except Exception:
            return None

    def _empty_stats(self) -> dict:
        return {
            "sampled_at": None,
            "sample_interval_seconds": self.interval,
            "cpu_percent":  0.0,
            "cpu_count": psutil.cpu_count(),
            "load_average_1m": None,
            "ram_percent":  0.0,
            "ram_used_gb":  0.0,
            "ram_available_gb": 0.0,
            "ram_total_gb": 0.0,
            "process": None,
            "gpu":          None,
        }
