import copy
import sys
import tempfile
import threading
import time
import unittest
from collections import deque
from concurrent.futures import CancelledError
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from network.supervisor import NetworkSupervisor


def wait_for_state(
    supervisor: NetworkSupervisor,
    expected: str,
    timeout: float = 2.0,
) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = supervisor.snapshot()
        if snapshot["state"] == expected:
            return snapshot
        time.sleep(0.01)
    raise AssertionError(
        f"Network supervisor did not reach {expected!r}; "
        f"last snapshot was {supervisor.snapshot()!r}"
    )


class FakeDHT:
    def __init__(self, peer_id: str = "control-peer") -> None:
        self.peer_id = peer_id
        self.shutdown_count = 0
        self.shutdown_timeout = 0.0
        self._alive = True
        self._lock = threading.Lock()

    def shutdown(self) -> None:
        with self._lock:
            self.shutdown_count += 1
            self._alive = False

    def is_alive(self) -> bool:
        with self._lock:
            return self._alive

    def join(self, timeout: float = 0.0) -> None:
        del timeout


class BlockingShutdownDHT(FakeDHT):
    def __init__(self, peer_id: str = "control-peer") -> None:
        super().__init__(peer_id=peer_id)
        self.shutdown_entered = threading.Event()
        self.release_shutdown = threading.Event()

    def shutdown(self) -> None:
        with self._lock:
            self.shutdown_count += 1
        self.shutdown_entered.set()
        if not self.release_shutdown.wait(2):
            raise TimeoutError("test did not release DHT shutdown")
        with self._lock:
            self._alive = False


class ValueWithExpiration:
    def __init__(self, value, expiration_time: float) -> None:
        self.value = value
        self.expiration_time = expiration_time


class ScriptedScanner:
    def __init__(self, *results) -> None:
        self._results = deque(results)
        self._lock = threading.Lock()
        self.calls = 0

    def __call__(self, dht, dht_prefix):
        with self._lock:
            self.calls += 1
            if not self._results:
                raise AssertionError("Unexpected topology scan")
            result = self._results.popleft()
        if isinstance(result, BaseException):
            raise result
        return copy.deepcopy(result)


