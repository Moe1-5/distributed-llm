"""
gpu_monitor.py
Collects GPU and CPU usage statistics for the dashboard.
Runs in a background thread and caches results so the
FastAPI endpoint can read them without blocking.
"""

import threading
import time
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
        stats = {
            "cpu_percent":  psutil.cpu_percent(interval=None),
            "ram_percent":  psutil.virtual_memory().percent,
            "ram_used_gb":  round(psutil.virtual_memory().used  / 1024**3, 1),
            "ram_total_gb": round(psutil.virtual_memory().total / 1024**3, 1),
            "gpu":          None,
        }

        if torch.cuda.is_available():
            try:
                gpu_id    = torch.cuda.current_device()
                props     = torch.cuda.get_device_properties(gpu_id)
                mem_used  = torch.cuda.memory_allocated(gpu_id)
                mem_total = props.total_memory

                stats["gpu"] = {
                    "name":        props.name,
                    "util_percent": self._get_gpu_utilization(gpu_id),
                    "vram_used_gb":  round(mem_used  / 1024**3, 2),
                    "vram_total_gb": round(mem_total / 1024**3, 2),
                    "vram_percent":  round(mem_used / mem_total * 100, 1),
                }
            except Exception as e:
                logger.warning(f"GPU stats collection failed: {e}")

        return stats

    def _get_gpu_utilization(self, gpu_id: int) -> float:
        """
        Try to get real GPU utilization % via pynvml.
        Falls back to 0.0 if pynvml is not available.
        """
        try:
            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(gpu_id)
            util   = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return float(util.gpu)
        except Exception:
            # pynvml not installed or not accessible — return 0
            return 0.0

    def _empty_stats(self) -> dict:
        return {
            "cpu_percent":  0.0,
            "ram_percent":  0.0,
            "ram_used_gb":  0.0,
            "ram_total_gb": 0.0,
            "gpu":          None,
        }
