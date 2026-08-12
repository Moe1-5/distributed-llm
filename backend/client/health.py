"""Continuous provider health state and lifecycle-owned metadata probing."""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True, order=True)
class ProviderKey:
    peer_id: str
    rpc_uid: str
    model_name: str
    model_revision: str
    layer_start: int
    layer_end: int

    @classmethod
    def from_node(cls, node: dict) -> ProviderKey:
        return cls(
            peer_id=str(node.get("peer_id", "")),
            rpc_uid=str(node.get("rpc_uid", "")),
            model_name=str(node.get("model_name", "")),
            model_revision=str(node.get("model_revision", "unversioned")),
            layer_start=int(node.get("layer_start", 0)),
            layer_end=int(node.get("layer_end", 0)),
        )


@dataclass(frozen=True)
class ProviderHealthConfig:
    selected_interval_seconds: float = 5.0
    standby_interval_seconds: float = 15.0
    probe_timeout_seconds: float = 3.0
    failure_threshold: int = 2
    offline_threshold: int = 4
    recovery_successes: int = 2
    dht_stale_seconds: float = 10.0
    max_concurrency: int = 4
    jitter_ratio: float = 0.15
    scheduler_interval_seconds: float = 0.25
    discovery_interval_seconds: float = 2.0

    def __post_init__(self) -> None:
        if not 0.1 <= self.selected_interval_seconds <= 3600:
            raise ValueError("Selected health interval must be between 0.1 and 3600 seconds")
        if not self.selected_interval_seconds <= self.standby_interval_seconds <= 7200:
            raise ValueError("Standby health interval must be at least the selected interval")
        if not 0.1 <= self.probe_timeout_seconds <= 300:
            raise ValueError("Health probe timeout must be between 0.1 and 300 seconds")
        if not 1 <= self.failure_threshold <= 20:
            raise ValueError("Health failure threshold must be between 1 and 20")
        if not self.failure_threshold <= self.offline_threshold <= 50:
            raise ValueError("Offline threshold must be at least the failure threshold")
        if not 1 <= self.recovery_successes <= 20:
            raise ValueError("Health recovery successes must be between 1 and 20")
        if not self.probe_timeout_seconds <= self.dht_stale_seconds <= 7200:
            raise ValueError("DHT stale time must be at least the probe timeout")
        if not 1 <= self.max_concurrency <= 32:
            raise ValueError("Health probe concurrency must be between 1 and 32")
        if not 0 <= self.jitter_ratio <= 0.5:
            raise ValueError("Health probe jitter must be between 0 and 0.5")
        if not 0.05 <= self.scheduler_interval_seconds <= 10:
            raise ValueError("Health scheduler interval must be between 0.05 and 10 seconds")
        if not 0.1 <= self.discovery_interval_seconds <= 300:
            raise ValueError("Health discovery interval must be between 0.1 and 300 seconds")

    @property
    def detection_window_seconds(self) -> float:
        return (
            self.selected_interval_seconds * self.failure_threshold
            + self.probe_timeout_seconds
        )


@dataclass
class ProviderHealthRecord:
    key: ProviderKey
    state: str = "checking"
    role: str = "standby"
    dht_present: bool = True
    protocol_compatible: bool = True
    transport_verified: bool | None = None
    consecutive_successes: int = 0
    consecutive_failures: int = 0
    last_probe_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    latency_ms: float | None = None
    reason: str | None = "Awaiting first RPC health probe."
    next_probe_at: float = 0.0
    dht_last_seen_at: float = 0.0
    advertisement_timestamp: float | None = None
    recovery_required: bool = False

    def to_dict(self) -> dict:
        return {
            "peer_id": self.key.peer_id,
            "rpc_uid": self.key.rpc_uid,
            "model_name": self.key.model_name,
            "model_revision": self.key.model_revision,
            "layer_start": self.key.layer_start,
            "layer_end": self.key.layer_end,
            "state": self.state,
            "role": self.role,
            "dht_present": self.dht_present,
            "protocol_compatible": self.protocol_compatible,
            "transport_verified": self.transport_verified,
            "consecutive_successes": self.consecutive_successes,
            "consecutive_failures": self.consecutive_failures,
            "last_probe_at": self.last_probe_at,
            "last_success_at": self.last_success_at,
            "last_failure_at": self.last_failure_at,
            "latency_ms": self.latency_ms,
            "reason": self.reason,
            "next_probe_at": self.next_probe_at,
            "dht_last_seen_at": self.dht_last_seen_at,
            "advertisement_timestamp": self.advertisement_timestamp,
        }


