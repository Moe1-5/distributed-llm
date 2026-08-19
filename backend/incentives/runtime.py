"""Process-local identity, settlement submission queue, and public status."""

from __future__ import annotations

import json
import math
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from incentives.config import IncentivesConfig, get_incentives_config
from incentives.identity import ApplicationIdentity, load_application_identity
from incentives.protocol import hash_document

UrlOpener = Callable[..., Any]
Sleeper = Callable[[float], None]


class SettlementTransientError(RuntimeError):
    """A connection or server failure that can be retried safely."""


@dataclass(frozen=True)
class _QueuedSubmission:
    payload: dict[str, Any]
    attempt: int = 1


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _env_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be a finite number at least {minimum}")
    return value


class UsefulWorkRuntime:
    def __init__(
        self,
        config: IncentivesConfig | None = None,
        identity: ApplicationIdentity | None = None,
        opener: UrlOpener = urllib.request.urlopen,
        sleeper: Sleeper = time.sleep,
        max_submission_attempts: int | None = None,
        retry_base_seconds: float | None = None,
        retry_max_seconds: float | None = None,
        queue_capacity: int | None = None,
    ) -> None:
        self.config = config or get_incentives_config()
        self.identity = (
            identity
            if identity is not None
            else (load_application_identity() if self.config.enabled else None)
        )
        self._opener = opener
        self._sleeper = sleeper
        self._max_submission_attempts = (
            max_submission_attempts
            if max_submission_attempts is not None
            else _env_int("DISTRIBLLM_SETTLEMENT_MAX_ATTEMPTS", 8, minimum=1)
        )
        if self._max_submission_attempts < 1:
            raise ValueError("max_submission_attempts must be at least 1")
        self._retry_base_seconds = (
            retry_base_seconds
            if retry_base_seconds is not None
            else _env_float(
                "DISTRIBLLM_SETTLEMENT_RETRY_BASE_SECONDS", 1.0, minimum=0.0
            )
        )
        self._retry_max_seconds = (
            retry_max_seconds
            if retry_max_seconds is not None
            else _env_float(
                "DISTRIBLLM_SETTLEMENT_RETRY_MAX_SECONDS", 30.0, minimum=0.0
            )
        )
        if self._retry_max_seconds < self._retry_base_seconds:
            raise ValueError(
                "DISTRIBLLM_SETTLEMENT_RETRY_MAX_SECONDS must be greater than or "
                "equal to DISTRIBLLM_SETTLEMENT_RETRY_BASE_SECONDS"
            )
        if (
            not math.isfinite(self._retry_base_seconds)
            or self._retry_base_seconds < 0
        ):
            raise ValueError("retry_base_seconds must be a finite non-negative number")
        if (
            not math.isfinite(self._retry_max_seconds)
            or self._retry_max_seconds < 0
        ):
            raise ValueError("retry_max_seconds must be a finite non-negative number")
        capacity = (
            queue_capacity
            if queue_capacity is not None
            else _env_int("DISTRIBLLM_SETTLEMENT_QUEUE_CAPACITY", 1024, minimum=1)
        )
        if capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        self._peer_id: str | None = None
        self._queue: queue.Queue[_QueuedSubmission] = queue.Queue(maxsize=capacity)
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._accepted = 0
        self._rejected = 0
        self._retry_attempts = 0
        self._last_error: str | None = None
        self._connectivity = (
            "disabled"
            if not self.config.enabled
            else ("unconfigured" if not self.config.settlement_url else "idle")
        )
        self._account = {
            "verified_credits": 0,
            "ledger_entries": 0,
            "accepted_receipts": 0,
            "useful_positions_served": 0,
        }

    @property
    def enabled(self) -> bool:
        return self.config.enabled and self.identity is not None

    def bind_peer_id(self, peer_id: str) -> None:
        if peer_id.strip():
            with self._lock:
                self._peer_id = peer_id

    def presence(self) -> dict[str, Any]:
        if self.identity is None or self._peer_id is None:
            raise RuntimeError("Useful-work identity is not bound to a p2p peer")
        return self.identity.presence(self._peer_id)

    def submit(self, submission: dict[str, Any]) -> bool:
        if not self.enabled or not self.config.settlement_url:
            with self._lock:
                self._connectivity = (
                    "disabled" if not self.enabled else "unconfigured"
                )
            return False
        with self._lock:
            try:
                self._queue.put_nowait(_QueuedSubmission(dict(submission)))
            except queue.Full:
                self._rejected += 1
                self._connectivity = "error"
                self._last_error = (
                    "Settlement submission queue is full; this receipt was not queued. "
                    "Restore settlement connectivity before generating more work."
                )
                return False
            self._ensure_worker_locked()
        return True

    def _ensure_worker_locked(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(
            target=self._submission_loop,
            daemon=True,
            name="useful-work-settlement",
        )
        self._worker.start()

    def _submission_loop(self) -> None:
        while True:
            try:
                queued = self._queue.get_nowait()
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._worker = None
                        return
                continue
            try:
                self._post_json("/v1/receipts", queued.payload)
                with self._lock:
                    self._accepted += 1
                    self._connectivity = "connected"
                    self._last_error = None
                self.refresh_account()
            except SettlementTransientError as exc:
                if queued.attempt < self._max_submission_attempts:
                    delay = min(
                        self._retry_base_seconds * (2 ** (queued.attempt - 1)),
                        self._retry_max_seconds,
                    )
                    with self._lock:
                        self._retry_attempts += 1
                        self._connectivity = "retrying"
                        self._last_error = self._retry_message(
                            exc,
                            attempt=queued.attempt + 1,
                            delay=delay,
                        )
                    self._sleeper(delay)
                    try:
                        self._queue.put_nowait(
                            _QueuedSubmission(queued.payload, queued.attempt + 1)
                        )
                    except queue.Full:
                        with self._lock:
                            self._rejected += 1
                            self._connectivity = "error"
                            self._last_error = (
                                "Settlement retry queue became full; the receipt was "
                                "not retained."
                            )
                else:
                    with self._lock:
                        self._rejected += 1
                        self._connectivity = "error"
                        self._last_error = (
                            f"{exc} Submission abandoned after "
                            f"{self._max_submission_attempts} attempts."
                        )
            except Exception as exc:
                with self._lock:
                    self._rejected += 1
                    self._connectivity = "error"
                    self._last_error = str(exc)
            finally:
                self._queue.task_done()

    def _retry_message(
        self,
        exc: BaseException,
        *,
        attempt: int,
        delay: float,
    ) -> str:
        guidance = self._connectivity_guidance()
        return (
            f"{exc} Retrying submission {attempt}/{self._max_submission_attempts} "
            f"in {delay:.1f} seconds. {guidance}"
        )

    def _connectivity_guidance(self) -> str:
        guidance = "Verify the configured settlement HTTPS endpoint."
        if self.config.settlement_url.startswith(
            ("http://127.0.0.1", "http://localhost")
        ):
            guidance = (
                "Verify that the participant WSL SSH tunnel to the VPS settlement "
                "service is running."
            )
        return guidance

    def _request_json(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.settlement_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if payload is not None and path == "/v1/receipts":
            headers["Idempotency-Key"] = hash_document(payload)
        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            with self._opener(request, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            message = f"Settlement returned HTTP {exc.code}: {detail}"
            if exc.code in {408, 425, 429} or exc.code >= 500:
                raise SettlementTransientError(message) from exc
            raise RuntimeError(message) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SettlementTransientError(f"Settlement is unreachable: {exc}") from exc
        if not isinstance(result, dict):
            raise RuntimeError("Settlement returned a non-object response")
        return result

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request_json(path, payload=payload)

    def refresh_account(self) -> bool:
        if not self.enabled or not self.config.settlement_url or self.identity is None:
            return False
        try:
            account = self._request_json(f"/v1/accounts/{self.identity.public_key}")
            with self._lock:
                self._account = {
                    field: int(account.get(field, 0))
                    for field in self._account
                }
                self._connectivity = "connected"
                self._last_error = None
            return True
        except Exception as exc:
            with self._lock:
                self._connectivity = (
                    "retrying" if self._queue.unfinished_tasks > 0 else "error"
                )
                self._last_error = f"{exc} {self._connectivity_guidance()}"
            return False

    def snapshot(self, *, display_peer_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            return {
                "mode": self.config.mode,
                "protocol_version": 1,
                "application_public_key": (
                    self.identity.public_key if self.identity is not None else None
                ),
                "p2p_peer_id": self._peer_id or display_peer_id,
                "settlement_url_configured": bool(self.config.settlement_url),
                "settlement_connectivity": self._connectivity,
                "pending_submissions": self._queue.unfinished_tasks,
                "accepted_submissions": self._accepted,
                "rejected_submissions": self._rejected,
                "submission_retry_attempts": self._retry_attempts,
                "submission_max_attempts": self._max_submission_attempts,
                "last_error": self._last_error,
                **self._account,
            }


_runtime: UsefulWorkRuntime | None = None
_runtime_lock = threading.Lock()


def get_useful_work_runtime() -> UsefulWorkRuntime:
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = UsefulWorkRuntime()
        return _runtime
