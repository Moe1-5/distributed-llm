"""Authoritative, thread-safe runtime state and bounded diagnostic events."""

from __future__ import annotations

import copy
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4


GENERATOR_STATES = {
    "stopped",
    "starting",
    "validating_route",
    "loading",
    "ready",
    "suspended",
    "stopping",
    "failed",
}


class RuntimeStateStore:
    """Keep one coherent runtime snapshot across API and lifecycle threads."""

    def __init__(self, *, event_limit: int = 100) -> None:
        self._lock = threading.RLock()
        self._revision = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=event_limit)
        self._generator: dict[str, Any] = {
            "state": "stopped",
            "model_name": None,
            "components_loaded": False,
            "route_ready": False,
            "reasons": ["Generator not loaded."],
            "node_trace": [],
            "health": None,
            "canary": None,
        }

    def transition_generator(
        self,
        state: str,
        *,
        model_name: Optional[str] = None,
        components_loaded: Optional[bool] = None,
        route_ready: Optional[bool] = None,
        reasons: Optional[list[str]] = None,
        node_trace: Optional[list[str]] = None,
        health: Optional[dict[str, Any]] = None,
        canary: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        if state not in GENERATOR_STATES:
            raise ValueError(f"Unknown generator state: {state}")
        with self._lock:
            previous_state = self._generator["state"]
            candidate = copy.deepcopy(self._generator)
            candidate.update({"state": state, "model_name": model_name})
            if components_loaded is not None:
                candidate["components_loaded"] = components_loaded
            if route_ready is not None:
                candidate["route_ready"] = route_ready
            if reasons is not None:
                candidate["reasons"] = list(reasons)
            if node_trace is not None:
                candidate["node_trace"] = list(node_trace)
            if health is not None or state in {"stopped", "failed"}:
                candidate["health"] = copy.deepcopy(health)
            if canary is not None or state in {"stopped", "failed"}:
                candidate["canary"] = copy.deepcopy(canary)
            comparable_current = {
                key: value for key, value in self._generator.items() if key != "updated_at"
            }
            comparable_candidate = {
                key: value for key, value in candidate.items() if key != "updated_at"
            }
            if comparable_current == comparable_candidate:
                return copy.deepcopy(self._generator)
            candidate["updated_at"] = _utc_now()
            self._generator = candidate
            self._revision += 1
            if previous_state != state:
                self._record_event_locked(
                    kind="generator",
                    phase=state,
                    status="error" if state == "failed" else "info",
                    message=(
                        f"Generator state changed from {previous_state} to {state}."
                    ),
                    details={"model_name": model_name, "reasons": reasons or []},
                )
            return copy.deepcopy(self._generator)

    def record_event(
        self,
        *,
        kind: str,
        phase: str,
        status: str,
        message: str,
        operation_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(
                self._record_event_locked(
                    kind=kind,
                    phase=phase,
                    status=status,
                    message=message,
                    operation_id=operation_id,
                    details=details,
                )
            )

    def _record_event_locked(
        self,
        *,
        kind: str,
        phase: str,
        status: str,
        message: str,
        operation_id: Optional[str] = None,
        details: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        event = {
            "event_id": str(uuid4()),
            "operation_id": operation_id,
            "captured_at": _utc_now(),
            "kind": kind,
            "phase": phase,
            "status": status,
            "message": message,
            "details": copy.deepcopy(details or {}),
        }
        self._events.append(event)
        return event

    def generator_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._generator)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "revision": self._revision,
                "captured_at": _utc_now(),
                "generator": copy.deepcopy(self._generator),
                "events": copy.deepcopy(list(self._events)),
            }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
