"""Small lifecycle job registry for long-running node and generator starts."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


ProgressCallback = Callable[[str, str], None]
JobTarget = Callable[[ProgressCallback, threading.Event], dict[str, Any]]
TERMINAL_STATES = {"ready", "failed", "cancelled"}
READY_RESULT_STATES = {"ready", "already_ready", "started", "already_running"}


@dataclass
class _LifecycleJob:
    job_id: str
    kind: str
    resource_key: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    status: str = "queued"
    stage: str = "queued"
    detail: str = "Waiting to start."
    result: dict[str, Any] | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "resource_key": self.resource_key,
            "status": self.status,
            "stage": self.stage,
            "detail": self.detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "elapsed_seconds": max(0.0, time.time() - self.created_at),
            "cancel_requested": self.cancel_event.is_set(),
            "result": self.result,
            "error": self.error,
        }


class LifecycleJobStore:
    def __init__(self, max_history: int = 100) -> None:
        if max_history < 1:
            raise ValueError("max_history must be positive")
        self._max_history = max_history
        self._jobs: dict[str, _LifecycleJob] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._admission_open = True
        self._lock = threading.RLock()

    def submit(self, kind: str, resource_key: str, target: JobTarget) -> dict[str, Any]:
        if not kind.strip() or not resource_key.strip():
            raise ValueError("kind and resource_key must not be empty")
        with self._lock:
            if not self._admission_open:
                self._prune_locked()
                detail = "Lifecycle job admission is closed while the backend is stopping."
                job = _LifecycleJob(
                    uuid4().hex,
                    kind,
                    resource_key,
                    status="cancelled",
                    stage="admission_closed",
                    detail=detail,
                    result={"status": "cancelled", "message": detail},
                    error=detail,
                )
                job.cancel_event.set()
                self._jobs[job.job_id] = job
                return {**job.snapshot(), "reused": False}

            existing = next(
                (
                    job
                    for job in self._jobs.values()
                    if job.resource_key == resource_key and job.status not in TERMINAL_STATES
                ),
                None,
            )
            if existing is not None:
                return {**existing.snapshot(), "reused": True}

            self._prune_locked()
            job = _LifecycleJob(uuid4().hex, kind, resource_key)
            self._jobs[job.job_id] = job
            thread = threading.Thread(
                target=self._run,
                args=(job, target),
                daemon=True,
                name=f"distribllm-{kind}-{job.job_id[:8]}",
            )
            self._threads[job.job_id] = thread
            try:
                thread.start()
            except Exception as exc:
                self._threads.pop(job.job_id, None)
                job.status = "failed"
                job.stage = "failed"
                job.error = f"Could not start lifecycle job thread: {exc}"
                job.detail = job.error
                job.updated_at = time.time()
            return {**job.snapshot(), "reused": False}

    @property
    def admission_open(self) -> bool:
        with self._lock:
            return self._admission_open

    def close_admission(self) -> None:
        """Prevent new targets from starting while preserving pollable results."""
        with self._lock:
            self._admission_open = False

    def reopen_admission(self) -> None:
        """Allow submissions after a previous, fully completed shutdown."""
        with self._lock:
            if self._threads:
                raise RuntimeError(
                    "Cannot reopen lifecycle job admission while workers are active."
                )
            self._admission_open = True

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "admission_open": self._admission_open,
                "active_thread_count": len(self._threads),
                "active_job_ids": sorted(self._threads),
            }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job is not None else None

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        if limit < 1:
            return []
        with self._lock:
            jobs = sorted(
                self._jobs.values(),
                key=lambda job: job.updated_at,
                reverse=True,
            )
            return [job.snapshot() for job in jobs[:limit]]

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status not in TERMINAL_STATES:
                job.cancel_event.set()
                job.stage = "cancelling"
                job.detail = "Cancellation requested; cleaning up owned resources."
                job.updated_at = time.time()
            return job.snapshot()

    def cancel_all(self) -> None:
        with self._lock:
            self._cancel_all_locked()

    def cancel_all_and_wait(self, timeout: float) -> bool:
        """Close admission, request cancellation, and join owned workers once.

        Returns ``True`` when every tracked worker has stopped before the shared
        deadline. Repeated calls are safe and can continue waiting for a target
        that did not honor an earlier cancellation request in time.
        """
        if timeout < 0:
            raise ValueError("timeout must not be negative")

        with self._lock:
            self._admission_open = False
            self._cancel_all_locked()
            current_thread = threading.current_thread()
            threads = [
                thread
                for thread in self._threads.values()
                if thread is not current_thread
            ]

        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))

        with self._lock:
            return not self._threads

    def _cancel_all_locked(self) -> None:
        for job in self._jobs.values():
            if job.status in TERMINAL_STATES:
                continue
            job.cancel_event.set()
            job.stage = "cancelling"
            job.detail = "Cancellation requested; cleaning up owned resources."
            job.updated_at = time.time()

    def _run(self, job: _LifecycleJob, target: JobTarget) -> None:
        def progress(stage: str, detail: str) -> None:
            with self._lock:
                if job.status in TERMINAL_STATES or job.cancel_event.is_set():
                    return
                job.status = "running"
                job.stage = stage
                job.detail = detail
                job.updated_at = time.time()

        result: dict[str, Any] | None = None
        result_status = ""
        failure: Exception | None = None
        try:
            if job.cancel_event.is_set():
                result = {"status": "cancelled"}
            else:
                progress("starting", "Starting the requested runtime.")
                result = target(progress, job.cancel_event)
            if not isinstance(result, dict):
                raise TypeError("Lifecycle job target must return a result dictionary.")
            result_status = str(result.get("status", ""))
        except Exception as exc:
            failure = exc

        with self._lock:
            if failure is not None:
                job.status = "failed"
                job.stage = "failed"
                job.error = str(failure)
                job.detail = str(failure)
                job.updated_at = time.time()
            else:
                job.result = result
                if result_status == "cancelled":
                    job.status = "cancelled"
                    job.stage = "cancelled"
                    job.detail = "The operation was cancelled and cleaned up."
                elif result_status == "error" or result_status not in READY_RESULT_STATES:
                    job.status = "failed"
                    job.stage = "failed"
                    job.error = str(
                        result.get("message")
                        or result.get("error")
                        or f"Runtime did not become ready: {result_status or 'unknown'}"
                    )
                    job.detail = job.error
                else:
                    job.status = "ready"
                    job.stage = "ready"
                    job.detail = (
                        "Runtime committed before the cancellation request could "
                        "take effect."
                        if job.cancel_event.is_set()
                        else "Runtime is ready."
                    )
                job.updated_at = time.time()
            self._threads.pop(job.job_id, None)

    def _prune_locked(self) -> None:
        if len(self._jobs) < self._max_history:
            return
        completed = sorted(
            (job for job in self._jobs.values() if job.status in TERMINAL_STATES),
            key=lambda item: item.updated_at,
        )
        while len(self._jobs) >= self._max_history and completed:
            job = completed.pop(0)
            self._jobs.pop(job.job_id, None)