class NetworkSupervisorTests(unittest.TestCase):
    def test_clean_start_is_async_and_discovers_before_any_runtime_role(self) -> None:
        factory_entered = threading.Event()
        release_factory = threading.Event()
        fake_dht = FakeDHT()
        nodes = [
            {
                "peer_id": "remote-worker",
                "model_name": "facebook/opt-125m",
                "layer_start": 0,
                "layer_end": 6,
                "running": True,
                "layers_loaded": True,
                "rpc_running": True,
            }
        ]

        def blocking_factory():
            factory_entered.set()
            if not release_factory.wait(1):
                raise TimeoutError("test did not release DHT factory")
            return fake_dht

        supervisor = NetworkSupervisor(
            dht_factory=blocking_factory,
            scanner=ScriptedScanner((nodes, [])),
            refresh_interval=60,
        )
        try:
            started_at = time.perf_counter()
            starting = supervisor.start()
            elapsed = time.perf_counter() - started_at

            self.assertLess(elapsed, 0.2)
            self.assertTrue(factory_entered.wait(1))
            self.assertEqual(starting["state"], "syncing")
            self.assertEqual(starting["roles"], {"workers": [], "generator": None})
            self.assertFalse(starting["resources"]["control_dht"])

            release_factory.set()
            ready = wait_for_state(supervisor, "ready")

            self.assertEqual(ready["control_peer_id"], "control-peer")
            self.assertEqual(ready["provider_count"], 1)
            self.assertEqual(ready["nodes"], nodes)
            self.assertFalse(ready["stale"])
            self.assertEqual(ready["roles"], {"workers": [], "generator": None})
        finally:
            release_factory.set()
            supervisor.stop()

    def test_peerless_control_dht_is_verified_dead_before_identity_reuse(self) -> None:
        peerless_dht = FakeDHT(peer_id=None)
        replacement_dht = FakeDHT(peer_id="replacement-control-peer")
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "network.supervisor.hivemind.DHT",
                side_effect=[peerless_dht, replacement_dht],
            ) as dht_constructor:
                supervisor = NetworkSupervisor(
                    identity_path=Path(temp_dir) / "control-plane.key",
                    scanner=ScriptedScanner(([], [])),
                    refresh_interval=0.01,
                )
                try:
                    supervisor.start()
                    ready = wait_for_state(supervisor, "ready")

                    self.assertEqual(dht_constructor.call_count, 2)
                    self.assertEqual(peerless_dht.shutdown_count, 1)
                    self.assertFalse(peerless_dht.is_alive())
                    self.assertEqual(
                        ready["control_peer_id"],
                        "replacement-control-peer",
                    )
                finally:
                    supervisor.stop()

    def test_failed_peerless_dht_cleanup_prevents_identity_reuse(self) -> None:
        class PeerlessLingeringDHT(FakeDHT):
            def shutdown(self) -> None:
                with self._lock:
                    self.shutdown_count += 1

        peerless_dht = PeerlessLingeringDHT(peer_id=None)
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch(
                "network.supervisor.hivemind.DHT",
                return_value=peerless_dht,
            ) as dht_constructor:
                supervisor = NetworkSupervisor(
                    identity_path=Path(temp_dir) / "control-plane.key",
                    scanner=ScriptedScanner(([], [])),
                    refresh_interval=0.01,
                )
                supervisor.start()
                wait_for_state(supervisor, "degraded")
                deadline = time.monotonic() + 1
                while (
                    time.monotonic() < deadline
                    and not supervisor.snapshot()["resources"][
                        "control_dht_shutdown_failed"
                    ]
                ):
                    time.sleep(0.01)

                time.sleep(0.03)
                snapshot = supervisor.snapshot()
                self.assertEqual(dht_constructor.call_count, 1)
                self.assertEqual(peerless_dht.shutdown_count, 1)
                self.assertTrue(peerless_dht.is_alive())
                self.assertTrue(
                    snapshot["resources"]["control_dht_shutdown_failed"]
                )
                stopped = supervisor.stop(timeout=1)
                self.assertEqual(stopped["status"], "timeout")

    def test_refresh_failure_retains_last_good_topology_and_revision(self) -> None:
        fake_dht = FakeDHT()
        nodes = [
            {
                "peer_id": "worker-a",
                "model_name": "facebook/opt-125m",
                "layer_start": 0,
                "layer_end": 12,
                "running": True,
            }
        ]
        scanner = ScriptedScanner(
            (nodes, [{"code": "ignored-malformed-record"}]),
            TimeoutError("DHT lookup timed out"),
        )
        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=scanner,
            refresh_interval=60,
        )
        try:
            supervisor.start()
            ready = wait_for_state(supervisor, "ready")
            topology_revision = ready["topology_revision"]
            captured_at = ready["snapshot_captured_at"]

            with self.assertRaisesRegex(TimeoutError, "DHT lookup timed out"):
                supervisor.refresh_now()

            degraded = supervisor.snapshot()
            self.assertEqual(degraded["state"], "degraded")
            self.assertTrue(degraded["stale"])
            self.assertEqual(degraded["nodes"], nodes)
            self.assertEqual(degraded["provider_count"], 1)
            self.assertEqual(degraded["topology_revision"], topology_revision)
            self.assertEqual(degraded["snapshot_captured_at"], captured_at)
            self.assertEqual(
                degraded["validation_errors"],
                [{"code": "ignored-malformed-record"}],
            )
            self.assertEqual(degraded["failure"]["stage"], "topology_lookup")
            self.assertEqual(degraded["failure"]["code"], "TimeoutError")
        finally:
            supervisor.stop()

    def test_ambiguous_empty_scan_retains_recent_provider_until_lease_horizon(self) -> None:
        nodes = [
            {
                "peer_id": "worker-a",
                "node_id": "node-a",
                "model_name": "facebook/opt-125m",
                "layer_start": 0,
                "layer_end": 12,
                "running": True,
            }
        ]
        scanner = ScriptedScanner((nodes, []), ([], []), ([], []))
        supervisor = NetworkSupervisor(
            dht_factory=FakeDHT,
            scanner=scanner,
            refresh_interval=60,
            disappearance_grace=90,
        )
        try:
            supervisor.start()
            ready = wait_for_state(supervisor, "ready")

            degraded = supervisor.refresh_now()

            self.assertEqual(degraded["state"], "degraded")
            self.assertEqual(degraded["nodes"], nodes)
            self.assertEqual(degraded["retained_provider_count"], 1)
            self.assertEqual(
                degraded["topology_revision"],
                ready["topology_revision"],
            )
            self.assertEqual(
                degraded["snapshot_captured_at"],
                ready["snapshot_captured_at"],
            )
            self.assertEqual(
                degraded["failure"]["stage"],
                "topology_visibility",
            )

            with supervisor._lock:
                supervisor._node_last_seen_at = {
                    identity: 0.0
                    for identity in supervisor._node_last_seen_at
                }
            expired = supervisor.refresh_now()

            self.assertEqual(expired["state"], "ready")
            self.assertEqual(expired["nodes"], [])
            self.assertEqual(expired["retained_provider_count"], 0)
        finally:
            supervisor.stop()

    def test_ambiguous_partial_scan_never_builds_a_hybrid_topology(self) -> None:
        first_nodes = [
            {
                "peer_id": "worker-a",
                "node_id": "node-a",
                "model_name": "facebook/opt-125m",
                "layer_start": 0,
                "layer_end": 6,
                "running": True,
            }
        ]
        later_nodes = [
            {
                "peer_id": "worker-b",
                "node_id": "node-b",
                "model_name": "facebook/opt-125m",
                "layer_start": 6,
                "layer_end": 12,
                "running": True,
            }
        ]
        supervisor = NetworkSupervisor(
            dht_factory=FakeDHT,
            scanner=ScriptedScanner(
                (first_nodes, []),
                (later_nodes, []),
                (later_nodes, []),
            ),
            refresh_interval=60,
            disappearance_grace=90,
        )
        try:
            supervisor.start()
            ready = wait_for_state(supervisor, "ready")
            time.sleep(0.01)

            degraded = supervisor.refresh_now()

            self.assertEqual(degraded["state"], "degraded")
            self.assertEqual(degraded["nodes"], first_nodes)
            self.assertNotIn(later_nodes[0], degraded["nodes"])
            self.assertEqual(
                degraded["topology_revision"],
                ready["topology_revision"],
            )
            self.assertEqual(
                degraded["snapshot_captured_at"],
                ready["snapshot_captured_at"],
            )
            self.assertEqual(
                degraded["last_refresh_success_at"],
                ready["last_refresh_success_at"],
            )
            self.assertGreater(
                degraded["snapshot_age_seconds"],
                ready["snapshot_age_seconds"],
            )

            with supervisor._lock:
                first_identity = next(
                    identity
                    for identity in supervisor._node_last_seen_at
                    if identity.startswith("worker-a|")
                )
                supervisor._node_last_seen_at[first_identity] = 0.0
            recovered = supervisor.refresh_now()

            self.assertEqual(recovered["state"], "ready")
            self.assertEqual(recovered["nodes"], later_nodes)
            self.assertNotEqual(
                recovered["topology_revision"],
                ready["topology_revision"],
            )
        finally:
            supervisor.stop()

    def test_role_registry_preserves_distinct_control_worker_and_generator_peers(self) -> None:
        fake_dht = FakeDHT(peer_id="control-peer")
        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        try:
            supervisor.start()
            wait_for_state(supervisor, "ready")
            supervisor.register_role(
                "worker",
                role_id="node-one",
                peer_id="worker-peer-one",
                state="ready",
            )
            supervisor.register_role(
                "worker",
                role_id="node-two",
                peer_id="worker-peer-two",
                state="ready",
            )
            supervisor.register_role(
                "generator",
                peer_id="generator-peer",
                state="ready",
            )

            snapshot = supervisor.snapshot()
            worker_peers = {
                worker["peer_id"] for worker in snapshot["roles"]["workers"]
            }
            all_role_peers = worker_peers | {
                snapshot["roles"]["generator"]["peer_id"],
                snapshot["control_peer_id"],
            }

            self.assertEqual(
                worker_peers,
                {"worker-peer-one", "worker-peer-two"},
            )
            self.assertEqual(len(all_role_peers), 4)
            self.assertEqual(snapshot["resources"]["publication_tasks"], 2)
            self.assertEqual(snapshot["resources"]["health_monitors"], 1)
        finally:
            supervisor.stop()

    def test_register_role_is_idempotent_for_an_unchanged_runtime(self) -> None:
        supervisor = NetworkSupervisor(
            dht_factory=lambda: FakeDHT(peer_id="control-peer"),
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )

        supervisor.register_role(
            "generator",
            peer_id="generator-peer",
            state="ready",
        )
        first = supervisor.snapshot()
        supervisor.register_role(
            "generator",
            peer_id="generator-peer",
            state="ready",
        )
        unchanged = supervisor.snapshot()
        supervisor.register_role(
            "generator",
            peer_id="generator-peer",
            state="suspended",
        )
        changed = supervisor.snapshot()

        self.assertEqual(unchanged["revision"], first["revision"])
        self.assertEqual(unchanged["updated_at"], first["updated_at"])
        self.assertEqual(
            unchanged["roles"]["generator"]["updated_at"],
            first["roles"]["generator"]["updated_at"],
        )
        self.assertEqual(changed["revision"], first["revision"] + 1)
        self.assertEqual(changed["roles"]["generator"]["state"], "suspended")

        supervisor.register_role(
            "worker",
            role_id="node-one",
            peer_id="worker-peer",
            state="ready",
        )
        worker_first = supervisor.snapshot()
        supervisor.register_role(
            "worker",
            role_id="node-one",
            peer_id="worker-peer",
            state="ready",
        )
        worker_unchanged = supervisor.snapshot()

        self.assertEqual(worker_unchanged["revision"], worker_first["revision"])
        self.assertEqual(
            worker_unchanged["roles"]["workers"][0]["updated_at"],
            worker_first["roles"]["workers"][0]["updated_at"],
        )

    def test_role_registry_rejects_identity_reuse_across_runtime_roles(self) -> None:
        supervisor = NetworkSupervisor(
            dht_factory=lambda: FakeDHT(peer_id="control-peer"),
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        try:
            supervisor.start()
            wait_for_state(supervisor, "ready")
            supervisor.register_role(
                "worker",
                role_id="node-one",
                peer_id="worker-peer",
                state="ready",
            )

            with self.assertRaisesRegex(ValueError, "control-plane"):
                supervisor.register_role(
                    "generator",
                    peer_id="control-peer",
                    state="starting",
                )
            with self.assertRaisesRegex(ValueError, "worker:node-one"):
                supervisor.register_role(
                    "generator",
                    peer_id="worker-peer",
                    state="starting",
                )
        finally:
            supervisor.stop()

    def test_active_role_identity_cannot_rotate_without_exact_stop(self) -> None:
        supervisor = NetworkSupervisor(
            dht_factory=lambda: FakeDHT(peer_id="control-peer"),
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        supervisor.register_role(
            "worker",
            role_id="node-one",
            peer_id="worker-peer-one",
            state="ready",
        )
        supervisor.register_role(
            "generator",
            peer_id="generator-peer-one",
            state="ready",
        )

        with self.assertRaisesRegex(ValueError, "cannot rotate"):
            supervisor.register_role(
                "worker",
                role_id="node-one",
                peer_id="worker-peer-two",
                state="ready",
            )
        with self.assertRaisesRegex(ValueError, "cannot rotate"):
            supervisor.register_role(
                "generator",
                peer_id="generator-peer-two",
                state="starting",
            )

        unchanged = supervisor.snapshot()["roles"]
        self.assertEqual(
            unchanged["workers"][0]["peer_id"],
            "worker-peer-one",
        )
        self.assertEqual(
            unchanged["generator"]["peer_id"],
            "generator-peer-one",
        )

        supervisor.register_role(
            "worker",
            role_id="node-one",
            peer_id="worker-peer-one",
            state="stopped",
        )
        supervisor.register_role(
            "worker",
            role_id="node-one",
            peer_id="worker-peer-two",
            state="starting",
        )
        supervisor.unregister_role("generator")
        supervisor.register_role(
            "generator",
            peer_id="generator-peer-two",
            state="starting",
        )

        replaced = supervisor.snapshot()["roles"]
        self.assertEqual(
            replaced["workers"][0]["peer_id"],
            "worker-peer-two",
        )
        self.assertEqual(
            replaced["generator"]["peer_id"],
            "generator-peer-two",
        )

    def test_false_publication_is_classified_from_independent_subkey_readback(self) -> None:
        expected = {"peer_id": "worker-a", "node_id": "node-a", "timestamp": 1.0}

        class CompletedFuture:
            def result(self, timeout: float):
                return ValueWithExpiration(
                    {
                        "worker-a": ValueWithExpiration(
                            {**expected, "timestamp": 2.0},
                            200.0,
                        )
                    },
                    200.0,
                )

        class ReadbackDHT(FakeDHT):
            def get(self, key, latest, return_future):
                self.get_call = (key, latest, return_future)
                return CompletedFuture()

        dht = ReadbackDHT()
        supervisor = NetworkSupervisor(
            dht_factory=lambda: dht,
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        with supervisor._lock:
            supervisor._dht = dht

        outcome = supervisor.verify_publication(
            key="distribllm.members.v2",
            subkey="worker-a",
            store_returned=False,
            expected_value=expected,
            attempted_expiration=180.0,
            minimum_safe_expiration=150.0,
            equivalent=lambda left, right: (
                left["peer_id"] == right["peer_id"]
                and left["node_id"] == right["node_id"]
            ),
        )

        self.assertEqual(outcome.disposition.value, "superseded_equivalent")
        self.assertTrue(outcome.publication_safe)
        self.assertFalse(outcome.permits_transport_recovery)
        self.assertEqual(
            dht.get_call,
            ("distribllm.members.v2", True, True),
        )

    def test_publication_timeout_retains_exact_dht_until_future_quiesces(self) -> None:
        release_result = threading.Event()

        class TimedOutFuture:
            cancel_called = False

            def result(self, timeout=None):
                if timeout is not None:
                    raise FutureTimeoutError()
                if not release_result.wait(1):
                    raise TimeoutError("test did not release DHT future")
                return ValueWithExpiration(
                    {"peer_id": "worker-a"},
                    200.0,
                )

            def cancel(self) -> None:
                self.cancel_called = True

            def cancelled(self) -> bool:
                return self.cancel_called

            def done(self) -> bool:
                return release_result.is_set()

        future = TimedOutFuture()

        class ReadbackDHT(FakeDHT):
            def get(self, key, latest, return_future):
                return future

        dht = ReadbackDHT()
        supervisor = NetworkSupervisor(
            dht_factory=lambda: dht,
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        with supervisor._lock:
            supervisor._dht = dht

        outcome = supervisor.verify_publication(
            key="distribllm.node_info.worker-a",
            subkey=None,
            store_returned=False,
            expected_value={"peer_id": "worker-a"},
            attempted_expiration=180.0,
            minimum_safe_expiration=150.0,
            equivalent=lambda left, right: left == right,
        )

        self.assertEqual(outcome.disposition.value, "unverified")
        self.assertIn("verification_TimeoutError", outcome.reason)
        self.assertFalse(future.cancel_called)
        self.assertFalse(outcome.permits_transport_recovery)
        retained = supervisor.snapshot()
        self.assertTrue(
            retained["resources"]["control_dht_operation_pending"]
        )
        refresh_errors = []

        def refresh() -> None:
            try:
                supervisor.refresh_now()
            except BaseException as exc:
                refresh_errors.append(exc)

        refresh_thread = threading.Thread(target=refresh, daemon=True)
        refresh_thread.start()
        time.sleep(0.01)
        self.assertTrue(refresh_thread.is_alive())
        self.assertEqual(supervisor._scanner.calls, 0)

        first_stop = supervisor.stop(timeout=0.01)

        self.assertEqual(first_stop["status"], "timeout")
        self.assertTrue(first_stop["dht_operation_pending"])
        self.assertEqual(dht.shutdown_count, 0)
        with self.assertRaisesRegex(RuntimeError, "operation still owns"):
            supervisor.start()

        release_result.set()
        refresh_thread.join(1)
        self.assertFalse(refresh_thread.is_alive())
        self.assertEqual(len(refresh_errors), 1)
        self.assertIn("shutting down", str(refresh_errors[0]))
        self.assertEqual(supervisor._scanner.calls, 0)
        deadline = time.monotonic() + 1
        while (
            time.monotonic() < deadline
            and supervisor.snapshot()["resources"][
                "control_dht_operation_pending"
            ]
        ):
            time.sleep(0.01)

        final_stop = supervisor.stop(timeout=1)
        self.assertEqual(final_stop["status"], "stopped")
        self.assertEqual(dht.shutdown_count, 1)

    def test_finalizer_waits_for_publication_readback_before_dht_shutdown(self) -> None:
        readback_entered = threading.Event()
        release_readback = threading.Event()

        class BlockingFuture:
            cancelled = False

            def result(self, timeout: float):
                readback_entered.set()
                if not release_readback.wait(1):
                    raise TimeoutError("test did not release publication readback")
                return ValueWithExpiration(
                    {"peer_id": "worker-a"},
                    200.0,
                )

            def cancel(self) -> None:
                self.cancelled = True

        future = BlockingFuture()

        class ReadbackDHT(FakeDHT):
            def get(self, key, latest, return_future):
                return future

        fake_dht = ReadbackDHT()
        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        outcomes = []
        errors = []

        def verify() -> None:
            try:
                outcomes.append(
                    supervisor.verify_publication(
                        key="distribllm.node_info.worker-a",
                        subkey=None,
                        store_returned=False,
                        expected_value={"peer_id": "worker-a"},
                        attempted_expiration=180.0,
                        minimum_safe_expiration=150.0,
                        equivalent=lambda left, right: left == right,
                    )
                )
            except BaseException as exc:
                errors.append(exc)

        publication_thread = threading.Thread(target=verify, daemon=True)
        supervisor.start()
        wait_for_state(supervisor, "ready")
        publication_thread.start()

        try:
            self.assertTrue(readback_entered.wait(1))
            first = supervisor.stop(timeout=0.01)

            self.assertEqual(first["status"], "timeout")
            self.assertFalse(first["thread_stopped"])
            self.assertFalse(first["dht_stopped"])
            self.assertEqual(fake_dht.shutdown_count, 0)
        finally:
            release_readback.set()
            publication_thread.join(1)
            final = supervisor.stop(timeout=1)

        self.assertFalse(publication_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].publication_safe)
        self.assertEqual(final["status"], "stopped")
        self.assertEqual(fake_dht.shutdown_count, 1)

    def test_cancelled_publication_future_quarantines_dht_identity(self) -> None:
        class CancelledFuture:
            def result(self, timeout=None):
                del timeout
                raise CancelledError()

            def cancelled(self) -> bool:
                return True

            def done(self) -> bool:
                return True

        class ReadbackDHT(FakeDHT):
            def get(self, key, latest, return_future):
                del key, latest, return_future
                return CancelledFuture()

        dht = ReadbackDHT()
        supervisor = NetworkSupervisor(
            dht_factory=lambda: dht,
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        with supervisor._lock:
            supervisor._dht = dht

        outcome = supervisor.verify_publication(
            key="distribllm.node_info.worker-a",
            subkey=None,
            store_returned=False,
            expected_value={"peer_id": "worker-a"},
            attempted_expiration=180.0,
            minimum_safe_expiration=150.0,
            equivalent=lambda left, right: left == right,
        )

        with supervisor._lock:
            retained_operation = supervisor._dht_operation
        self.assertIsNotNone(retained_operation)
        self.assertTrue(retained_operation.watcher_finished.wait(1))
        retained = supervisor.snapshot()
        self.assertEqual(outcome.disposition.value, "unverified")
        self.assertTrue(
            retained["resources"]["control_dht_operation_pending"]
        )
        self.assertTrue(
            retained["resources"]["control_dht_operation_uncertain"]
        )
        stopped = supervisor.stop(timeout=0.01)
        self.assertEqual(stopped["status"], "timeout")
        self.assertEqual(dht.shutdown_count, 0)

    def test_timed_out_stop_does_not_close_dht_under_an_active_scan(self) -> None:
        scan_entered = threading.Event()
        release_scan = threading.Event()
        fake_dht = FakeDHT()

        def blocking_scanner(dht, dht_prefix):
            scan_entered.set()
            if not release_scan.wait(1):
                raise TimeoutError("test did not release topology scan")
            return [], []

        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=blocking_scanner,
            refresh_interval=60,
        )
        supervisor.start()
        self.assertTrue(scan_entered.wait(1))

        first = supervisor.stop(timeout=0.01)

        self.assertEqual(first["status"], "timeout")
        self.assertFalse(first["thread_stopped"])
        self.assertFalse(first["dht_stopped"])
        self.assertEqual(fake_dht.shutdown_count, 0)

        release_scan.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and fake_dht.shutdown_count == 0:
            time.sleep(0.01)

        self.assertEqual(fake_dht.shutdown_count, 1)
        second = supervisor.stop(timeout=1)
        self.assertEqual(second["status"], "stopped")
        self.assertEqual(fake_dht.shutdown_count, 1)
        self.assertEqual(supervisor.snapshot()["state"], "disconnected")

    def test_scan_result_is_discarded_when_shutdown_wins_commit_race(self) -> None:
        scan_entered = threading.Event()
        release_scan = threading.Event()
        fake_dht = FakeDHT()
        scanned_nodes = [
            {
                "peer_id": "worker-after-shutdown",
                "node_id": "node-after-shutdown",
                "model_name": "facebook/opt-125m",
                "layer_start": 0,
                "layer_end": 12,
                "running": True,
            }
        ]

        def blocking_scanner(dht, dht_prefix):
            del dht, dht_prefix
            scan_entered.set()
            if not release_scan.wait(1):
                raise TimeoutError("test did not release topology scan")
            return scanned_nodes, []

        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=blocking_scanner,
            refresh_interval=60,
        )
        supervisor.start()
        self.assertTrue(scan_entered.wait(1))

        supervisor.begin_shutdown()
        release_scan.set()
        stopped = supervisor.stop(timeout=1)
        snapshot = supervisor.snapshot()

        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(fake_dht.shutdown_count, 1)
        self.assertEqual(snapshot["state"], "disconnected")
        self.assertEqual(snapshot["nodes"], [])
        self.assertIsNone(snapshot["topology_revision"])
        self.assertIsNone(snapshot["snapshot_captured_at"])
        self.assertIsNone(snapshot["last_refresh_success_at"])

    def test_finalizer_waits_for_an_external_refresh_before_dht_shutdown(self) -> None:
        external_scan_entered = threading.Event()
        release_external_scan = threading.Event()
        scan_lock = threading.Lock()
        scan_count = 0
        fake_dht = FakeDHT()

        def scanner(dht, dht_prefix):
            nonlocal scan_count
            with scan_lock:
                scan_count += 1
                current_scan = scan_count
            if current_scan == 1:
                return [], []
            external_scan_entered.set()
            if not release_external_scan.wait(1):
                raise TimeoutError("test did not release external topology scan")
            return [], []

        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=scanner,
            refresh_interval=60,
        )
        supervisor.start()
        wait_for_state(supervisor, "ready")
        refresh_errors = []

        def refresh() -> None:
            try:
                supervisor.refresh_now()
            except BaseException as exc:
                refresh_errors.append(exc)

        external_refresh = threading.Thread(target=refresh)
        external_refresh.start()
        self.assertTrue(external_scan_entered.wait(1))

        first = supervisor.stop(timeout=0.01)

        self.assertEqual(first["status"], "timeout")
        self.assertFalse(first["thread_stopped"])
        self.assertFalse(first["dht_stopped"])
        self.assertEqual(fake_dht.shutdown_count, 0)

        release_external_scan.set()
        external_refresh.join(1)
        self.assertFalse(external_refresh.is_alive())
        self.assertEqual(len(refresh_errors), 1)
        self.assertIn("shutdown started", str(refresh_errors[0]))
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline and fake_dht.shutdown_count == 0:
            time.sleep(0.01)

        self.assertEqual(fake_dht.shutdown_count, 1)
        second = supervisor.stop(timeout=1)
        self.assertEqual(second["status"], "stopped")
        self.assertEqual(fake_dht.shutdown_count, 1)

    def test_stop_is_idempotent_and_shuts_down_control_dht_exactly_once(self) -> None:
        fake_dht = FakeDHT()
        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        supervisor.start()
        wait_for_state(supervisor, "ready")

        first = supervisor.stop()
        second = supervisor.stop()
        stopped = supervisor.snapshot()

        self.assertEqual(first["status"], "stopped")
        self.assertEqual(second["status"], "stopped")
        self.assertEqual(fake_dht.shutdown_count, 1)
        self.assertEqual(stopped["state"], "disconnected")
        self.assertFalse(stopped["resources"]["control_dht"])
        self.assertFalse(stopped["resources"]["discovery_task"])
        self.assertFalse(stopped["accepting_roles"])

    def test_timed_out_dht_shutdown_is_retained_until_a_later_stop(self) -> None:
        fake_dht = BlockingShutdownDHT()
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return fake_dht

        supervisor = NetworkSupervisor(
            dht_factory=factory,
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        supervisor.start()
        wait_for_state(supervisor, "ready")

        first = supervisor.stop(timeout=0.05)

        self.assertTrue(fake_dht.shutdown_entered.is_set())
        self.assertEqual(first["status"], "timeout")
        self.assertTrue(first["thread_stopped"])
        self.assertFalse(first["dht_stopped"])
        self.assertTrue(first["dht_shutdown_pending"])
        self.assertEqual(fake_dht.shutdown_count, 1)
        self.assertTrue(
            supervisor.snapshot()["resources"]["control_dht_shutdown_pending"]
        )
        with self.assertRaisesRegex(RuntimeError, "shutdown is still in progress"):
            supervisor.start()
        self.assertEqual(factory_calls, 1)

        fake_dht.release_shutdown.set()
        second = supervisor.stop(timeout=1)
        third = supervisor.stop(timeout=1)

        self.assertEqual(second["status"], "stopped")
        self.assertEqual(third["status"], "stopped")
        self.assertEqual(fake_dht.shutdown_count, 1)
        self.assertFalse(supervisor._dht_shutdown)
        self.assertFalse(
            supervisor.snapshot()["resources"]["control_dht_shutdown_pending"]
        )

    def test_failed_dht_shutdown_quarantines_the_stable_identity(self) -> None:
        class FailingShutdownDHT(FakeDHT):
            def shutdown(self) -> None:
                with self._lock:
                    self.shutdown_count += 1
                raise RuntimeError("shutdown failed")

        fake_dht = FailingShutdownDHT()
        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        supervisor.start()
        wait_for_state(supervisor, "ready")

        first = supervisor.stop(timeout=1)
        second = supervisor.stop(timeout=1)

        self.assertEqual(first["status"], "timeout")
        self.assertEqual(second["status"], "timeout")
        self.assertFalse(first["dht_stopped"])
        self.assertFalse(first["dht_shutdown_pending"])
        self.assertEqual(fake_dht.shutdown_count, 1)
        self.assertTrue(
            supervisor.snapshot()["resources"]["control_dht_shutdown_failed"]
        )
        with self.assertRaisesRegex(RuntimeError, "stable peer identity"):
            supervisor.start()

    def test_shutdown_return_without_process_death_quarantines_identity(self) -> None:
        class LingeringDHT(FakeDHT):
            def shutdown(self) -> None:
                with self._lock:
                    self.shutdown_count += 1

        fake_dht = LingeringDHT()
        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=ScriptedScanner(([], [])),
            refresh_interval=60,
        )
        supervisor.start()
        wait_for_state(supervisor, "ready")

        stopped = supervisor.stop(timeout=1)

        self.assertEqual(stopped["status"], "timeout")
        self.assertFalse(stopped["dht_stopped"])
        self.assertEqual(fake_dht.shutdown_count, 1)
        self.assertTrue(
            supervisor.snapshot()["resources"]["control_dht_shutdown_failed"]
        )
        with self.assertRaisesRegex(RuntimeError, "stable peer identity"):
            supervisor.start()

    def test_first_refresh_failure_is_stale_and_not_authoritative_empty_topology(self) -> None:
        fake_dht = FakeDHT()
        supervisor = NetworkSupervisor(
            dht_factory=lambda: fake_dht,
            scanner=ScriptedScanner(ConnectionError("bootstrap unavailable")),
            refresh_interval=60,
        )
        try:
            supervisor.start()
            degraded = wait_for_state(supervisor, "degraded")

            self.assertTrue(degraded["stale"])
            self.assertIsNone(degraded["topology_revision"])
            self.assertIsNone(degraded["snapshot_captured_at"])
            self.assertIsNone(degraded["last_refresh_success_at"])
            self.assertEqual(degraded["nodes"], [])
            self.assertEqual(degraded["provider_count"], 0)
            self.assertEqual(degraded["failure"]["stage"], "topology_lookup")
            self.assertEqual(degraded["failure"]["code"], "ConnectionError")
        finally:
            supervisor.stop()


if __name__ == "__main__":
    unittest.main()