class ProviderHealthRegistry:
    def __init__(self, config: ProviderHealthConfig) -> None:
        self.config = config
        self._records: dict[ProviderKey, ProviderHealthRecord] = {}
        self._lock = threading.RLock()

    def observe(
        self,
        nodes: Iterable[dict],
        selected_keys: set[ProviderKey],
        *,
        now: float,
    ) -> None:
        observed: set[ProviderKey] = set()
        with self._lock:
            for node in nodes:
                key = ProviderKey.from_node(node)
                observed.add(key)
                role = "selected" if key in selected_keys else "standby"
                record = self._records.get(key)
                if record is None:
                    record = ProviderHealthRecord(
                        key=key,
                        role=role,
                        next_probe_at=now,
                        dht_last_seen_at=now,
                    )
                    self._records[key] = record
                else:
                    if record.role != role and role == "selected":
                        record.next_probe_at = min(record.next_probe_at, now)
                    record.role = role
                    record.dht_present = True
                    record.protocol_compatible = True
                    record.dht_last_seen_at = now
                record.transport_verified = node.get("transport_verified")
                timestamp = node.get("timestamp")
                record.advertisement_timestamp = (
                    float(timestamp) if isinstance(timestamp, (int, float)) else None
                )

            for key, record in self._records.items():
                if key in observed or not record.protocol_compatible:
                    continue
                record.dht_present = False
                if now - record.dht_last_seen_at >= self.config.dht_stale_seconds:
                    self._set_unhealthy(
                        record,
                        state="offline",
                        reason="DHT advertisement expired or disappeared.",
                        now=now,
                    )

    def record_protocol_failure(
        self,
        *,
        peer_id: str,
        model_name: str,
        reason: str,
        now: float,
    ) -> None:
        key = ProviderKey(peer_id, "invalid", model_name, "unknown", 0, 0)
        with self._lock:
            record = self._records.get(key) or ProviderHealthRecord(key=key)
            self._records[key] = record
            record.protocol_compatible = False
            record.dht_present = True
            record.dht_last_seen_at = now
            self._set_unhealthy(
                record,
                state="offline",
                reason=f"Protocol-incompatible advertisement: {reason}",
                now=now,
            )

    def record_success(self, key: ProviderKey, *, now: float, latency_ms: float) -> None:
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return
            record.last_probe_at = now
            record.last_success_at = now
            record.latency_ms = max(0.0, float(latency_ms))
            record.consecutive_successes += 1
            record.consecutive_failures = 0
            if not record.recovery_required or (
                record.consecutive_successes >= self.config.recovery_successes
            ):
                record.state = "healthy"
                record.reason = None
                record.recovery_required = False
            else:
                record.reason = (
                    "RPC recovery pending "
                    f"({record.consecutive_successes}/{self.config.recovery_successes})."
                )

    def record_failure(self, key: ProviderKey, *, now: float, reason: str) -> None:
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return
            record.last_probe_at = now
            record.last_failure_at = now
            record.consecutive_failures += 1
            record.consecutive_successes = 0
            record.reason = reason
            if record.consecutive_failures >= self.config.offline_threshold:
                record.state = "offline"
                record.recovery_required = True
            elif record.consecutive_failures >= self.config.failure_threshold:
                record.state = "degraded"
                record.recovery_required = True

    def schedule_next(self, key: ProviderKey, *, now: float, jitter: float) -> None:
        with self._lock:
            record = self._records.get(key)
            if record is None:
                return
            interval = (
                self.config.selected_interval_seconds
                if record.role == "selected"
                else self.config.standby_interval_seconds
            )
            record.next_probe_at = now + max(0.05, interval * (1.0 + jitter))

    def due(self, *, now: float, active: set[ProviderKey]) -> list[ProviderHealthRecord]:
        with self._lock:
            return [
                ProviderHealthRecord(**record.__dict__)
                for record in sorted(
                    self._records.values(),
                    key=lambda item: (item.role != "selected", item.next_probe_at, item.key),
                )
                if record.dht_present
                and record.protocol_compatible
                and record.next_probe_at <= now
                and record.key not in active
            ]

    def get(self, key: ProviderKey) -> dict | None:
        with self._lock:
            record = self._records.get(key)
            return record.to_dict() if record is not None else None

    def snapshot(self) -> dict:
        with self._lock:
            providers = [
                record.to_dict() for record in sorted(self._records.values(), key=lambda r: r.key)
            ]
        route_state = [
            {
                "peer_id": provider["peer_id"],
                "rpc_uid": provider["rpc_uid"],
                "state": provider["state"],
                "role": provider["role"],
                "dht_present": provider["dht_present"],
                "protocol_compatible": provider["protocol_compatible"],
            }
            for provider in providers
        ]
        revision = hashlib.sha256(
            json.dumps(route_state, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:20]
        return {"health_revision": revision, "providers": providers}

    def _set_unhealthy(
        self,
        record: ProviderHealthRecord,
        *,
        state: str,
        reason: str,
        now: float,
    ) -> None:
        record.state = state
        record.reason = reason
        record.last_failure_at = now
        record.consecutive_successes = 0
        record.recovery_required = True


@dataclass
class _ActiveProbe:
    thread: threading.Thread
    started_at: float
    timeout_failures_recorded: int = 0


class ProviderHealthMonitor:
    def __init__(
        self,
        *,
        config: ProviderHealthConfig,
        registry: ProviderHealthRegistry,
        discover: Callable[[], tuple[list[dict], list[dict]]],
        classify: Callable[[list[dict]], tuple[list[dict], list[dict]]],
        probe: Callable[[dict], None],
        clock: Callable[[], float] = time.time,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.config = config
        self.registry = registry
        self._discover = discover
        self._classify = classify
        self._probe = probe
        self._clock = clock
        self._jitter = jitter
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: dict[ProviderKey, _ActiveProbe] = {}
        self._active_lock = threading.Lock()
        self._latest_nodes: list[dict] = []
        self._latest_lock = threading.Lock()
        self._last_discovery_error: str | None = None
        self._next_discovery_at = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self.run_cycle()
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="provider-health-monitor",
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        probes_stopped = self.wait_for_idle(
            timeout=max(0.0, deadline - time.monotonic())
        )
        self._thread = None
        return (thread is None or not thread.is_alive()) and probes_stopped

    def _run(self) -> None:
        while not self._stop.wait(self.config.scheduler_interval_seconds):
            self.run_cycle()

    def run_cycle(self) -> None:
        now = self._clock()
        self._record_probe_timeouts(now)
        if now >= self._next_discovery_at:
            try:
                nodes, protocol_errors = self._discover()
                selected, _ = self._classify(nodes)
                selected_keys = {ProviderKey.from_node(node) for node in selected}
                self.registry.observe(nodes, selected_keys, now=now)
                for error in protocol_errors:
                    self.registry.record_protocol_failure(
                        peer_id=str(error.get("peer_id", "unknown")),
                        model_name=str(error.get("model_name", "unknown")),
                        reason=str(error.get("reason", "invalid metadata")),
                        now=now,
                    )
                with self._latest_lock:
                    self._latest_nodes = [dict(node) for node in nodes]
                self._last_discovery_error = None
                self._next_discovery_at = now + self.config.discovery_interval_seconds
            except Exception as exc:
                self._last_discovery_error = str(exc)
                return
        else:
            nodes = self.latest_nodes()

        with self._active_lock:
            active_keys = set(self._active)
            capacity = self.config.max_concurrency - len(active_keys)
        if capacity <= 0:
            return
        node_by_key = {ProviderKey.from_node(node): node for node in nodes}
        for record in self.registry.due(now=now, active=active_keys)[:capacity]:
            node = node_by_key.get(record.key)
            if node is not None:
                self._launch_probe(record.key, node)

    def _launch_probe(self, key: ProviderKey, node: dict) -> None:
        started_at = self._clock()

        def target() -> None:
            monotonic_started = time.perf_counter()
            try:
                self._probe(node)
                latency_ms = (time.perf_counter() - monotonic_started) * 1000
                self.registry.record_success(key, now=self._clock(), latency_ms=latency_ms)
            except Exception as exc:
                self.registry.record_failure(key, now=self._clock(), reason=str(exc))
            finally:
                if not self._stop.is_set():
                    ratio = self.config.jitter_ratio
                    self.registry.schedule_next(
                        key,
                        now=self._clock(),
                        jitter=self._jitter(-ratio, ratio),
                    )
                with self._active_lock:
                    self._active.pop(key, None)

        thread = threading.Thread(
            target=target,
            daemon=True,
            name=f"provider-health-{key.peer_id[:8]}",
        )
        with self._active_lock:
            if key in self._active or len(self._active) >= self.config.max_concurrency:
                return
            self._active[key] = _ActiveProbe(thread=thread, started_at=started_at)
        thread.start()

    def _record_probe_timeouts(self, now: float) -> None:
        timeout = self.config.probe_timeout_seconds
        with self._active_lock:
            active = list(self._active.items())
        for key, task in active:
            elapsed_windows = int((now - task.started_at) // timeout)
            while task.timeout_failures_recorded < elapsed_windows:
                task.timeout_failures_recorded += 1
                self.registry.record_failure(
                    key,
                    now=now,
                    reason=(
                        "RPC metadata probe exceeded "
                        f"{timeout:g} seconds (window {task.timeout_failures_recorded})."
                    ),
                )

    def latest_nodes(self) -> list[dict]:
        with self._latest_lock:
            return [dict(node) for node in self._latest_nodes]

    def snapshot(self) -> dict:
        with self._active_lock:
            active_count = len(self._active)
        return {
            **self.registry.snapshot(),
            "monitor_running": self.running,
            "active_probes": active_count,
            "last_discovery_error": self._last_discovery_error,
            "detection_window_seconds": self.config.detection_window_seconds,
        }

    def wait_for_idle(self, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._active_lock:
                if not self._active:
                    return True
            time.sleep(0.005)
        return False


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number; got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer; got {raw!r}") from exc


def get_provider_health_config() -> ProviderHealthConfig:
    return ProviderHealthConfig(
        selected_interval_seconds=_env_float("DISTRIBLLM_HEALTH_SELECTED_INTERVAL", 5.0),
        standby_interval_seconds=_env_float("DISTRIBLLM_HEALTH_STANDBY_INTERVAL", 15.0),
        probe_timeout_seconds=_env_float("DISTRIBLLM_HEALTH_PROBE_TIMEOUT", 3.0),
        failure_threshold=_env_int("DISTRIBLLM_HEALTH_FAILURE_THRESHOLD", 2),
        offline_threshold=_env_int("DISTRIBLLM_HEALTH_OFFLINE_THRESHOLD", 4),
        recovery_successes=_env_int("DISTRIBLLM_HEALTH_RECOVERY_SUCCESSES", 2),
        dht_stale_seconds=_env_float("DISTRIBLLM_HEALTH_DHT_STALE_SECONDS", 10.0),
        max_concurrency=_env_int("DISTRIBLLM_HEALTH_MAX_CONCURRENCY", 4),
        jitter_ratio=_env_float("DISTRIBLLM_HEALTH_JITTER_RATIO", 0.15),
        scheduler_interval_seconds=_env_float("DISTRIBLLM_HEALTH_SCHEDULER_INTERVAL", 0.25),
        discovery_interval_seconds=_env_float("DISTRIBLLM_HEALTH_DISCOVERY_INTERVAL", 2.0),
    )
