"""Backend-owned control-plane discovery and last-good topology state."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import CancelledError
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import hivemind
from hivemind.utils import get_dht_time
from hivemind.utils.logging import get_logger

from client.sequential import RemoteSequential
from constants import (
    ANNOUNCE_INTERVAL,
    DHT_EXPIRY_TIME,
    DHT_OPERATION_TIMEOUT,
    DHT_PREFIX,
    get_p2p_identity_dir,
)
from network.publication import (
    PublicationOutcome,
    classify_publication,
    unverified_outcome,
)
from network.infrastructure import summarize_infrastructure


logger = get_logger(__name__)

NETWORK_STATES = {"disconnected", "syncing", "ready", "degraded"}
DEFAULT_REFRESH_INTERVAL_SECONDS = max(
    1.0,
    float(os.environ.get("DISTRIBLLM_NETWORK_REFRESH_SECONDS", "5")),
)

EventSink = Callable[..., dict[str, Any]]
DHTFactory = Callable[[], Any]
TopologyScanner = Callable[[Any, str], tuple[list[dict], list[dict]]]


@dataclass
class _DHTShutdownAttempt:
    """One retained shutdown call for one exact control-plane DHT."""

    dht: Any
    name: str
    done: threading.Event
    thread: Optional[threading.Thread] = None
    error: Optional[BaseException] = None


@dataclass
class _DHTOperationAttempt:
    """One exact asynchronous operation that still owns the control DHT."""

    dht: Any
    future: Any
    name: str
    quiesced: threading.Event
    watcher_finished: threading.Event
    thread: Optional[threading.Thread] = None
    error: Optional[BaseException] = None
    ownership_uncertain: bool = False


def _shutdown_attempt_finished(attempt: _DHTShutdownAttempt) -> bool:
    thread = attempt.thread
    return bool(
        attempt.done.is_set()
        and (thread is None or not thread.is_alive())
    )


def _utc_from_epoch(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _failure(stage: str, exc: BaseException, *, retryable: bool = True) -> dict:
    return {
        "stage": stage,
        "code": type(exc).__name__,
        "message": str(exc),
        "observed_at": _utc_from_epoch(time.time()),
        "retryable": retryable,
    }


def _failure_signature(failure: Optional[dict]) -> Optional[tuple[Any, ...]]:
    if failure is None:
        return None
    return (
        failure.get("stage"),
        failure.get("code"),
        failure.get("message"),
        failure.get("retryable"),
    )


def _topology_revision(nodes: list[dict]) -> str:
    """Hash stable routing fields, excluding heartbeat timestamps and counters."""
    fields = (
        "peer_id",
        "node_id",
        "model_name",
        "layer_start",
        "layer_end",
        "running",
        "layers_loaded",
        "rpc_running",
        "rpc_uid",
        "rpc_uid_schema_version",
        "rpc_peer_id",
        "receipt_rpc_uid",
        "connection_mode",
        "transport_verified",
        "maddrs",
    )
    canonical = [
        {field: node.get(field) for field in fields}
        for node in sorted(
            nodes,
            key=lambda item: (
                str(item.get("model_name", "")),
                int(item.get("layer_start", 0)),
                int(item.get("layer_end", 0)),
                str(item.get("peer_id", "")),
                str(item.get("rpc_uid", "")),
            ),
        )
    ]
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def _provider_identity(node: dict) -> str:
    """Return a stable key for retaining one independently advertised worker."""
    return "|".join(
        (
            str(node.get("peer_id", "unknown")),
            str(node.get("node_id", "unknown")),
            str(node.get("model_name", "unknown")),
            str(node.get("layer_start", "unknown")),
            str(node.get("layer_end", "unknown")),
        )
    )


class NetworkSupervisor:
    """Own one discovery-only DHT without sharing its P2P identity with roles.

    Worker and generator runtimes remain separate. The supervisor owns only
    control-plane discovery, immutable last-good topology, role diagnostics,
    publication read-back, and its own deterministic shutdown.
    """

    def __init__(
        self,
        *,
        initial_peers: Optional[list[str]] = None,
        trusted_relays: Optional[list[str]] = None,
        dht_prefix: str = DHT_PREFIX,
        identity_path: Optional[Path | str] = None,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL_SECONDS,
        disappearance_grace: float = DHT_EXPIRY_TIME,
        dht_factory: Optional[DHTFactory] = None,
        scanner: Optional[TopologyScanner] = None,
        event_sink: Optional[EventSink] = None,
    ) -> None:
        if not dht_prefix.strip():
            raise ValueError("dht_prefix must not be empty")
        if refresh_interval <= 0:
            raise ValueError("refresh_interval must be positive")
        if disappearance_grace < 0:
            raise ValueError("disappearance_grace must not be negative")
        self.initial_peers = list(initial_peers or [])
        self.trusted_relays = list(trusted_relays or [])
        self.dht_prefix = dht_prefix
        self.identity_path = Path(
            identity_path
            or get_p2p_identity_dir() / "roles" / "control-plane.key"
        ).expanduser()
        self.refresh_interval = float(refresh_interval)
        self.disappearance_grace = float(disappearance_grace)
        self._dht_factory = dht_factory or self._create_control_dht
        self._scanner = scanner or self._scan_topology
        self._event_sink = event_sink

        self._lock = threading.RLock()
        # Serializes every operation that borrows the attached control DHT.
        # Shutdown takes this lock before detaching the exact instance.
        self._refresh_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._dht: Optional[Any] = None
        self._dht_operation: Optional[_DHTOperationAttempt] = None
        self._dht_shutdown: Optional[_DHTShutdownAttempt] = None
        self._closing = False
        self._state = "disconnected"
        self._revision = 0
        self._started_at: Optional[float] = None
        self._updated_at = time.time()
        self._last_refresh_attempt_at: Optional[float] = None
        self._last_refresh_success_at: Optional[float] = None
        self._snapshot_captured_at: Optional[float] = None
        self._topology_revision: Optional[str] = None
        self._nodes: list[dict] = []
        self._node_last_seen_at: dict[str, float] = {}
        self._retained_provider_count = 0
        self._validation_errors: list[dict] = []
        self._last_failure: Optional[dict] = None
        self._roles: dict[str, Any] = {"workers": {}, "generator": None}
        self._publication_dispositions: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> dict:
        """Start discovery asynchronously so a bad bootstrap cannot block HTTP."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._closing:
                    raise RuntimeError(
                        "Control-plane discovery shutdown is still in progress"
                    )
                return self.snapshot()
            operation = self._dht_operation
            if operation is not None:
                raise RuntimeError(
                    "A control-plane DHT operation still owns the stable peer "
                    "identity; restart is unsafe until exact quiescence is observed"
                )
            shutdown = self._dht_shutdown
            if shutdown is not None:
                if not _shutdown_attempt_finished(shutdown):
                    raise RuntimeError(
                        "Control-plane DHT shutdown is still in progress"
                    )
                if shutdown.error is not None:
                    raise RuntimeError(
                        "The previous control-plane DHT shutdown failed; refusing "
                        "to reuse its stable peer identity"
                    ) from shutdown.error
                self._dht_shutdown = None
            if self._dht is not None:
                raise RuntimeError(
                    "A previous control-plane DHT is still attached without an "
                    "active discovery task"
                )
            self._closing = False
            self._stop_event.clear()
            self._wake_event.clear()
            self._started_at = time.time()
            self._transition_locked("syncing", failure=None)
            self._thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="distribllm-network-supervisor",
            )
            self._thread.start()
        self._emit(
            phase="starting",
            status="info",
            message="Control-plane discovery supervisor is starting.",
        )
        return self.snapshot()

    def begin_shutdown(self) -> None:
        with self._lock:
            self._closing = True
        self._stop_event.set()
        self._wake_event.set()

    def stop(self, timeout: float = 5.0) -> dict:
        """Stop the refresh loop and close the control DHT at most once."""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = time.monotonic() + timeout
        self.begin_shutdown()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, deadline - time.monotonic()))

        with self._lock:
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
            orphaned_dht = self._dht if not thread_alive else None

        # The discovery thread normally owns detachment after its final scan.
        # If it died before doing so, take the shared control-DHT I/O lock before
        # touching the exact DHT so no scan or publication readback is using it.
        if orphaned_dht is not None:
            remaining = max(0.0, deadline - time.monotonic())
            refresh_quiesced = self._refresh_lock.acquire(
                timeout=remaining,
            )
            if refresh_quiesced:
                try:
                    with self._lock:
                        current_thread_alive = bool(
                            self._thread is not None and self._thread.is_alive()
                        )
                        if not current_thread_alive and self._dht is orphaned_dht:
                            self._dht = None
                            self._begin_dht_shutdown_locked(
                                orphaned_dht,
                                name="network-supervisor-dht-shutdown",
                            )
                finally:
                    self._refresh_lock.release()

        with self._lock:
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
            shutdown = self._dht_shutdown
        if not thread_alive and shutdown is not None:
            shutdown_thread = shutdown.thread
            if (
                shutdown_thread is not None
                and shutdown_thread is not threading.current_thread()
            ):
                shutdown_thread.join(max(0.0, deadline - time.monotonic()))

        with self._lock:
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
            shutdown = self._dht_shutdown
            operation = self._dht_operation
            shutdown_error = (
                shutdown.error
                if shutdown is not None and _shutdown_attempt_finished(shutdown)
                else None
            )
            shutdown_pending = bool(
                shutdown is not None and not _shutdown_attempt_finished(shutdown)
            )
            if (
                shutdown is not None
                and _shutdown_attempt_finished(shutdown)
                and shutdown.error is None
            ):
                self._dht_shutdown = None
                shutdown = None
            dht_stopped = bool(
                not thread_alive
                and self._dht is None
                and operation is None
                and shutdown is None
            )
            if not dht_stopped:
                exc = shutdown_error or TimeoutError(
                    "Network supervisor did not stop within its deadline"
                )
                self._transition_locked("degraded", failure=_failure("shutdown", exc))
            else:
                self._transition_locked("disconnected", failure=None)
        self._emit(
            phase="stopped" if not thread_alive and dht_stopped else "stopping",
            status="info" if not thread_alive and dht_stopped else "error",
            message=(
                "Control-plane discovery supervisor stopped."
                if not thread_alive and dht_stopped
                else "Control-plane discovery supervisor shutdown timed out."
            ),
        )
        return {
            "status": "stopped" if not thread_alive and dht_stopped else "timeout",
            "thread_stopped": not thread_alive,
            "dht_stopped": dht_stopped,
            "dht_operation_pending": operation is not None,
            "dht_shutdown_pending": shutdown_pending,
        }

    @property
    def accepting_roles(self) -> bool:
        with self._lock:
            return not self._closing

    @property
    def dht(self) -> Optional[Any]:
        with self._lock:
            return self._dht

    def request_refresh(self) -> None:
        self._wake_event.set()

    def _run(self) -> None:
        try:
            while not self._stop_event.is_set():
                if self.dht is None:
                    with self._lock:
                        cleanup = self._dht_shutdown
                        if (
                            cleanup is not None
                            and _shutdown_attempt_finished(cleanup)
                            and cleanup.error is None
                        ):
                            self._dht_shutdown = None
                            cleanup = None
                    if cleanup is not None:
                        cleanup_error = (
                            cleanup.error
                            if _shutdown_attempt_finished(cleanup)
                            and cleanup.error is not None
                            else RuntimeError(
                                "Previous control-plane DHT identity cleanup is "
                                "still in progress"
                            )
                        )
                        self._record_refresh_failure(
                            "control_dht_cleanup",
                            cleanup_error,
                        )
                        if self._wait_for_next_refresh():
                            return
                        continue
                    try:
                        candidate = self._dht_factory()
                        if self._stop_event.is_set():
                            with self._lock:
                                self._begin_dht_shutdown_locked(
                                    candidate,
                                    name="late-control-dht-shutdown",
                                )
                            return
                        with self._lock:
                            self._dht = candidate
                            self._transition_locked("syncing", failure=None)
                        self._emit(
                            phase="syncing",
                            status="info",
                            message="Control-plane peer is online and synchronizing topology.",
                            details={"control_peer_id": self._control_peer_id(candidate)},
                        )
                    except Exception as exc:
                        self._record_refresh_failure("control_dht_start", exc)
                        if self._wait_for_next_refresh():
                            return
                        continue

                try:
                    self.refresh_now()
                except Exception:
                    # refresh_now already records a bounded structured failure.
                    pass
                if self._wait_for_next_refresh():
                    return
        finally:
            # Control-DHT readers acquire _refresh_lock before _lock. Use the
            # same ordering here so a scan or publication readback cannot retain
            # the exact DHT after this finalizer detaches it for shutdown.
            with self._refresh_lock:
                with self._lock:
                    closing = self._closing
                    if closing:
                        dht = self._dht
                        self._dht = None
                        if dht is not None:
                            self._begin_dht_shutdown_locked(
                                dht,
                                name="network-supervisor-dht-shutdown",
                            )
                    if self._thread is threading.current_thread():
                        self._thread = None
                    if closing:
                        shutdown = self._dht_shutdown
                        if shutdown is None or (
                            shutdown.done.is_set() and shutdown.error is None
                        ):
                            self._transition_locked("disconnected", failure=None)
                        elif shutdown.done.is_set():
                            self._transition_locked(
                                "degraded",
                                failure=_failure(
                                    "shutdown",
                                    shutdown.error
                                    or RuntimeError(
                                        "Control-plane DHT shutdown failed"
                                    ),
                                ),
                            )

    def _begin_dht_shutdown_locked(
        self,
        dht: Any,
        *,
        name: str,
    ) -> _DHTShutdownAttempt:
        """Start or return the one retained shutdown attempt for ``dht``."""
        existing = self._dht_shutdown
        if existing is not None:
            if existing.dht is dht:
                return existing
            if (
                not _shutdown_attempt_finished(existing)
                or existing.error is not None
            ):
                raise RuntimeError(
                    "Cannot shut down another control-plane DHT while cleanup "
                    "for the previous stable identity is unresolved"
                )
            self._dht_shutdown = None

        attempt = _DHTShutdownAttempt(
            dht=dht,
            name=name,
            done=threading.Event(),
        )
        self._dht_shutdown = attempt

        def run() -> None:
            try:
                dht.shutdown()
                self._verify_dht_process_stopped(dht)
            except BaseException as exc:
                attempt.error = exc
                logger.warning(
                    "%s raised during shutdown: %s",
                    name,
                    exc,
                    exc_info=True,
                )
            finally:
                attempt.done.set()
                self._finish_dht_shutdown(attempt)

        thread = threading.Thread(target=run, daemon=True, name=name)
        attempt.thread = thread
        thread.start()
        return attempt

    @staticmethod
    def _verify_dht_process_stopped(dht: Any) -> None:
        """Require positive process-death evidence after ``DHT.shutdown``."""
        is_alive = getattr(dht, "is_alive", None)
        if not callable(is_alive):
            raise RuntimeError(
                "Control-plane DHT shutdown returned without a process-liveness "
                "probe; stable identity release cannot be verified"
            )
        try:
            alive = bool(is_alive())
        except Exception as exc:
            raise RuntimeError(
                "Control-plane DHT process liveness could not be verified"
            ) from exc
        if alive:
            join = getattr(dht, "join", None)
            if callable(join):
                shutdown_timeout = getattr(dht, "shutdown_timeout", 0.0)
                try:
                    verification_timeout = min(
                        DHT_OPERATION_TIMEOUT,
                        max(0.0, float(shutdown_timeout)),
                    )
                except (TypeError, ValueError):
                    verification_timeout = 0.0
                join(timeout=verification_timeout)
            try:
                alive = bool(is_alive())
            except Exception as exc:
                raise RuntimeError(
                    "Control-plane DHT process liveness could not be rechecked"
                ) from exc
        if alive:
            raise RuntimeError(
                "Control-plane DHT process is still alive after shutdown; "
                "stable identity remains quarantined"
            )

    def _retain_dht_operation(
        self,
        *,
        dht: Any,
        future: Any,
        name: str,
    ) -> _DHTOperationAttempt:
        """Retain DHT ownership until a timed-out future truly terminates.

        The caller must hold ``_refresh_lock`` and must transfer release of that
        lock to this watcher.  A cancelled or otherwise non-terminal future is
        deliberately quarantined because Hivemind cancellation only cancels
        the local ``MPFuture`` and does not prove the child DHT task stopped.
        """
        attempt = _DHTOperationAttempt(
            dht=dht,
            future=future,
            name=name,
            quiesced=threading.Event(),
            watcher_finished=threading.Event(),
        )
        with self._lock:
            if self._dht_operation is not None:
                raise RuntimeError(
                    "Another unresolved control-plane DHT operation already "
                    "owns the stable identity"
                )
            self._dht_operation = attempt

        def watch() -> None:
            quiesced = False
            try:
                future.result(timeout=None)
                quiesced = True
            except BaseException as exc:
                attempt.error = exc
                cancelled = isinstance(exc, CancelledError)
                cancelled_probe = getattr(future, "cancelled", None)
                if callable(cancelled_probe):
                    try:
                        cancelled = cancelled or bool(cancelled_probe())
                    except Exception:
                        cancelled = True
                done_probe = getattr(future, "done", None)
                terminal = False
                if callable(done_probe) and not cancelled:
                    try:
                        terminal = bool(done_probe())
                    except Exception:
                        terminal = False
                quiesced = terminal
                attempt.ownership_uncertain = not quiesced
            finally:
                if quiesced:
                    attempt.quiesced.set()
                    with self._lock:
                        if self._dht_operation is attempt:
                            self._dht_operation = None
                    self._refresh_lock.release()
                else:
                    uncertainty = RuntimeError(
                        f"{name} ended without proof that its child DHT task "
                        "quiesced; the stable identity remains quarantined"
                    )
                    self._record_refresh_failure(
                        "dht_operation_quiescence",
                        uncertainty,
                    )
                attempt.watcher_finished.set()

        thread = threading.Thread(
            target=watch,
            daemon=True,
            name=f"distribllm-{name}-quiescence",
        )
        attempt.thread = thread
        try:
            thread.start()
        except BaseException as exc:
            attempt.error = exc
            attempt.ownership_uncertain = True
            attempt.watcher_finished.set()
            self._record_refresh_failure("dht_operation_quiescence", exc)
        return attempt

    def _finish_dht_shutdown(self, attempt: _DHTShutdownAttempt) -> None:
        """Publish eventual shutdown completion without forgetting its handle."""
        with self._lock:
            if self._dht_shutdown is not attempt or not self._closing:
                return
            supervisor_alive = bool(
                self._thread is not None and self._thread.is_alive()
            )
            if supervisor_alive:
                return
            if attempt.error is None:
                self._transition_locked("disconnected", failure=None)
            else:
                self._transition_locked(
                    "degraded",
                    failure=_failure("shutdown", attempt.error),
                )

    def _wait_for_next_refresh(self) -> bool:
        self._wake_event.wait(self.refresh_interval)
        self._wake_event.clear()
        return self._stop_event.is_set()

    # ------------------------------------------------------------------
    # Discovery and state
    # ------------------------------------------------------------------

    def refresh_now(self) -> dict:
        """Refresh once; only a successful validated scan can replace topology."""
        with self._refresh_lock:
            with self._lock:
                dht = self._dht
                closing = self._closing
            if closing:
                raise RuntimeError(
                    "Control-plane discovery is shutting down; refresh admission "
                    "is closed"
                )
            if dht is None:
                exc = RuntimeError("Control-plane DHT is not connected")
                self._record_refresh_failure("topology_lookup", exc)
                raise exc
            with self._lock:
                self._last_refresh_attempt_at = time.time()
            try:
                nodes, validation_errors = self._scanner(dht, self.dht_prefix)
                if not isinstance(nodes, list) or not isinstance(validation_errors, list):
                    raise TypeError("Topology scanner returned an invalid result")
                captured_at = time.time()
                with self._lock:
                    if self._closing or self._dht is not dht:
                        raise RuntimeError(
                            "Topology scan completed after control-plane shutdown "
                            "started; its result was discarded"
                        )
                    previous_state = self._state
                    previous_revision = self._topology_revision
                    previous_retained_count = self._retained_provider_count
                    previous_nodes = {
                        _provider_identity(node): copy.deepcopy(node)
                        for node in self._nodes
                    }
                    current_nodes = {
                        _provider_identity(node): copy.deepcopy(node)
                        for node in nodes
                    }
                    for identity in current_nodes:
                        self._node_last_seen_at[identity] = captured_at
                    unresolved_missing_nodes = []
                    for identity, previous_node in previous_nodes.items():
                        if identity in current_nodes:
                            continue
                        last_seen_at = self._node_last_seen_at.get(
                            identity,
                            self._snapshot_captured_at or captured_at,
                        )
                        if captured_at - last_seen_at < self.disappearance_grace:
                            unresolved_missing_nodes.append(previous_node)
                        else:
                            self._node_last_seen_at.pop(identity, None)

                    candidate_nodes = list(current_nodes.values())
                    retained_errors = [
                        {
                            "kind": "provider_disappearance_unconfirmed",
                            "provider": _provider_identity(node),
                            "reason": (
                                "The provider was absent from the latest scan but "
                                "remains within its DHT lease horizon."
                            ),
                        }
                        for node in unresolved_missing_nodes
                    ]
                    self._validation_errors = copy.deepcopy(
                        [*validation_errors, *retained_errors]
                    )
                    if unresolved_missing_nodes:
                        # Keep the exact previous snapshot and its capture time.
                        # Mixing newly scanned records into old retained records
                        # would create a topology that was never observed at one
                        # point in time and then lie about its age.
                        topology_revision = self._topology_revision
                        self._retained_provider_count = len(
                            unresolved_missing_nodes
                        )
                        visibility_error = RuntimeError(
                            f"{len(unresolved_missing_nodes)} recently observed "
                            "provider(s) "
                            "were absent from the latest DHT scan"
                        )
                        self._transition_locked(
                            "degraded",
                            failure=_failure(
                                "topology_visibility",
                                visibility_error,
                            ),
                        )
                    else:
                        topology_revision = _topology_revision(candidate_nodes)
                        self._nodes = candidate_nodes
                        self._topology_revision = topology_revision
                        self._retained_provider_count = 0
                        self._node_last_seen_at = {
                            identity: self._node_last_seen_at[identity]
                            for identity in current_nodes
                        }
                        self._snapshot_captured_at = captured_at
                        self._last_refresh_success_at = captured_at
                        self._transition_locked("ready", failure=None)
                phase = "recovered" if previous_state == "degraded" else "snapshot"
                if unresolved_missing_nodes:
                    if (
                        previous_state != "degraded"
                        or previous_retained_count != len(unresolved_missing_nodes)
                    ):
                        self._emit(
                            phase="degraded",
                            status="error",
                            message=(
                                "Control-plane discovery retained recently observed "
                                "providers missing from an ambiguous DHT scan."
                            ),
                            details={
                                "topology_revision": topology_revision,
                                "provider_count": len(self._nodes),
                                "retained_provider_count": len(
                                    unresolved_missing_nodes
                                ),
                                "last_good_retained": True,
                                "failure_stage": "topology_visibility",
                            },
                        )
                elif previous_revision != topology_revision or previous_state != "ready":
                    self._emit(
                        phase=phase,
                        status="success",
                        message=(
                            f"Control-plane topology contains {len(nodes)} provider record(s)."
                        ),
                        details={
                            "topology_revision": topology_revision,
                            "provider_count": len(nodes),
                            "validation_error_count": len(validation_errors),
                        },
                    )
                return self.snapshot()
            except Exception as exc:
                self._record_refresh_failure("topology_lookup", exc)
                raise

    def _record_refresh_failure(self, stage: str, exc: BaseException) -> None:
        failure = _failure(stage, exc)
        with self._lock:
            semantic_changed = (
                self._state != "degraded"
                or _failure_signature(self._last_failure)
                != _failure_signature(failure)
            )
            self._last_refresh_attempt_at = time.time()
            self._retained_provider_count = len(self._nodes)
            self._transition_locked("degraded", failure=failure)
        if not semantic_changed:
            logger.debug("Network supervisor %s is still failing: %s", stage, exc)
            return
        logger.warning("Network supervisor %s failed: %s", stage, exc)
        self._emit(
            phase="degraded",
            status="error",
            message=f"Control-plane {stage} failed: {exc}",
            details={
                "failure_stage": stage,
                "failure_code": type(exc).__name__,
                "topology_revision": self._topology_revision,
                "last_good_retained": self._snapshot_captured_at is not None,
            },
        )

    @staticmethod
    def _scan_topology(dht: Any, dht_prefix: str) -> tuple[list[dict], list[dict]]:
        sequential = RemoteSequential(
            dht=dht,
            dht_prefix=dht_prefix,
            num_layers=0,
            model_name=None,
        )
        return sequential._scan_node_metadata()

    def _create_control_dht(self) -> hivemind.DHT:
        self.identity_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.identity_path.parent.chmod(0o700)
        if self.identity_path.exists():
            if not self.identity_path.is_file():
                raise RuntimeError("Control-plane P2P identity target is not a file")
            self.identity_path.chmod(0o600)
        dht = hivemind.DHT(
            initial_peers=self.initial_peers,
            start=True,
            use_ipfs=False,
            use_relay=True,
            client_mode=True,
            cache_locally=False,
            cache_nearest=0,
            cache_on_store=False,
            identity_path=str(self.identity_path),
        )
        try:
            if dht.peer_id is None:
                raise RuntimeError(
                    "Control-plane DHT started without a peer identity"
                )
            if self.identity_path.is_file():
                self.identity_path.chmod(0o600)
        except BaseException:
            with self._lock:
                self._begin_dht_shutdown_locked(
                    dht,
                    name="uncommitted-control-dht-shutdown",
                )
            raise
        return dht

    # ------------------------------------------------------------------
    # Role registry and snapshots
    # ------------------------------------------------------------------

    def register_role(
        self,
        role: str,
        *,
        peer_id: Optional[str],
        state: str,
        role_id: Optional[str] = None,
    ) -> None:
        if role not in {"worker", "generator"}:
            raise ValueError(f"Unknown network role: {role}")
        if not isinstance(state, str) or not state.strip():
            raise ValueError("Network role state must not be empty")
        with self._lock:
            if self._closing:
                raise RuntimeError("Backend network lifecycle is shutting down")
            if role == "worker" and not role_id:
                raise ValueError("role_id is required for a worker")
            self._assert_distinct_peer_locked(
                role,
                peer_id=peer_id,
                role_id=role_id,
            )
            existing = (
                self._roles["workers"].get(role_id)
                if role == "worker"
                else self._roles["generator"]
            )
            if isinstance(existing, dict):
                existing_peer_id = existing.get("peer_id")
                existing_active = existing.get("state") not in {
                    "stopped",
                    "failed",
                }
                if (
                    existing_active
                    and existing_peer_id is not None
                    and peer_id != existing_peer_id
                ):
                    role_label = (
                        f"worker:{role_id}" if role == "worker" else "generator"
                    )
                    raise ValueError(
                        f"Active {role_label} peer identity cannot rotate from "
                        f"{existing_peer_id!r} to {peer_id!r}; stop or unregister "
                        "the exact runtime before replacing its stable identity"
                    )
            if (
                isinstance(existing, dict)
                and existing.get("peer_id") == peer_id
                and existing.get("state") == state
            ):
                return
            record = {
                "peer_id": peer_id,
                "state": state,
                "updated_at": _utc_from_epoch(time.time()),
            }
            if role == "worker":
                record["node_id"] = role_id
                self._roles["workers"][role_id] = record
            else:
                self._roles["generator"] = record
            self._revision += 1
            self._updated_at = time.time()

    def unregister_role(self, role: str, *, role_id: Optional[str] = None) -> None:
        with self._lock:
            changed = False
            if role == "worker" and role_id:
                changed = self._roles["workers"].pop(role_id, None) is not None
            elif role == "generator":
                changed = self._roles["generator"] is not None
                self._roles["generator"] = None
            elif role not in {"worker", "generator"}:
                raise ValueError(f"Unknown network role: {role}")
            if changed:
                self._revision += 1
                self._updated_at = time.time()

    def _assert_distinct_peer_locked(
        self,
        role: str,
        *,
        peer_id: Optional[str],
        role_id: Optional[str],
    ) -> None:
        """Reject self-dial-prone identity reuse across co-located roles."""
        if peer_id is None:
            return
        normalized = peer_id.strip()
        if not normalized:
            raise ValueError("Network role peer_id must not be empty")

        occupied: dict[str, str] = {}
        control_peer_id = self._control_peer_id(self._dht)
        if control_peer_id is not None:
            occupied[control_peer_id] = "control-plane"
        for worker_node_id, worker in self._roles["workers"].items():
            if role == "worker" and worker_node_id == role_id:
                continue
            worker_peer_id = worker.get("peer_id")
            if worker_peer_id:
                occupied[str(worker_peer_id)] = f"worker:{worker_node_id}"
        generator = self._roles["generator"]
        if role != "generator" and isinstance(generator, dict):
            generator_peer_id = generator.get("peer_id")
            if generator_peer_id:
                occupied[str(generator_peer_id)] = "generator"

        conflicting_role = occupied.get(normalized)
        if conflicting_role is not None:
            raise ValueError(
                f"Network peer identity is already owned by {conflicting_role}; "
                f"{role} must use a distinct stable identity"
            )

    def nodes_for_model(self, model_name: Optional[str] = None) -> list[dict]:
        with self._lock:
            nodes = copy.deepcopy(self._nodes)
        if model_name is None:
            return nodes
        return [node for node in nodes if node.get("model_name") == model_name]

    def verify_publication(
        self,
        *,
        key: str,
        subkey: Optional[str],
        store_returned: Optional[bool],
        expected_value: Any,
        attempted_expiration: float,
        equivalent: Callable[[Any, Any], bool],
        minimum_safe_expiration: Optional[float] = None,
    ) -> PublicationOutcome:
        """Classify a worker lease through this distinct cache-disabled DHT."""
        minimum_safe = (
            float(minimum_safe_expiration)
            if minimum_safe_expiration is not None
            else get_dht_time() + max(ANNOUNCE_INTERVAL, DHT_OPERATION_TIMEOUT)
        )
        if store_returned is True:
            outcome = classify_publication(
                key=key,
                subkey=subkey,
                store_returned=True,
                expected_value=expected_value,
                observed_value=None,
                observed_expiration=None,
                attempted_expiration=attempted_expiration,
                minimum_safe_expiration=minimum_safe,
                equivalent=equivalent,
            )
            self._emit_publication(outcome)
            return outcome

        self._refresh_lock.acquire()
        release_refresh_lock = True
        try:
            with self._lock:
                dht = self._dht
                closing = self._closing
            if dht is None or closing:
                outcome = unverified_outcome(
                    key=key,
                    subkey=subkey,
                    store_returned=store_returned,
                    attempted_expiration=attempted_expiration,
                    minimum_safe_expiration=minimum_safe,
                    reason=(
                        "control_dht_closing"
                        if closing
                        else "control_dht_unavailable"
                    ),
                )
            else:
                try:
                    try:
                        pending = dht.get(key, latest=True, return_future=True)
                    except TypeError as exc:
                        if "return_future" not in str(exc):
                            raise
                        observed = dht.get(key, latest=True)
                    else:
                        try:
                            observed = pending.result(timeout=DHT_OPERATION_TIMEOUT)
                        except Exception as exc:
                            done_probe = getattr(pending, "done", None)
                            cancelled_probe = getattr(pending, "cancelled", None)
                            try:
                                cancelled = bool(
                                    callable(cancelled_probe)
                                    and cancelled_probe()
                                )
                            except Exception:
                                cancelled = True
                            try:
                                terminal = bool(
                                    callable(done_probe)
                                    and done_probe()
                                    and not cancelled
                                )
                            except Exception:
                                terminal = False
                            if not terminal:
                                # Transfer release of _refresh_lock to the exact
                                # future watcher. MPFuture.cancel() only changes
                                # the caller-side future and is not quiescence
                                # evidence for the child DHT coroutine.
                                release_refresh_lock = False
                                self._retain_dht_operation(
                                    dht=dht,
                                    future=pending,
                                    name="publication-readback",
                                )
                                if isinstance(exc, FutureTimeoutError):
                                    raise TimeoutError(
                                        "Independent publication verification "
                                        f"exceeded {DHT_OPERATION_TIMEOUT:g} "
                                        "seconds; exact DHT ownership is retained"
                                    ) from exc
                                raise RuntimeError(
                                    "Independent publication verification ended "
                                    "without confirmed DHT-operation quiescence; "
                                    "exact ownership is retained"
                                ) from exc
                            raise

                    observed_value, observed_expiration = _select_observed_record(
                        observed,
                        subkey=subkey,
                    )
                    outcome = classify_publication(
                        key=key,
                        subkey=subkey,
                        store_returned=store_returned,
                        expected_value=expected_value,
                        observed_value=observed_value,
                        observed_expiration=observed_expiration,
                        attempted_expiration=attempted_expiration,
                        minimum_safe_expiration=minimum_safe,
                        equivalent=equivalent,
                    )
                except Exception as exc:
                    outcome = unverified_outcome(
                        key=key,
                        subkey=subkey,
                        store_returned=store_returned,
                        attempted_expiration=attempted_expiration,
                        minimum_safe_expiration=minimum_safe,
                        reason=f"verification_{type(exc).__name__}: {exc}",
                    )
        finally:
            if release_refresh_lock:
                self._refresh_lock.release()
        self._emit_publication(outcome)
        return outcome

    def snapshot(self) -> dict:
        with self._lock:
            now = time.time()
            captured_at = self._snapshot_captured_at
            thread_alive = bool(self._thread is not None and self._thread.is_alive())
            workers = [
                copy.deepcopy(record)
                for _node_id, record in sorted(self._roles["workers"].items())
            ]
            generator = copy.deepcopy(self._roles["generator"])
            active_workers = [
                worker
                for worker in workers
                if worker.get("state") not in {"stopped", "failed"}
            ]
            shutdown = self._dht_shutdown
            operation = self._dht_operation
            return {
                "schema_version": 1,
                "state": self._state,
                "revision": self._revision,
                "topology_revision": self._topology_revision,
                "control_peer_id": self._control_peer_id(self._dht),
                "started_at": _utc_from_epoch(self._started_at),
                "updated_at": _utc_from_epoch(self._updated_at),
                "last_refresh_attempt_at": _utc_from_epoch(
                    self._last_refresh_attempt_at
                ),
                "last_refresh_success_at": _utc_from_epoch(
                    self._last_refresh_success_at
                ),
                "snapshot_captured_at": _utc_from_epoch(captured_at),
                "snapshot_age_seconds": (
                    max(0.0, now - captured_at) if captured_at is not None else None
                ),
                "stale": self._state != "ready",
                "provider_count": len(self._nodes),
                "retained_provider_count": self._retained_provider_count,
                "failure": copy.deepcopy(self._last_failure),
                "validation_errors": copy.deepcopy(self._validation_errors),
                "nodes": copy.deepcopy(self._nodes),
                "infrastructure": summarize_infrastructure(
                    self.initial_peers,
                    self.trusted_relays,
                    network_state=self._state,
                ),
                "roles": {"workers": workers, "generator": generator},
                "resources": {
                    "control_dht": self._dht is not None,
                    "control_dht_operation_pending": operation is not None,
                    "control_dht_operation_uncertain": bool(
                        operation is not None and operation.ownership_uncertain
                    ),
                    "control_dht_shutdown_pending": bool(
                        shutdown is not None
                        and not _shutdown_attempt_finished(shutdown)
                    ),
                    "control_dht_shutdown_failed": bool(
                        shutdown is not None
                        and _shutdown_attempt_finished(shutdown)
                        and shutdown.error is not None
                    ),
                    "discovery_task": thread_alive,
                    "registered_workers": len(workers),
                    "publication_tasks": len(active_workers),
                    "health_monitors": int(
                        generator is not None
                        and generator.get("state") not in {"stopped", "failed"}
                    ),
                },
                "accepting_roles": not self._closing,
            }

    def _transition_locked(
        self,
        state: str,
        *,
        failure: Optional[dict],
    ) -> None:
        if state not in NETWORK_STATES:
            raise ValueError(f"Unknown network state: {state}")
        changed = (
            state != self._state
            or _failure_signature(failure) != _failure_signature(self._last_failure)
        )
        self._state = state
        self._last_failure = copy.deepcopy(failure)
        self._updated_at = time.time()
        if changed:
            self._revision += 1

    @staticmethod
    def _control_peer_id(dht: Optional[Any]) -> Optional[str]:
        if dht is None:
            return None
        try:
            return str(dht.peer_id) if dht.peer_id is not None else None
        except Exception:
            return None

    def _emit(
        self,
        *,
        phase: str,
        status: str,
        message: str,
        details: Optional[dict] = None,
    ) -> None:
        if self._event_sink is None:
            return
        try:
            self._event_sink(
                kind="network",
                phase=phase,
                status=status,
                message=message,
                details=details or {},
            )
        except Exception as exc:
            logger.warning("Could not record network lifecycle event: %s", exc)

    def _emit_publication(self, outcome: PublicationOutcome) -> None:
        if self._event_sink is None:
            return
        details = outcome.to_dict()
        label = (
            details["key"]
            if details["subkey"] is None
            else f"{details['key']}[{details['subkey']}]"
        )
        with self._lock:
            previous = self._publication_dispositions.get(label)
            self._publication_dispositions[label] = details["disposition"]
        if previous == details["disposition"]:
            return
        try:
            self._event_sink(
                kind="publication",
                phase=details["disposition"],
                status="success" if outcome.publication_safe else "error",
                message=(
                    "Worker publication was independently classified as "
                    f"{details['disposition']}."
                ),
                details=details,
            )
        except Exception as exc:
            logger.warning("Could not record publication event: %s", exc)


def _select_observed_record(
    observed: Any,
    *,
    subkey: Optional[str],
) -> tuple[Any, Optional[float]]:
    if observed is None:
        return None, None
    if subkey is None:
        return (
            getattr(observed, "value", None),
            getattr(observed, "expiration_time", None),
        )
    mapping = getattr(observed, "value", None)
    if not isinstance(mapping, dict):
        return None, None
    wrapped = mapping.get(subkey)
    if wrapped is None:
        return None, None
    return (
        getattr(wrapped, "value", wrapped),
        getattr(wrapped, "expiration_time", None),
    )
