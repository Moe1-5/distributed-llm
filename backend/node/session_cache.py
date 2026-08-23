"""Bounded provider-owned state for versioned prefill/decode sessions."""

from __future__ import annotations

import math
import multiprocessing as mp
import os
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypeVar

import torch
from transformers import DynamicCache

T = TypeVar("T")


class SessionCacheError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"session_cache:{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class SessionCacheConfig:
    max_sessions: int = 8
    max_total_bytes: int = 2 * 1024**3
    max_session_bytes: int = 512 * 1024**2
    max_positions: int = 2048
    ttl_seconds: float = 300.0
    operation_history_limit: int = 128
    # Zero derives a safe per-session value.  This keeps small test and
    # development budgets valid while production defaults to sixteen MiB.
    max_replay_result_bytes: int = 0

    def __post_init__(self) -> None:
        if not 1 <= self.max_sessions <= 1024:
            raise ValueError("session max_sessions must be between 1 and 1024")
        if not 1024 <= self.max_session_bytes <= self.max_total_bytes:
            raise ValueError(
                "session max_session_bytes must be at least 1024 and no larger than max_total_bytes"
            )
        if self.max_total_bytes > 64 * 1024**3:
            raise ValueError("session max_total_bytes must not exceed 64 GiB")
        if not 1 <= self.max_positions <= 131_072:
            raise ValueError("session max_positions must be between 1 and 131072")
        if not math.isfinite(self.ttl_seconds) or not 5 <= self.ttl_seconds <= 86400:
            raise ValueError("session ttl_seconds must be between 5 and 86400")
        if not 8 <= self.operation_history_limit <= 4096:
            raise ValueError("session operation_history_limit must be between 8 and 4096")
        if self.max_replay_result_bytes == 0:
            object.__setattr__(
                self,
                "max_replay_result_bytes",
                min(16 * 1024**2, self.max_session_bytes),
            )
        if not 1024 <= self.max_replay_result_bytes <= self.max_session_bytes:
            raise ValueError(
                "session max_replay_result_bytes must be at least 1024 and no larger than "
                "max_session_bytes"
            )

    def public_dict(self) -> dict[str, int | float]:
        return {
            "max_sessions": self.max_sessions,
            "max_total_bytes": self.max_total_bytes,
            "max_session_bytes": self.max_session_bytes,
            "max_positions": self.max_positions,
            "ttl_seconds": self.ttl_seconds,
            "operation_history_limit": self.operation_history_limit,
            "max_replay_result_bytes": self.max_replay_result_bytes,
        }


@dataclass
class _SessionState:
    session_id: str
    route_id: str
    request_id: str
    cache: DynamicCache
    created_at: float
    last_access_at: float
    expected_position: int = 0
    estimated_bytes: int = 0
    active: bool = False
    operation_ids: deque[str] = field(default_factory=deque)
    operation_id_set: set[str] = field(default_factory=set)
    replay_results: OrderedDict[str, "_RetainedOperationResult"] = field(
        default_factory=OrderedDict
    )
    replay_result_bytes: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True)
class _RetainedOperationResult:
    """A bounded CPU response that makes an ambiguous session RPC idempotent."""

    operation: str
    position_start: int
    token_count: int
    input_bytes: int
    input_fingerprint: str
    output: torch.Tensor
    state: dict[str, Any]
    output_bytes: int


