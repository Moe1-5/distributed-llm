"""Local incentive mode, asynchronous settlement submission, and status."""

from __future__ import annotations

import json
import os
import queue
import threading
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from incentives.identity import AppIdentity, get_or_create_identity


VALID_MODES = {"off", "shadow", "credit"}


def get_incentive_mode() -> str:
    mode = os.environ.get("DISTRIBLLM_INCENTIVE_MODE", "off").strip().lower()
    if mode not in VALID_MODES:
        raise ValueError("DISTRIBLLM_INCENTIVE_MODE must be off, shadow, or credit")
    return mode


def get_settlement_url() -> str:
    return os.environ.get("DISTRIBLLM_SETTLEMENT_URL", "").strip().rstrip("/")


@dataclass
class SubmissionStatus:
    pending: int = 0
    accepted: int = 0
    rejected: int = 0
    last_error: Optional[str] = None
    settlement_connected: bool = False
    recent: deque = field(default_factory=lambda: deque(maxlen=50))


class ReceiptSubmitter:
    def __init__(self, *, mode: str, settlement_url: str):
        self.mode = mode
        self.settlement_url = settlement_url
        self._queue: queue.Queue[dict] = queue.Queue()
        self._lock = threading.Lock()
        self._status = SubmissionStatus()
        self._thread: Optional[threading.Thread] = None

    def submit(self, pair: dict) -> None:
        if self.mode == "off":
            return
        with self._lock:
            self._status.pending += 1
            self._status.recent.appendleft(
                {"request_id": pair["worker_receipt"]["payload"]["request_id"], "status": "pending"}
            )
        self._queue.put(pair)
        self._ensure_thread()

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="receipt-submitter")
        self._thread.start()

    def _run(self) -> None:
        while True:
            pair = self._queue.get()
            request_id = pair["worker_receipt"]["payload"]["request_id"]
            try:
                result = self._post(pair)
                status = "accepted" if result.get("accepted") else "rejected"
                with self._lock:
                    self._status.pending -= 1
                    if status == "accepted":
                        self._status.accepted += 1
                    else:
                        self._status.rejected += 1
                    self._status.last_error = None if status == "accepted" else str(result.get("reason"))
                    self._status.settlement_connected = True
                    self._status.recent.appendleft({"request_id": request_id, "status": status})
            except Exception as exc:
                with self._lock:
                    self._status.pending -= 1
                    self._status.rejected += 1
                    self._status.last_error = str(exc)
                    self._status.settlement_connected = False
                    self._status.recent.appendleft(
                        {"request_id": request_id, "status": "rejected", "error": str(exc)}
                    )
            finally:
                self._queue.task_done()

    def _post(self, pair: dict) -> dict:
        if not self.settlement_url:
            raise RuntimeError("DISTRIBLLM_SETTLEMENT_URL is not configured")
        request = urllib.request.Request(
            f"{self.settlement_url}/v1/receipts",
            data=json.dumps(pair).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Settlement rejected receipt: HTTP {exc.code}: {detail}") from exc

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "pending": self._status.pending,
                "accepted": self._status.accepted,
                "rejected": self._status.rejected,
                "last_error": self._status.last_error,
                "settlement_connected": self._status.settlement_connected,
                "recent": list(self._status.recent),
            }


_runtime_lock = threading.Lock()
_identity: Optional[AppIdentity] = None
_submitter: Optional[ReceiptSubmitter] = None


def get_runtime_identity() -> AppIdentity:
    global _identity
    with _runtime_lock:
        if _identity is None:
            _identity = get_or_create_identity()
        return _identity


def get_receipt_submitter() -> ReceiptSubmitter:
    global _submitter
    mode = get_incentive_mode()
    url = get_settlement_url()
    with _runtime_lock:
        if _submitter is None or _submitter.mode != mode or _submitter.settlement_url != url:
            _submitter = ReceiptSubmitter(mode=mode, settlement_url=url)
        return _submitter


def fetch_settlement_account(public_key: str) -> dict:
    url = get_settlement_url()
    if not url:
        return {
            "public_key": public_key,
            "verified_credits": 0,
            "entry_count": 0,
            "available": False,
            "error": "DISTRIBLLM_SETTLEMENT_URL is not configured",
        }
    try:
        with urllib.request.urlopen(f"{url}/v1/accounts/{public_key}", timeout=3) as response:
            result = json.loads(response.read().decode("utf-8"))
            return {**result, "available": True, "error": None}
    except Exception as exc:
        return {
            "public_key": public_key,
            "verified_credits": 0,
            "entry_count": 0,
            "available": False,
            "error": str(exc),
        }


def reset_runtime_for_tests() -> None:
    global _identity, _submitter
    with _runtime_lock:
        _identity = None
        _submitter = None
