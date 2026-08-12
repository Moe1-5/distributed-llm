"""Process-local identity, settlement submission queue, and public status."""

from __future__ import annotations

import json
import queue
import threading
import urllib.error
import urllib.request
from typing import Any, Callable

from incentives.config import IncentivesConfig, get_incentives_config
from incentives.identity import ApplicationIdentity, load_application_identity

UrlOpener = Callable[..., Any]


class UsefulWorkRuntime:
    def __init__(
        self,
        config: IncentivesConfig | None = None,
        identity: ApplicationIdentity | None = None,
        opener: UrlOpener = urllib.request.urlopen,
    ) -> None:
        self.config = config or get_incentives_config()
        self.identity = (
            identity
            if identity is not None
            else (load_application_identity() if self.config.enabled else None)
        )
        self._opener = opener
        self._peer_id: str | None = None
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._accepted = 0
        self._rejected = 0
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
            self._queue.put(submission)
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
                submission = self._queue.get_nowait()
            except queue.Empty:
                with self._lock:
                    if self._queue.empty():
                        self._worker = None
                        return
                continue
            try:
                self._post_json("/v1/receipts", submission)
                with self._lock:
                    self._accepted += 1
                    self._connectivity = "connected"
                    self._last_error = None
                self.refresh_account()
            except Exception as exc:
                with self._lock:
                    self._rejected += 1
                    self._connectivity = "error"
                    self._last_error = str(exc)
            finally:
                self._queue.task_done()

    def _request_json(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.config.settlement_url}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        try:
            with self._opener(request, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Settlement returned HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Settlement is unreachable: {exc}") from exc
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
                self._connectivity = "error"
                self._last_error = str(exc)
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