class SessionCacheManager:
    """Owns exact-route transformer caches with bounded memory and lifecycle."""

    def __init__(
        self,
        *,
        layer_count: int,
        hidden_size: int,
        element_size: int,
        model_config: Any,
        config: Optional[SessionCacheConfig] = None,
        clock: Callable[[], float] = time.monotonic,
        enable_background_cleanup: bool = True,
    ) -> None:
        if layer_count <= 0 or hidden_size <= 0 or element_size <= 0:
            raise ValueError("session cache dimensions must be positive")
        self.layer_count = int(layer_count)
        self.hidden_size = int(hidden_size)
        self.element_size = int(element_size)
        self.model_config = model_config
        self.config = config or get_session_cache_config()
        self.clock = clock
        self.enable_background_cleanup = enable_background_cleanup
        self._lock = threading.RLock()
        self._sessions: dict[str, _SessionState] = {}
        self._cleanup_stop = threading.Event()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._shared_metrics = {
            name: mp.Value("Q", 0)
            for name in (
                "active_sessions",
                "estimated_cache_bytes",
                "opened_sessions",
                "closed_sessions",
                "cancelled_sessions",
                "expired_sessions",
                "evicted_sessions",
                "admission_rejections",
                "replay_rejections",
                "replay_results_served",
                "replay_result_rejections",
                "replay_result_evictions",
                "retained_replay_bytes",
                "retained_replay_results",
                "prefill_operations",
                "decode_operations",
                "prefill_input_bytes",
                "decode_input_bytes",
            )
        }

    def open(
        self,
        *,
        session_id: str,
        route_id: str,
        request_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        self._ensure_cleanup_thread()
        self._validate_identity(session_id, "session_id")
        self._validate_identity(route_id, "route_id")
        self._validate_identity(request_id, "request_id")
        self._validate_identity(operation_id, "operation_id")
        now = self.clock()
        with self._lock:
            self._expire_locked(now)
            existing = self._sessions.get(session_id)
            if existing is not None:
                if existing.route_id != route_id or existing.request_id != request_id:
                    raise SessionCacheError(
                        "identity_conflict",
                        "session ID is already owned by another route or request",
                    )
                self._remember_operation_locked(existing, operation_id, allow_replay=True)
                existing.last_access_at = now
                return self._state_public(existing, idempotent_replay=True)
            self._evict_for_session_slot_locked(now)
            state = _SessionState(
                session_id=session_id,
                route_id=route_id,
                request_id=request_id,
                cache=DynamicCache(config=self.model_config),
                created_at=now,
                last_access_at=now,
            )
            self._remember_operation_locked(state, operation_id, allow_replay=True)
            self._sessions[session_id] = state
            self._increment_metric("opened_sessions")
            self._refresh_shared_usage_locked()
            return self._state_public(state, idempotent_replay=False)

    def execute(
        self,
        *,
        session_id: str,
        route_id: str,
        request_id: str,
        operation_id: str,
        operation: str,
        position_start: int,
        token_count: int,
        input_bytes: int,
        input_fingerprint: str,
        target: Callable[[DynamicCache], T],
    ) -> tuple[T, dict[str, Any]]:
        if operation not in {"prefill", "decode"}:
            raise ValueError("session execution operation must be prefill or decode")
        if token_count <= 0 or input_bytes < 0:
            raise SessionCacheError("invalid_work", "token_count and input_bytes are invalid")
        self._validate_fingerprint(input_fingerprint)
        now = self.clock()
        with self._lock:
            self._expire_locked(now)
            state = self._require_owned_locked(session_id, route_id, request_id)
        with state.lock:
            with self._lock:
                current = self._require_owned_locked(session_id, route_id, request_id)
                if current is not state:
                    raise SessionCacheError("session_replaced", "session ownership changed")
                retained = state.replay_results.get(operation_id)
                if retained is not None:
                    self._validate_retained_operation(
                        retained,
                        operation=operation,
                        position_start=position_start,
                        token_count=token_count,
                        input_bytes=input_bytes,
                        input_fingerprint=input_fingerprint,
                    )
                    state.replay_results.move_to_end(operation_id)
                    state.last_access_at = now
                    self._increment_metric("replay_results_served")
                    replay_state = {
                        **retained.state,
                        "idempotent_replay": True,
                        "operation_replayed": True,
                    }
                    return retained.output.clone(), replay_state
                self._remember_operation_locked(state, operation_id, allow_replay=False)
                if state.active:
                    raise SessionCacheError("session_busy", "another operation owns the session")
                if position_start != state.expected_position:
                    raise SessionCacheError(
                        "position_mismatch",
                        f"expected position {state.expected_position}, got {position_start}",
                    )
                if operation == "prefill" and state.expected_position != 0:
                    raise SessionCacheError("prefill_already_applied", "prefill is already complete")
                if operation == "decode" and state.expected_position == 0:
                    raise SessionCacheError("prefill_required", "decode requires a completed prefill")
                new_position = position_start + token_count
                if new_position > self.config.max_positions:
                    raise SessionCacheError(
                        "position_limit",
                        f"session position {new_position} exceeds {self.config.max_positions}",
                    )
                estimated_bytes = self._estimate_bytes(new_position)
                self._admit_growth_locked(state, estimated_bytes, now)
                state.active = True
                state.last_access_at = now
            try:
                result = target(state.cache)
            except Exception:
                with self._lock:
                    state.active = False
                    state.last_access_at = self.clock()
                raise
            with self._lock:
                state.active = False
                state.expected_position = new_position
                state.estimated_bytes = estimated_bytes
                state.last_access_at = self.clock()
                response_state = self._state_public(state, idempotent_replay=False)
                self._retain_operation_result_locked(
                    state,
                    operation_id=operation_id,
                    operation=operation,
                    position_start=position_start,
                    token_count=token_count,
                    input_bytes=input_bytes,
                    input_fingerprint=input_fingerprint,
                    result=result,
                    response_state=response_state,
                )
                if operation == "prefill":
                    self._increment_metric("prefill_operations")
                    self._increment_metric("prefill_input_bytes", input_bytes)
                else:
                    self._increment_metric("decode_operations")
                    self._increment_metric("decode_input_bytes", input_bytes)
                self._refresh_shared_usage_locked()
                return result, response_state

    def close(
        self,
        *,
        session_id: str,
        route_id: str,
        request_id: str,
        operation_id: str,
        cancelled: bool = False,
    ) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            self._expire_locked(now)
            state = self._sessions.get(session_id)
            if state is None:
                return {
                    "session_id": session_id,
                    "state": "CLOSED",
                    "idempotent_replay": True,
                }
            self._require_owned_locked(session_id, route_id, request_id)
            if state.active:
                raise SessionCacheError("session_busy", "cannot close an active operation")
            self._remember_operation_locked(state, operation_id, allow_replay=True)
            self._sessions.pop(session_id, None)
            if cancelled:
                self._increment_metric("cancelled_sessions")
            else:
                self._increment_metric("closed_sessions")
            self._refresh_shared_usage_locked()
            return {
                **self._state_public(state, idempotent_replay=False),
                "state": "CANCELLED" if cancelled else "CLOSED",
            }

    def close_all(self, reason: str = "worker_shutdown") -> int:
        if not reason.strip():
            raise ValueError("close_all reason must not be blank")
        with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            self._increment_metric("closed_sessions", count)
            self._refresh_shared_usage_locked()
        self._stop_cleanup_thread()
        return count

    def snapshot(self) -> dict[str, Any]:
        now = self.clock()
        with self._lock:
            self._expire_locked(now)
            sessions = [self._state_public(state) for state in self._sessions.values()]
            return {
                "protocol_version": 1,
                "policy": self.config.public_dict(),
                **self._shared_metric_snapshot(),
                "sessions": sessions,
            }

    def _estimate_bytes(self, positions: int) -> int:
        return (
            2
            * self.layer_count
            * positions
            * self.hidden_size
            * self.element_size
        )

    def _admit_growth_locked(
        self,
        state: _SessionState,
        estimated_bytes: int,
        now: float,
    ) -> None:
        if estimated_bytes + state.replay_result_bytes > self.config.max_session_bytes:
            self._increment_metric("admission_rejections")
            raise SessionCacheError(
                "session_memory_limit",
                "session key/value cache exceeds the per-session memory limit",
            )
        while self._total_bytes_locked(exclude=state.session_id) + estimated_bytes > self.config.max_total_bytes:
            candidate = self._oldest_idle_locked(exclude=state.session_id)
            if candidate is None:
                self._increment_metric("admission_rejections")
                raise SessionCacheError(
                    "total_memory_limit",
                    "provider key/value cache memory is fully admitted",
                )
            self._sessions.pop(candidate.session_id, None)
            self._increment_metric("evicted_sessions")
            self._refresh_shared_usage_locked()
        state.last_access_at = now

    def _evict_for_session_slot_locked(self, now: float) -> None:
        if len(self._sessions) < self.config.max_sessions:
            return
        candidate = self._oldest_idle_locked()
        if candidate is None:
            self._increment_metric("admission_rejections")
            raise SessionCacheError("session_limit", "all provider session slots are active")
        self._sessions.pop(candidate.session_id, None)
        self._increment_metric("evicted_sessions")
        self._refresh_shared_usage_locked()

    def _oldest_idle_locked(self, *, exclude: Optional[str] = None) -> Optional[_SessionState]:
        candidates = [
            state
            for state in self._sessions.values()
            if not state.active and state.session_id != exclude
        ]
        return min(candidates, key=lambda state: state.last_access_at, default=None)

    def _total_bytes_locked(self, *, exclude: Optional[str] = None) -> int:
        return sum(
            state.estimated_bytes + state.replay_result_bytes
            for state in self._sessions.values()
            if state.session_id != exclude
        )

    def _expire_locked(self, now: float) -> None:
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if not state.active and now - state.last_access_at >= self.config.ttl_seconds
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
            self._increment_metric("expired_sessions")
        if expired:
            self._refresh_shared_usage_locked()

    def _require_owned_locked(
        self,
        session_id: str,
        route_id: str,
        request_id: str,
    ) -> _SessionState:
        state = self._sessions.get(session_id)
        if state is None:
            raise SessionCacheError("session_not_found", "session is not open")
        if state.route_id != route_id or state.request_id != request_id:
            raise SessionCacheError(
                "identity_conflict",
                "session route or request identity does not match its owner",
            )
        return state

    def _remember_operation_locked(
        self,
        state: _SessionState,
        operation_id: str,
        *,
        allow_replay: bool,
    ) -> None:
        self._validate_identity(operation_id, "operation_id")
        if operation_id in state.operation_id_set:
            if allow_replay:
                return
            self._increment_metric("replay_rejections")
            raise SessionCacheError(
                "operation_replayed",
                "tensor session operation was already applied and will not be replayed",
            )
        state.operation_ids.append(operation_id)
        state.operation_id_set.add(operation_id)
        while len(state.operation_ids) > self.config.operation_history_limit:
            removed = state.operation_ids.popleft()
            state.operation_id_set.discard(removed)
            removed_result = state.replay_results.pop(removed, None)
            if removed_result is not None:
                state.replay_result_bytes -= removed_result.output_bytes

    def _retain_operation_result_locked(
        self,
        state: _SessionState,
        *,
        operation_id: str,
        operation: str,
        position_start: int,
        token_count: int,
        input_bytes: int,
        input_fingerprint: str,
        result: T,
        response_state: dict[str, Any],
    ) -> None:
        """Retain only a small completed tensor result; never retain activation inputs."""
        if not isinstance(result, torch.Tensor):
            return
        output_bytes = result.numel() * result.element_size()
        if output_bytes > self.config.max_replay_result_bytes:
            self._increment_metric("replay_result_rejections")
            return
        if state.estimated_bytes + output_bytes > self.config.max_session_bytes:
            self._increment_metric("replay_result_rejections")
            return
        while state.replay_result_bytes + output_bytes > self.config.max_replay_result_bytes:
            _removed_id, removed = state.replay_results.popitem(last=False)
            state.replay_result_bytes -= removed.output_bytes
            self._increment_metric("replay_result_evictions")
        if self._total_bytes_locked() + output_bytes > self.config.max_total_bytes:
            self._increment_metric("replay_result_rejections")
            return
        # Check the source tensor before copying it to CPU: an oversized
        # response must not create a transient unbounded duplicate.
        retained_output = result.detach().to(device="cpu").contiguous().clone()
        state.replay_results[operation_id] = _RetainedOperationResult(
            operation=operation,
            position_start=position_start,
            token_count=token_count,
            input_bytes=input_bytes,
            input_fingerprint=input_fingerprint,
            output=retained_output,
            state=dict(response_state),
            output_bytes=output_bytes,
        )
        state.replay_result_bytes += output_bytes

    @staticmethod
    def _validate_retained_operation(
        retained: _RetainedOperationResult,
        *,
        operation: str,
        position_start: int,
        token_count: int,
        input_bytes: int,
        input_fingerprint: str,
    ) -> None:
        if (
            retained.operation != operation
            or retained.position_start != position_start
            or retained.token_count != token_count
            or retained.input_bytes != input_bytes
            or retained.input_fingerprint != input_fingerprint
        ):
            raise SessionCacheError(
                "operation_identity_conflict",
                "operation ID was reused with different session work",
            )

    @staticmethod
    def _validate_fingerprint(value: str) -> None:
        if not isinstance(value, str) or len(value) != 64:
            raise SessionCacheError(
                "invalid_fingerprint",
                "input_fingerprint must be a SHA-256 hexadecimal digest",
            )
        try:
            int(value, 16)
        except ValueError as exc:
            raise SessionCacheError(
                "invalid_fingerprint",
                "input_fingerprint must be a SHA-256 hexadecimal digest",
            ) from exc

    def _increment_metric(self, name: str, amount: int = 1) -> None:
        metric = self._shared_metrics[name]
        with metric.get_lock():
            metric.value += max(0, int(amount))

    def _set_metric(self, name: str, value: int) -> None:
        metric = self._shared_metrics[name]
        with metric.get_lock():
            metric.value = max(0, int(value))

    def _refresh_shared_usage_locked(self) -> None:
        self._set_metric("active_sessions", len(self._sessions))
        self._set_metric(
            "estimated_cache_bytes",
            sum(state.estimated_bytes for state in self._sessions.values()),
        )
        self._set_metric(
            "retained_replay_bytes",
            sum(state.replay_result_bytes for state in self._sessions.values()),
        )
        self._set_metric(
            "retained_replay_results",
            sum(len(state.replay_results) for state in self._sessions.values()),
        )

    def _shared_metric_snapshot(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for name, metric in self._shared_metrics.items():
            with metric.get_lock():
                result[name] = int(metric.value)
        return result

    def _ensure_cleanup_thread(self) -> None:
        if not self.enable_background_cleanup:
            return
        with self._lock:
            thread = self._cleanup_thread
            if thread is not None and thread.is_alive():
                return
            self._cleanup_stop = threading.Event()
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_loop,
                daemon=True,
                name="session-cache-expiry",
            )
            self._cleanup_thread.start()

    def _cleanup_loop(self) -> None:
        interval = min(max(self.config.ttl_seconds / 4, 1.0), 10.0)
        while not self._cleanup_stop.wait(interval):
            with self._lock:
                self._expire_locked(self.clock())

    def _stop_cleanup_thread(self) -> None:
        self._cleanup_stop.set()
        thread = self._cleanup_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._cleanup_thread = None

    @staticmethod
    def _validate_identity(value: str, field: str) -> None:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise SessionCacheError(
                "invalid_identity",
                f"{field} must be a non-empty string no longer than 128 characters",
            )

    @staticmethod
    def _state_public(
        state: _SessionState,
        *,
        idempotent_replay: bool = False,
    ) -> dict[str, Any]:
        return {
            "session_id": state.session_id,
            "route_id": state.route_id,
            "request_id": state.request_id,
            "state": "OPEN",
            "expected_position": state.expected_position,
            "estimated_bytes": state.estimated_bytes,
            "created_at_monotonic": state.created_at,
            "last_access_at_monotonic": state.last_access_at,
            "active": state.active,
            "idempotent_replay": idempotent_replay,
            "retained_replay_bytes": state.replay_result_bytes,
            "retained_replay_results": len(state.replay_results),
        }


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def get_session_cache_config() -> SessionCacheConfig:
    return SessionCacheConfig(
        max_sessions=_env_int("DISTRIBLLM_SESSION_MAX_SESSIONS", 8),
        max_total_bytes=_env_int("DISTRIBLLM_SESSION_MAX_TOTAL_BYTES", 2 * 1024**3),
        max_session_bytes=_env_int(
            "DISTRIBLLM_SESSION_MAX_SESSION_BYTES",
            512 * 1024**2,
        ),
        max_positions=_env_int("DISTRIBLLM_SESSION_MAX_POSITIONS", 2048),
        ttl_seconds=_env_float("DISTRIBLLM_SESSION_TTL_SECONDS", 300.0),
        operation_history_limit=_env_int(
            "DISTRIBLLM_SESSION_OPERATION_HISTORY",
            128,
        ),
        max_replay_result_bytes=_env_int(
            "DISTRIBLLM_SESSION_MAX_REPLAY_RESULT_BYTES",
            0,
        ),
    )
