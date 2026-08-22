import asyncio
import copy
import gc
import os
import sys
import tempfile
import threading
import unittest
import weakref
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from api import server as api_server
from api.lifecycle_jobs import LifecycleJobStore
from api.runtime_state import RuntimeStateStore
from constants import get_role_identity_path


MODEL_ID = "facebook/opt-125m"


class ControlledExecutorLoop:
    """Keep one selected executor callback pending until the test releases it."""

    def __init__(self, loop: asyncio.AbstractEventLoop, should_block) -> None:
        self.loop = loop
        self.should_block = should_block
        self.blocked_started = threading.Event()
        self.blocked_future: asyncio.Future | None = None
        self.blocked_call = None

    def run_in_executor(self, _executor, function, *args):
        future = self.loop.create_future()
        if self.blocked_future is None and self.should_block(function):
            self.blocked_future = future
            self.blocked_call = lambda: function(*args)
            self.blocked_started.set()
            return future
        try:
            result = function(*args)
        except BaseException as exc:
            future.set_exception(exc)
        else:
            future.set_result(result)
        return future

    def complete_blocked(self) -> None:
        if self.blocked_future is None or self.blocked_call is None:
            raise AssertionError("No executor callback is waiting")
        try:
            result = self.blocked_call()
        except BaseException as exc:
            self.blocked_future.set_exception(exc)
        else:
            self.blocked_future.set_result(result)


def serving_node(start: int, end: int, peer_id: str) -> dict:
    return {
        "node_id": f"node-{peer_id}",
        "peer_id": peer_id,
        "model_name": MODEL_ID,
        "layer_start": start,
        "layer_end": end,
        "rpc_uid": f"distribllm.0.{start}.{end}",
        "rpc_uid_schema_version": 1,
        "rpc_peer_id": peer_id,
        "device": "cpu",
        "maddrs": [],
        "running": True,
        "layers_loaded": True,
        "rpc_running": True,
    }


class FakeSupervisor:
    def __init__(
        self,
        snapshot: dict,
        *,
        accepting_roles: bool = True,
        dht_prefix: str = "distribllm",
    ) -> None:
        self._snapshot = copy.deepcopy(snapshot)
        self.accepting_roles = accepting_roles
        self.dht_prefix = dht_prefix
        self.snapshot_calls = 0
        self.refresh_requests = 0
        self.role_updates: list[tuple[str, str | None, str, str | None]] = []

    def snapshot(self) -> dict:
        self.snapshot_calls += 1
        return copy.deepcopy(self._snapshot)

    def nodes_for_model(self, model_name: str | None = None) -> list[dict]:
        nodes = copy.deepcopy(self._snapshot.get("nodes", []))
        if model_name is None:
            return nodes
        return [node for node in nodes if node.get("model_name") == model_name]

    def request_refresh(self) -> None:
        self.refresh_requests += 1

    def register_role(
        self,
        role: str,
        *,
        peer_id: str | None,
        state: str,
        role_id: str | None = None,
    ) -> None:
        self.role_updates.append((role, peer_id, state, role_id))

    def unregister_role(self, role: str, *, role_id: str | None = None) -> None:
        self.role_updates.append((role, None, "unregistered", role_id))

    def verify_publication(self, **_kwargs):
        raise AssertionError("Mock nodes must not perform real publication")


def network_snapshot(
    *,
    state: str,
    nodes: list[dict],
    failure: dict | None = None,
    age_seconds: float = 0.0,
) -> dict:
    return {
        "schema_version": 1,
        "state": state,
        "revision": 17,
        "topology_revision": "topology-last-good",
        "control_peer_id": "control-peer",
        "snapshot_captured_at": "2026-08-23T01:02:03+00:00",
        "snapshot_age_seconds": age_seconds,
        "failure": copy.deepcopy(failure),
        "nodes": copy.deepcopy(nodes),
        "roles": {"workers": [], "generator": None},
        "resources": {
            "control_dht": True,
            "discovery_task": True,
            "publication_tasks": 0,
            "health_monitors": 0,
        },
    }


class SupervisorBackedApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_supervisor = api_server.network_supervisor
        self.original_local_node_infos = api_server._local_node_infos
        self.original_remote_sequential = api_server.RemoteSequential
        self.original_node_class = api_server.Node
        self.original_generator_class = api_server.DistributedGenerator
        self.original_dht_class = api_server.hivemind.DHT
        self.original_get_local_model_path = api_server.get_local_model_path
        self.original_generator = api_server.generator
        self.original_client_dht = api_server.client_dht
        self.original_client_dht_prefix = api_server.client_dht_prefix
        self.original_node = api_server.node
        self.original_local_nodes = dict(api_server.local_nodes)
        self.original_active_node_starts = dict(api_server._active_node_starts)
        self.original_active_node_operations = dict(
            api_server._active_node_operations
        )
        self.original_local_node_shutdown_attempts = dict(
            api_server._local_node_shutdown_attempts
        )
        self.original_runtime_state = api_server._runtime_state
        self.original_lifecycle_jobs = api_server._lifecycle_jobs
        self.original_generator_start_lock = api_server._generator_start_lock
        self.original_generator_lifecycle_lock = api_server._generator_lifecycle_lock
        with self.original_generator_lifecycle_lock:
            self.original_backend_closing = api_server._backend_closing
            self.original_generator_cleanup = api_server._generator_cleanup_in_progress
            self.original_generator_quarantine = api_server._generator_identity_quarantine
            self.original_active_generator_start = api_server._active_generator_start
            self.original_pending_generator_cleanup = (
                api_server._pending_generator_cleanup
            )
            self.original_generator_dht_shutdown_attempts = dict(
                api_server._generator_dht_shutdown_attempts
            )
        with api_server._serving_plan_cache_lock:
            self.original_serving_plan_cache = dict(api_server._serving_plan_cache)
            api_server._serving_plan_cache.clear()
        api_server._generator_start_lock = threading.Lock()
        api_server._generator_lifecycle_lock = threading.RLock()
        api_server._backend_closing = False
        api_server._generator_cleanup_in_progress = False
        api_server._generator_identity_quarantine = None
        api_server._active_generator_start = None
        api_server._pending_generator_cleanup = None
        api_server._generator_dht_shutdown_attempts.clear()
        api_server.generator = None
        api_server.client_dht = None
        api_server.node = None
        api_server.local_nodes.clear()
        api_server._active_node_starts.clear()
        api_server._active_node_operations.clear()
        api_server._local_node_shutdown_attempts.clear()
        api_server._runtime_state = RuntimeStateStore()

    def tearDown(self) -> None:
        active_start = api_server._active_generator_start
        if active_start is not None:
            active_start.cancel_event.set()
            active_start.done_event.set()
        for active_node_start in api_server._active_node_starts.values():
            active_node_start.cancel_event.set()
            active_node_start.done_event.set()
        for active_node_operation in api_server._active_node_operations.values():
            active_node_operation.done_event.set()
        if api_server._generator_start_lock.locked():
            api_server._generator_start_lock.release()
        api_server.network_supervisor = self.original_supervisor
        api_server._local_node_infos = self.original_local_node_infos
        api_server.RemoteSequential = self.original_remote_sequential
        api_server.Node = self.original_node_class
        api_server.DistributedGenerator = self.original_generator_class
        api_server.hivemind.DHT = self.original_dht_class
        api_server.get_local_model_path = self.original_get_local_model_path
        api_server.generator = self.original_generator
        api_server.client_dht = self.original_client_dht
        api_server.client_dht_prefix = self.original_client_dht_prefix
        api_server.node = self.original_node
        api_server.local_nodes.clear()
        api_server.local_nodes.update(self.original_local_nodes)
        api_server._active_node_starts.clear()
        api_server._active_node_starts.update(self.original_active_node_starts)
        api_server._active_node_operations.clear()
        api_server._active_node_operations.update(
            self.original_active_node_operations
        )
        api_server._local_node_shutdown_attempts.clear()
        api_server._local_node_shutdown_attempts.update(
            self.original_local_node_shutdown_attempts
        )
        api_server._runtime_state = self.original_runtime_state
        api_server._lifecycle_jobs = self.original_lifecycle_jobs
        api_server._backend_closing = self.original_backend_closing
        api_server._generator_cleanup_in_progress = self.original_generator_cleanup
        api_server._generator_identity_quarantine = self.original_generator_quarantine
        api_server._active_generator_start = self.original_active_generator_start
        api_server._pending_generator_cleanup = self.original_pending_generator_cleanup
        api_server._generator_dht_shutdown_attempts.clear()
        api_server._generator_dht_shutdown_attempts.update(
            self.original_generator_dht_shutdown_attempts
        )
        api_server._generator_start_lock = self.original_generator_start_lock
        api_server._generator_lifecycle_lock = self.original_generator_lifecycle_lock
        with api_server._serving_plan_cache_lock:
            api_server._serving_plan_cache.clear()
            api_server._serving_plan_cache.update(self.original_serving_plan_cache)

    def test_nodes_passively_returns_last_good_topology_while_degraded(self) -> None:
        failure = {
            "stage": "topology_lookup",
            "code": "TimeoutError",
            "message": "refresh timed out",
            "retryable": True,
        }
        supervisor = FakeSupervisor(
            network_snapshot(
                state="degraded",
                nodes=[serving_node(0, 6, "remote-head")],
                failure=failure,
                age_seconds=13.25,
            )
        )
        api_server.network_supervisor = supervisor
        api_server._local_node_infos = lambda: [serving_node(6, 12, "local-tail")]

        class UnexpectedSequential:
            def __init__(self, *args, **kwargs) -> None:
                raise AssertionError("The nodes endpoint must not own DHT discovery")

        api_server.RemoteSequential = UnexpectedSequential

        result = asyncio.run(api_server.get_nodes())

        self.assertEqual(
            [node["peer_id"] for node in result["nodes"]],
            ["remote-head", "local-tail"],
        )
        self.assertTrue(result["snapshot_stale"])
        self.assertFalse(result["refreshing"])
        self.assertEqual(result["snapshot_source"], "dht_cache")
        self.assertEqual(result["snapshot_age_seconds"], 13.25)
        self.assertEqual(result["network_state"], "degraded")
        self.assertEqual(result["network_revision"], 17)
        self.assertEqual(result["network_topology_revision"], "topology-last-good")
        self.assertEqual(result["network_failure"], failure)
        self.assertIn("last-good", result["warning"])
        self.assertEqual(supervisor.refresh_requests, 0)

    def test_generator_topology_merges_authoritative_local_workers(self) -> None:
        remote = serving_node(0, 6, "remote-head")
        stale_local = serving_node(6, 12, "local-tail")
        stale_local["node_id"] = "local-node"
        stale_local["running"] = False
        live_local = {**stale_local, "running": True, "rpc_running": True}
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[remote, stale_local])
        )
        api_server._local_node_infos = lambda: [live_local]

        merged = api_server._generator_topology_nodes(MODEL_ID)

        self.assertEqual(len(merged), 2)
        by_node_id = {item["node_id"]: item for item in merged}
        self.assertTrue(by_node_id["local-node"]["running"])
        self.assertEqual(by_node_id[remote["node_id"]]["peer_id"], "remote-head")

        nodes_payload = api_server._get_nodes_sync()
        nodes_by_id = {item["node_id"]: item for item in nodes_payload["nodes"]}
        self.assertTrue(nodes_by_id["local-node"]["running"])
        self.assertEqual(len(nodes_payload["nodes"]), 2)

        active = api_server._active_serving_nodes(MODEL_ID)
        self.assertEqual(
            [(item["layer_start"], item["layer_end"]) for item in active],
            [(0, 6), (6, 12)],
        )

        model_payload = api_server._get_models_sync()
        model = next(item for item in model_payload["models"] if item["id"] == MODEL_ID)
        self.assertTrue(model["runnable"])
        self.assertEqual(model["covered_layers"], 12)

    def test_network_snapshot_overlays_authoritative_local_worker_role(self) -> None:
        snapshot = network_snapshot(state="degraded", nodes=[])
        snapshot["roles"]["workers"] = [
            {
                "node_id": "local-node",
                "peer_id": "stale-peer",
                "state": "starting",
            }
        ]
        supervisor = FakeSupervisor(snapshot)
        api_server.network_supervisor = supervisor

        class LocalNode:
            node_id = "local-node"

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": "actual-peer",
                    "running": True,
                    "announcement": {"publication_state": "healthy"},
                }

            def has_pending_serving_cleanup(self) -> bool:
                return False

        local_node = LocalNode()
        api_server.local_nodes[local_node.node_id] = local_node

        result = api_server._network_snapshot()

        self.assertEqual(
            result["roles"]["workers"],
            [
                {
                    "node_id": "local-node",
                    "peer_id": "actual-peer",
                    "state": "ready",
                }
            ],
        )
        self.assertEqual(result["resources"]["registered_workers"], 1)
        self.assertEqual(supervisor.role_updates, [])

    def test_serving_plan_uses_stale_last_good_snapshot_without_refresh(self) -> None:
        failure = {
            "stage": "topology_lookup",
            "code": "P2PDaemonError",
            "message": "bootstrap unavailable",
            "retryable": True,
        }
        supervisor = FakeSupervisor(
            network_snapshot(
                state="degraded",
                nodes=[serving_node(0, 6, "remote-head")],
                failure=failure,
                age_seconds=8.5,
            )
        )
        api_server.network_supervisor = supervisor
        api_server._local_node_infos = lambda: []

        plan = asyncio.run(api_server.get_model_serving_plan(MODEL_ID, 6))

        self.assertFalse(plan["current_runnable"])
        self.assertEqual(plan["missing_ranges"], [{"start": 6, "end": 12}])
        self.assertEqual(
            (plan["recommendation"]["layer_start"], plan["recommendation"]["layer_end"]),
            (6, 12),
        )
        self.assertTrue(plan["snapshot_stale"])
        self.assertFalse(plan["refreshing"])
        self.assertEqual(plan["snapshot_source"], "dht_cache")
        self.assertEqual(plan["snapshot_age_seconds"], 8.5)
        self.assertEqual(plan["network_state"], "degraded")
        self.assertEqual(plan["network_failure"], failure)
        self.assertEqual(supervisor.refresh_requests, 0)
        self.assertNotIn((MODEL_ID, 6), api_server._serving_plan_refresh_tasks)

    def test_serving_plan_uses_one_supervisor_revision_for_nodes_and_freshness(
        self,
    ) -> None:
        supervisor = FakeSupervisor(
            network_snapshot(
                state="ready",
                nodes=[serving_node(0, 6, "remote-head")],
            )
        )
        api_server.network_supervisor = supervisor
        api_server._local_node_infos = lambda: []

        plan = asyncio.run(api_server.get_model_serving_plan(MODEL_ID, 6))

        self.assertEqual(supervisor.snapshot_calls, 1)
        self.assertEqual(plan["network_topology_revision"], "topology-last-good")
        self.assertEqual(plan["missing_ranges"], [{"start": 6, "end": 12}])
        self.assertEqual(
            (plan["recommendation"]["layer_start"], plan["recommendation"]["layer_end"]),
            (6, 12),
        )

    def test_node_start_conflict_does_not_relabel_stale_topology_as_fresh(self) -> None:
        failure = {
            "stage": "topology_visibility",
            "code": "RuntimeError",
            "message": "provider disappearance is unconfirmed",
            "retryable": True,
        }
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(
                state="degraded",
                nodes=[serving_node(0, 6, "remote-head")],
                failure=failure,
                age_seconds=12.0,
            )
        )
        api_server._local_node_infos = lambda: []
        request = api_server.NodeStartRequest(
            model_name=MODEL_ID,
            layer_start=6,
            layer_end=12,
            device="cpu",
            coverage_revision="older-client-revision",
        )

        with self.assertRaises(api_server.HTTPException) as raised:
            asyncio.run(api_server.start_node(request))

        plan = raised.exception.detail["plan"]
        self.assertEqual(raised.exception.status_code, 409)
        self.assertTrue(plan["snapshot_stale"])
        self.assertEqual(plan["snapshot_source"], "dht_cache")
        self.assertEqual(plan["network_state"], "degraded")
        self.assertEqual(plan["network_failure"], failure)

    def test_runtime_roles_reject_a_different_bootstrap_peer_set(self) -> None:
        supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[]),
        )
        supervisor.initial_peers = ["/ip4/127.0.0.1/tcp/7001/p2p/control"]
        api_server.network_supervisor = supervisor

        with self.assertRaises(api_server.HTTPException) as raised:
            api_server._role_initial_peers(
                ["/ip4/127.0.0.1/tcp/7002/p2p/other"]
            )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail["error"],
            "network_peers_mismatch",
        )

    def test_generator_start_uses_stable_identity_and_supervisor_topology(self) -> None:
        captured: dict[str, object] = {}
        supervisor = FakeSupervisor(
            network_snapshot(
                state="ready",
                nodes=[serving_node(0, 12, "worker-peer")],
            )
        )
        api_server.network_supervisor = supervisor
        api_server._local_node_infos = lambda: []

        class FakeDHT:
            peer_id = "generator-peer"

            def __init__(self, **kwargs) -> None:
                captured["dht_kwargs"] = kwargs

            def shutdown(self) -> None:
                captured["dht_shutdown"] = True

        class FakeSequential:
            def __init__(
                self,
                *,
                dht,
                dht_prefix: str,
                num_layers: int,
                model_name: str,
                topology_provider=None,
            ) -> None:
                captured["topology_provider"] = topology_provider
                self.topology_provider = topology_provider

            def start_health_monitor(self) -> None:
                captured["health_monitor_started"] = True

            def stop_health_monitor(self) -> None:
                captured["health_monitor_stopped"] = True

            def get_health_readiness(self) -> dict:
                return {
                    "route_ready": True,
                    "selected_route": [serving_node(0, 12, "worker-peer")],
                    "reasons": [],
                }

        class FakeGenerator:
            def __init__(self, **kwargs) -> None:
                self.model_name = kwargs["model_name"]
                self.sequential = kwargs["sequential"]
                self.loaded = False

            def load(self) -> None:
                self.loaded = True

            def is_loaded(self) -> bool:
                return self.loaded

            def set_startup_duration_ms(self, duration_ms: float) -> None:
                captured["startup_duration_ms"] = duration_ms

            def get_performance_snapshot(self) -> dict:
                return {"startup_duration_ms": captured["startup_duration_ms"]}

        class InlineExecutorLoop:
            async def run_in_executor(self, _executor, function):
                return function()

        api_server.hivemind.DHT = FakeDHT
        api_server.RemoteSequential = FakeSequential
        api_server.DistributedGenerator = FakeGenerator
        api_server.get_local_model_path = lambda model_name: None

        with tempfile.TemporaryDirectory() as identity_dir, patch.dict(
            os.environ,
            {"DISTRIBLLM_P2P_IDENTITY_DIR": identity_dir},
        ), patch.object(
            api_server.torch.cuda,
            "is_available",
            return_value=False,
        ), patch.object(
            api_server.asyncio,
            "get_running_loop",
            return_value=InlineExecutorLoop(),
        ):
            result = asyncio.run(
                api_server.start_generator(
                    api_server.GeneratorStartRequest(model_name=MODEL_ID)
                )
            )
            generator_identity = Path(captured["dht_kwargs"]["identity_path"])
            self.assertEqual(
                generator_identity,
                get_role_identity_path("generator"),
            )
            self.assertNotEqual(
                generator_identity,
                get_role_identity_path("control-plane"),
            )
            self.assertEqual(
                api_server._generator_dht_kwargs([])["identity_path"],
                str(generator_identity),
            )

        topology_provider = captured["topology_provider"]
        self.assertTrue(callable(topology_provider))
        self.assertEqual(
            [node["peer_id"] for node in topology_provider()],
            ["worker-peer"],
        )
        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            supervisor.role_updates,
            [
                ("generator", "generator-peer", "starting", None),
                ("generator", "generator-peer", "ready", None),
            ],
        )
        self.assertTrue(captured["dht_kwargs"]["client_mode"])
        self.assertTrue(captured["dht_kwargs"]["use_relay"])

    def test_generator_commit_rechecks_selected_local_provider_liveness(self) -> None:
        class LocalProvider:
            node_id = "local-provider"
            model_name = MODEL_ID
            layer_start = 0
            layer_end = 12
            dht_prefix = "distribllm"

            def __init__(self) -> None:
                self.running = True

            def is_running(self) -> bool:
                return self.running

            def get_peer_id(self) -> str:
                return "worker-peer"

            def get_visible_maddrs(self) -> list[str]:
                return []

            def get_info(self) -> dict:
                return serving_node(0, 12, "worker-peer")

        local_provider = LocalProvider()
        api_server._register_local_node(local_provider)
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(
                state="ready",
                nodes=[serving_node(0, 12, "worker-peer")],
            )
        )

        class FakeDHT:
            peer_id = "generator-peer"

            def __init__(self, **_kwargs) -> None:
                self.shutdown_calls = 0

            def shutdown(self) -> None:
                self.shutdown_calls += 1

        class FakeSequential:
            def __init__(self, **_kwargs) -> None:
                self.stop_calls = 0

            def start_health_monitor(self) -> None:
                return None

            def stop_health_monitor(self) -> None:
                self.stop_calls += 1

            def get_health_readiness(self) -> dict:
                return {
                    "enabled": True,
                    "route_ready": True,
                    "selected_route": [serving_node(0, 12, "worker-peer")],
                    "reasons": [],
                }

        class FakeGenerator:
            def __init__(self, **kwargs) -> None:
                self.model_name = kwargs["model_name"]
                self.sequential = kwargs["sequential"]
                self.loaded = False
                self.unload_calls = 0

            def load(self) -> None:
                self.loaded = True
                local_provider.running = False

            def unload(self, timeout: float = 2.0) -> bool:
                self.unload_calls += 1
                self.loaded = False
                return True

            def is_loaded(self) -> bool:
                return self.loaded

            def get_performance_snapshot(self) -> dict:
                return {"startup_duration_ms": 1.0}

        class InlineExecutorLoop:
            async def run_in_executor(self, _executor, function):
                return function()

        api_server.hivemind.DHT = FakeDHT
        api_server.RemoteSequential = FakeSequential
        api_server.DistributedGenerator = FakeGenerator
        api_server.get_local_model_path = lambda _model_name: None

        with tempfile.TemporaryDirectory() as identity_dir, patch.dict(
            os.environ,
            {"DISTRIBLLM_P2P_IDENTITY_DIR": identity_dir},
        ), patch.object(
            api_server.asyncio,
            "get_running_loop",
            return_value=InlineExecutorLoop(),
        ), patch.object(api_server.torch.cuda, "is_available", return_value=False):
            result = asyncio.run(
                api_server.start_generator(
                    api_server.GeneratorStartRequest(model_name=MODEL_ID)
                )
            )

        self.assertEqual(result["status"], "cancelled")
        self.assertIn("became unavailable", result["message"])
        self.assertIsNone(api_server.generator)
        self.assertIsNone(api_server.client_dht)

    def _assert_cancelled_generator_executor_stage_is_joined(
        self,
        stage: str,
    ) -> None:
        target_names = {
            "dht": "construct_candidate_dht",
            "route": "validate_reachable_route",
            "canary": "validate_tensor_route",
        }
        future_attributes = {
            "dht": "dht_future",
            "route": "route_validation_future",
            "canary": "tensor_canary_future",
        }
        target_name = target_names[stage]
        order: list[str] = []
        created_dht: list[object] = []
        created_sequential: list[object] = []

        supervisor = FakeSupervisor(
            network_snapshot(
                state="ready",
                nodes=[serving_node(0, 12, "worker-peer")],
            )
        )
        api_server.network_supervisor = supervisor
        api_server._local_node_infos = lambda: []

        class FakeDHT:
            peer_id = "generator-peer"

            def __init__(self, **_kwargs) -> None:
                self.shutdown_calls = 0
                created_dht.append(self)
                order.append("dht_work_finished")

            def shutdown(self) -> None:
                self.shutdown_calls += 1
                order.append("dht_shutdown")

        class FakeSequential:
            def __init__(self, **_kwargs) -> None:
                created_sequential.append(self)

            def start_health_monitor(self) -> None:
                order.append("monitor_started")

            def stop_health_monitor(self) -> None:
                order.append("monitor_stopped")

        if stage == "route":
            def validate_reachable_route(self) -> list[dict]:
                order.append("route_work_finished")
                return [serving_node(0, 12, "worker-peer")]

            FakeSequential.validate_reachable_route = validate_reachable_route
        elif stage == "canary":
            def get_health_readiness(self) -> dict:
                return {
                    "route_ready": True,
                    "selected_route": [serving_node(0, 12, "worker-peer")],
                    "reasons": [],
                }

            def validate_tensor_route(self, hidden_size: int) -> dict:
                order.append("canary_work_finished")
                return {"ok": True, "hidden_size": hidden_size}

            FakeSequential.get_health_readiness = get_health_readiness
            FakeSequential.validate_tensor_route = validate_tensor_route

        class UnexpectedGenerator:
            def __init__(self, **_kwargs) -> None:
                raise AssertionError(
                    "Generator construction must wait for startup validation"
                )

        api_server.hivemind.DHT = FakeDHT
        api_server.RemoteSequential = FakeSequential
        api_server.DistributedGenerator = UnexpectedGenerator
        api_server.get_local_model_path = lambda _model_name: None

        async def run_race() -> None:
            executor_loop = ControlledExecutorLoop(
                asyncio.get_running_loop(),
                lambda function: (
                    getattr(function, "__name__", "") == target_name
                ),
            )
            with patch.object(
                api_server.asyncio,
                "get_running_loop",
                return_value=executor_loop,
            ):
                task = asyncio.create_task(
                    api_server.start_generator(
                        api_server.GeneratorStartRequest(model_name=MODEL_ID)
                    )
                )
                for _ in range(200):
                    if executor_loop.blocked_started.is_set():
                        break
                    await asyncio.sleep(0.005)
                self.assertTrue(executor_loop.blocked_started.is_set())
                with api_server._generator_lifecycle_lock:
                    transaction = api_server._active_generator_start
                    self.assertIsNotNone(transaction)
                    self.assertIs(
                        getattr(transaction, future_attributes[stage]),
                        executor_loop.blocked_future,
                    )

                task.cancel()
                await asyncio.sleep(0.02)

                self.assertFalse(task.done())
                self.assertNotIn(f"{stage}_work_finished", order)
                self.assertNotIn("monitor_stopped", order)
                self.assertNotIn("dht_shutdown", order)

                try:
                    executor_loop.complete_blocked()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=3.0)
                finally:
                    if (
                        executor_loop.blocked_future is not None
                        and not executor_loop.blocked_future.done()
                    ):
                        executor_loop.complete_blocked()

        asyncio.run(run_race())

        self.assertIn(f"{stage}_work_finished", order)
        self.assertEqual(len(created_dht), 1)
        self.assertEqual(created_dht[0].shutdown_calls, 1)
        self.assertLess(
            order.index(f"{stage}_work_finished"),
            order.index("dht_shutdown"),
        )
        if stage != "dht":
            self.assertEqual(len(created_sequential), 1)
            self.assertLess(
                order.index(f"{stage}_work_finished"),
                order.index("monitor_stopped"),
            )
            self.assertLess(
                order.index("monitor_stopped"),
                order.index("dht_shutdown"),
            )
        else:
            self.assertEqual(created_sequential, [])
        self.assertIsNone(api_server.generator)
        self.assertIsNone(api_server.client_dht)
        self.assertIsNone(api_server._active_generator_start)
        self.assertTrue(api_server._generator_start_lock.acquire(blocking=False))
        api_server._generator_start_lock.release()

    def test_cancelled_dht_construction_is_joined_before_exact_shutdown(self) -> None:
        self._assert_cancelled_generator_executor_stage_is_joined("dht")

    def test_cancelled_legacy_route_validation_is_joined_before_cleanup(self) -> None:
        self._assert_cancelled_generator_executor_stage_is_joined("route")

    def test_cancelled_tensor_canary_is_joined_before_cleanup(self) -> None:
        self._assert_cancelled_generator_executor_stage_is_joined("canary")

    def test_ready_registration_shutdown_race_rolls_back_candidate(self) -> None:
        created_dht: list[object] = []
        created_generators: list[object] = []
        monitor_stops: list[bool] = []

        class ReadyFlipSupervisor(FakeSupervisor):
            def register_role(
                self,
                role: str,
                *,
                peer_id: str | None,
                state: str,
                role_id: str | None = None,
            ) -> None:
                super().register_role(
                    role,
                    peer_id=peer_id,
                    state=state,
                    role_id=role_id,
                )
                if role == "generator" and state == "ready":
                    self.accepting_roles = False

        supervisor = ReadyFlipSupervisor(
            network_snapshot(
                state="ready",
                nodes=[serving_node(0, 12, "worker-peer")],
            )
        )
        api_server.network_supervisor = supervisor
        api_server._local_node_infos = lambda: []

        class FakeDHT:
            peer_id = "generator-peer"

            def __init__(self, **_kwargs) -> None:
                self.shutdown_calls = 0
                created_dht.append(self)

            def shutdown(self) -> None:
                self.shutdown_calls += 1

        class FakeSequential:
            def __init__(self, **_kwargs) -> None:
                pass

            def start_health_monitor(self) -> None:
                pass

            def stop_health_monitor(self) -> None:
                monitor_stops.append(True)

            def get_health_readiness(self) -> dict:
                return {
                    "route_ready": True,
                    "selected_route": [serving_node(0, 12, "worker-peer")],
                    "reasons": [],
                }

        class FakeGenerator:
            def __init__(self, **kwargs) -> None:
                self.model_name = kwargs["model_name"]
                self.sequential = kwargs["sequential"]
                self.loaded = False
                self.unload_calls = 0
                created_generators.append(self)

            def load(self) -> None:
                self.loaded = True

            def unload(self) -> None:
                self.unload_calls += 1
                self.loaded = False

            def is_loaded(self) -> bool:
                return self.loaded

            def get_performance_snapshot(self) -> dict:
                return {"startup_duration_ms": 1.0}

        class InlineExecutorLoop:
            async def run_in_executor(self, _executor, function):
                return function()

        api_server.hivemind.DHT = FakeDHT
        api_server.RemoteSequential = FakeSequential
        api_server.DistributedGenerator = FakeGenerator
        api_server.get_local_model_path = lambda _model_name: None

        with tempfile.TemporaryDirectory() as identity_dir, patch.dict(
            os.environ,
            {"DISTRIBLLM_P2P_IDENTITY_DIR": identity_dir},
        ), patch.object(
            api_server.asyncio,
            "get_running_loop",
            return_value=InlineExecutorLoop(),
        ), patch.object(api_server.torch.cuda, "is_available", return_value=False):
            result = asyncio.run(
                api_server.start_generator(
                    api_server.GeneratorStartRequest(model_name=MODEL_ID)
                )
            )

        self.assertEqual(result["status"], "cancelled")
        self.assertIsNone(api_server.generator)
        self.assertIsNone(api_server.client_dht)
        self.assertIsNone(api_server._active_generator_start)
        self.assertEqual(len(created_generators), 1)
        self.assertEqual(created_generators[0].unload_calls, 1)
        self.assertEqual(len(created_dht), 1)
        self.assertEqual(created_dht[0].shutdown_calls, 1)
        self.assertEqual(len(monitor_stops), 1)
        self.assertEqual(
            supervisor.role_updates,
            [
                ("generator", "generator-peer", "starting", None),
                ("generator", "generator-peer", "ready", None),
                ("generator", None, "unregistered", None),
            ],
        )
        self.assertTrue(api_server._generator_start_lock.acquire(blocking=False))
        api_server._generator_start_lock.release()

    def test_cancelled_blocked_load_finishes_before_candidate_cleanup(self) -> None:
        load_started = threading.Event()
        load_finished = threading.Event()
        unload_started = threading.Event()
        order: list[str] = []
        created_dht: list[object] = []
        created_generators: list[object] = []
        monitor_stop_calls: list[bool] = []

        supervisor = FakeSupervisor(
            network_snapshot(
                state="ready",
                nodes=[serving_node(0, 12, "worker-peer")],
            )
        )
        api_server.network_supervisor = supervisor
        api_server._local_node_infos = lambda: []

        class FakeDHT:
            peer_id = "generator-peer"

            def __init__(self, **_kwargs) -> None:
                self.shutdown_calls = 0
                created_dht.append(self)

            def shutdown(self) -> None:
                self.shutdown_calls += 1
                order.append("dht_shutdown")

        class FakeSequential:
            def __init__(self, **_kwargs) -> None:
                pass

            def start_health_monitor(self) -> None:
                pass

            def stop_health_monitor(self) -> None:
                monitor_stop_calls.append(True)
                order.append("monitor_stop")

            def get_health_readiness(self) -> dict:
                return {
                    "route_ready": True,
                    "selected_route": [serving_node(0, 12, "worker-peer")],
                    "reasons": [],
                }

        class BlockingGenerator:
            def __init__(self, **kwargs) -> None:
                self.model_name = kwargs["model_name"]
                self.sequential = kwargs["sequential"]
                self.loaded = False
                self.unload_calls = 0
                created_generators.append(self)

            def load(self) -> None:
                order.append("load_started")
                load_started.set()
                self.loaded = True
                order.append("load_finished")
                load_finished.set()

            def unload(self) -> None:
                unload_started.set()
                self.assert_load_finished()
                self.unload_calls += 1
                self.loaded = False
                order.append("generator_unload")

            @staticmethod
            def assert_load_finished() -> None:
                if not load_finished.is_set():
                    raise AssertionError("candidate unload raced its in-flight load")

            def is_loaded(self) -> bool:
                return self.loaded

        api_server.hivemind.DHT = FakeDHT
        api_server.RemoteSequential = FakeSequential
        api_server.DistributedGenerator = BlockingGenerator
        api_server.get_local_model_path = lambda _model_name: None

        async def run_race() -> None:
            executor_loop = ControlledExecutorLoop(
                asyncio.get_running_loop(),
                lambda function: getattr(function, "__name__", "") == "load",
            )
            with patch.object(
                api_server.asyncio,
                "get_running_loop",
                return_value=executor_loop,
            ):
                task = asyncio.create_task(
                    api_server.start_generator(
                        api_server.GeneratorStartRequest(model_name=MODEL_ID)
                    )
                )
                try:
                    for _ in range(200):
                        if executor_loop.blocked_started.is_set():
                            break
                        await asyncio.sleep(0.005)
                    self.assertTrue(executor_loop.blocked_started.is_set())
                    self.assertFalse(load_started.is_set())
                    with api_server._generator_lifecycle_lock:
                        api_server._backend_closing = True
                        transaction = api_server._active_generator_start
                        self.assertIsNotNone(transaction)
                        transaction.cancel_event.set()
                    supervisor.accepting_roles = False
                    await asyncio.sleep(0.02)
                    self.assertFalse(unload_started.is_set())
                    self.assertFalse(task.done())
                finally:
                    if (
                        executor_loop.blocked_future is not None
                        and not executor_loop.blocked_future.done()
                    ):
                        executor_loop.complete_blocked()
                result = await asyncio.wait_for(task, timeout=3.0)
                self.assertEqual(result["status"], "cancelled")

        with tempfile.TemporaryDirectory() as identity_dir, patch.dict(
            os.environ,
            {"DISTRIBLLM_P2P_IDENTITY_DIR": identity_dir},
        ), patch.object(api_server.torch.cuda, "is_available", return_value=False):
            asyncio.run(run_race())

        self.assertTrue(load_finished.is_set())
        self.assertEqual(len(created_generators), 1)
        self.assertEqual(created_generators[0].unload_calls, 1)
        self.assertEqual(len(created_dht), 1)
        self.assertEqual(created_dht[0].shutdown_calls, 1)
        self.assertEqual(len(monitor_stop_calls), 1)
        self.assertLess(order.index("load_finished"), order.index("generator_unload"))
        self.assertLess(order.index("generator_unload"), order.index("dht_shutdown"))
        self.assertIsNone(api_server.generator)
        self.assertIsNone(api_server.client_dht)
        self.assertIsNone(api_server._active_generator_start)
        self.assertTrue(api_server._generator_start_lock.acquire(blocking=False))
        api_server._generator_start_lock.release()

    def test_unload_closes_detached_dht_not_replacement_after_await(self) -> None:
        unload_started = threading.Event()
        old_unload_finished = threading.Event()
        order: list[str] = []

        class FakeDHT:
            def __init__(self, name: str) -> None:
                self.name = name
                self.shutdown_calls = 0

            def shutdown(self) -> None:
                self.shutdown_calls += 1
                order.append(f"{self.name}_dht_shutdown")

        class BlockingOldGenerator:
            model_name = MODEL_ID

            def __init__(self) -> None:
                self.request_stop_calls = 0
                self.unload_calls = 0

            def request_stop(self) -> None:
                self.request_stop_calls += 1

            def unload(self) -> None:
                self.unload_calls += 1
                order.append("old_unload_started")
                unload_started.set()
                order.append("old_unload_finished")
                old_unload_finished.set()

        class ReplacementGenerator:
            model_name = MODEL_ID

        old_generator = BlockingOldGenerator()
        old_dht = FakeDHT("old")
        replacement_generator = ReplacementGenerator()
        replacement_dht = FakeDHT("replacement")
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        with api_server._generator_lifecycle_lock:
            api_server.generator = old_generator
            api_server.client_dht = old_dht

        async def run_race() -> dict:
            executor_loop = ControlledExecutorLoop(
                asyncio.get_running_loop(),
                lambda function: (
                    getattr(function, "__self__", None) is old_generator
                    and getattr(function, "__name__", "") == "unload"
                ),
            )
            with patch.object(
                api_server.asyncio,
                "get_running_loop",
                return_value=executor_loop,
            ):
                task = asyncio.create_task(
                    api_server._unload_generator_runtime("Replace test runtime.")
                )
                try:
                    for _ in range(200):
                        if executor_loop.blocked_started.is_set():
                            break
                        await asyncio.sleep(0.005)
                    self.assertTrue(executor_loop.blocked_started.is_set())
                    self.assertFalse(unload_started.is_set())
                    self.assertIsNone(api_server.generator)
                    self.assertIsNone(api_server.client_dht)
                    with api_server._generator_lifecycle_lock:
                        api_server.generator = replacement_generator
                        api_server.client_dht = replacement_dht
                finally:
                    if (
                        executor_loop.blocked_future is not None
                        and not executor_loop.blocked_future.done()
                    ):
                        executor_loop.complete_blocked()
                return await asyncio.wait_for(task, timeout=3.0)

        result = asyncio.run(run_race())

        self.assertEqual(result["status"], "unloaded")
        self.assertTrue(old_unload_finished.is_set())
        self.assertEqual(old_generator.request_stop_calls, 1)
        self.assertEqual(old_generator.unload_calls, 1)
        self.assertEqual(old_dht.shutdown_calls, 1)
        self.assertEqual(replacement_dht.shutdown_calls, 0)
        self.assertIs(api_server.generator, replacement_generator)
        self.assertIs(api_server.client_dht, replacement_dht)
        self.assertFalse(api_server._generator_cleanup_in_progress)
        self.assertLess(order.index("old_unload_finished"), order.index("old_dht_shutdown"))

    def test_unload_publishes_terminal_state_before_reopening_admission(self) -> None:
        class OrderingState(RuntimeStateStore):
            def __init__(self) -> None:
                super().__init__()
                self.cleanup_gate_during_stopped = None

            def transition_generator(self, state: str, **kwargs) -> dict:
                if state == "stopped":
                    self.cleanup_gate_during_stopped = (
                        api_server._generator_cleanup_in_progress
                    )
                return super().transition_generator(state, **kwargs)

        class LoadedGenerator:
            model_name = MODEL_ID

            def __init__(self) -> None:
                self.loaded = True

            def is_loaded(self) -> bool:
                return self.loaded

            def request_stop(self) -> None:
                return None

            def unload(self, timeout: float = 2.0) -> bool:
                self.loaded = False
                return True

        class ExactDHT:
            def shutdown(self) -> None:
                return None

        runtime_state = OrderingState()
        api_server._runtime_state = runtime_state
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        api_server.generator = LoadedGenerator()
        api_server.client_dht = ExactDHT()

        result = asyncio.run(
            api_server._unload_generator_runtime("Ordering regression test.")
        )

        self.assertEqual(result["status"], "unloaded")
        self.assertTrue(runtime_state.cleanup_gate_during_stopped)
        self.assertFalse(api_server._generator_cleanup_in_progress)

    def test_generator_dht_shutdown_retains_identity_until_process_exit(self) -> None:
        process_exited = threading.Event()

        class LateExitDHT:
            def __init__(self) -> None:
                self.shutdown_calls = 0
                self.join_calls = 0

            def shutdown(self) -> None:
                self.shutdown_calls += 1

            def is_alive(self) -> bool:
                return not process_exited.is_set()

            def join(self, timeout: float) -> None:
                self.join_calls += 1
                process_exited.wait(timeout)

        dht = LateExitDHT()

        first = api_server._close_generator_dht(dht, timeout=0.01)

        self.assertEqual(first["status"], "pending")
        self.assertIsNotNone(api_server._generator_identity_quarantine)
        self.assertEqual(dht.shutdown_calls, 1)

        process_exited.set()
        second = api_server._close_generator_dht(dht, timeout=1.0)

        self.assertEqual(second["status"], "stopped")
        self.assertEqual(dht.shutdown_calls, 1)
        self.assertGreaterEqual(dht.join_calls, 1)
        self.assertIsNone(api_server._generator_identity_quarantine)

    def test_pending_dht_cleanup_retry_joins_exact_attempt(self) -> None:
        shutdown_started = threading.Event()
        release_shutdown = threading.Event()

        class BlockingDHT:
            def __init__(self) -> None:
                self.shutdown_calls = 0

            def shutdown(self) -> None:
                self.shutdown_calls += 1
                shutdown_started.set()
                release_shutdown.wait(2.0)

        class ReplacementDHT:
            def __init__(self) -> None:
                self.shutdown_calls = 0

            def shutdown(self) -> None:
                self.shutdown_calls += 1

        old_dht = BlockingDHT()
        replacement_dht = ReplacementDHT()
        replacement_generator = object()
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        with api_server._generator_lifecycle_lock:
            api_server.generator = None
            api_server.client_dht = old_dht

        async def run_cleanup() -> tuple[dict, dict]:
            first = api_server._shutdown_client_dht(timeout=0.01)
            self.assertTrue(shutdown_started.is_set())
            self.assertIsNotNone(api_server._pending_generator_cleanup)
            with api_server._generator_lifecycle_lock:
                api_server.generator = replacement_generator
                api_server.client_dht = replacement_dht
            release_shutdown.set()
            second = await api_server._unload_generator_runtime(
                "Retry exact cleanup.",
                timeout=1.0,
            )
            return first, second

        try:
            with patch.object(
                api_server,
                "shutdown_remote_expert_p2p",
                return_value=True,
            ):
                first, second = asyncio.run(run_cleanup())
                terminal = api_server._close_generator_dht(old_dht, timeout=0.0)
        finally:
            release_shutdown.set()

        self.assertEqual(first["status"], "pending")
        self.assertEqual(second["status"], "unloaded")
        self.assertEqual(second["network"]["status"], "stopped")
        self.assertEqual(terminal["status"], "stopped")
        self.assertEqual(old_dht.shutdown_calls, 1)
        self.assertEqual(replacement_dht.shutdown_calls, 0)
        self.assertIs(api_server.generator, replacement_generator)
        self.assertIs(api_server.client_dht, replacement_dht)
        self.assertIsNone(api_server._pending_generator_cleanup)
        self.assertIsNone(api_server._generator_identity_quarantine)

    def test_dht_shutdown_error_is_terminal_and_not_reinvoked(self) -> None:
        class FailingDHT:
            def __init__(self) -> None:
                self.shutdown_calls = 0

            def shutdown(self) -> None:
                self.shutdown_calls += 1
                raise RuntimeError("shutdown failed")

        dht = FailingDHT()
        with patch.object(
            api_server,
            "shutdown_remote_expert_p2p",
            return_value=True,
        ):
            first = api_server._close_generator_dht(dht, timeout=1.0)
            second = api_server._close_generator_dht(dht, timeout=1.0)

        self.assertEqual(first["status"], "error")
        self.assertEqual(second, first)
        self.assertIn("RuntimeError: shutdown failed", first["error"])
        self.assertEqual(dht.shutdown_calls, 1)
        self.assertEqual(
            api_server._generator_identity_quarantine,
            api_server._GENERATOR_DHT_QUARANTINE_REASON,
        )

    def test_successful_dht_tombstone_does_not_retain_transport(self) -> None:
        class FakeDHT:
            _p2p_replica = None

            def shutdown(self) -> None:
                pass

        dht = FakeDHT()
        attempt_key = id(dht)
        dht_ref = weakref.ref(dht)

        result = api_server._close_generator_dht(dht, timeout=1.0)

        self.assertEqual(result["status"], "stopped")
        self.assertIsInstance(
            api_server._generator_dht_shutdown_attempts[attempt_key],
            api_server._GeneratorDHTShutdownTombstone,
        )
        del dht
        gc.collect()

        self.assertIsNone(dht_ref())
        self.assertNotIn(
            attempt_key,
            api_server._generator_dht_shutdown_attempts,
        )

    def test_exact_shutdown_completion_does_not_clear_an_unrelated_quarantine(self) -> None:
        shutdown_started = threading.Event()
        release_shutdown = threading.Event()

        class BlockingDHT:
            def shutdown(self) -> None:
                shutdown_started.set()
                release_shutdown.wait(2.0)

        dht = BlockingDHT()
        replacement_quarantine = "A replacement generator cleanup is unresolved."
        try:
            with patch.object(
                api_server,
                "shutdown_remote_expert_p2p",
                return_value=True,
            ):
                pending = api_server._close_generator_dht(dht, timeout=0.01)
                self.assertTrue(shutdown_started.is_set())
                api_server._generator_identity_quarantine = replacement_quarantine
                release_shutdown.set()
                stopped = api_server._close_generator_dht(dht, timeout=1.0)
        finally:
            release_shutdown.set()

        self.assertEqual(pending["status"], "pending")
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(
            api_server._generator_identity_quarantine,
            replacement_quarantine,
        )

    def test_node_shutdown_waits_for_owned_start_before_rollback(self) -> None:
        start_entered = threading.Event()
        release_start = threading.Event()
        start_finished = threading.Event()
        cleanup_started = threading.Event()
        order: list[str] = []

        class BlockingNode:
            def __init__(self, **kwargs) -> None:
                self.node_id = "candidate-node"
                self.model_name = kwargs["model_name"]
                self.layer_start = kwargs["layer_start"]
                self.layer_end = kwargs["layer_end"]
                self.dht_prefix = kwargs["dht_prefix"]
                self.rpc_uid_suffix = None
                self.running = False

            def start(self) -> None:
                order.append("start_entered")
                start_entered.set()
                if not release_start.wait(3.0):
                    raise TimeoutError("test did not release node start")
                self.running = True
                order.append("start_finished")
                start_finished.set()

            def stop(self) -> bool:
                if not start_finished.is_set():
                    raise AssertionError("candidate cleanup raced node.start")
                order.append("node_stop")
                cleanup_started.set()
                self.running = False
                return True

            def is_running(self) -> bool:
                return self.running

        supervisor = FakeSupervisor(network_snapshot(state="ready", nodes=[]))
        api_server.network_supervisor = supervisor
        api_server.Node = BlockingNode
        api_server.get_local_model_path = lambda _model_name: None

        async def run_race() -> dict:
            task = asyncio.create_task(
                api_server._start_node_runtime(
                    api_server.NodeStartRequest(
                        model_name=MODEL_ID,
                        layer_start=0,
                        layer_end=6,
                        device="cpu",
                    )
                )
            )
            try:
                for _ in range(200):
                    if start_entered.is_set():
                        break
                    await asyncio.sleep(0.005)
                self.assertTrue(start_entered.is_set())
                with api_server._generator_lifecycle_lock:
                    api_server._backend_closing = True
                    active = list(api_server._active_node_starts.values())
                    self.assertEqual(len(active), 1)
                    active[0].cancel_event.set()
                await asyncio.sleep(0.02)
                self.assertFalse(task.done())
                self.assertFalse(cleanup_started.is_set())
            finally:
                release_start.set()
            return await asyncio.wait_for(task, timeout=3.0)

        result = asyncio.run(run_race())

        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(order, ["start_entered", "start_finished", "node_stop"])
        self.assertEqual(api_server.local_nodes, {})
        self.assertEqual(api_server._active_node_starts, {})

    def test_failed_node_rollback_retains_candidate_for_delete_retry(self) -> None:
        cancelled = threading.Event()

        class RetryCleanupNode:
            def __init__(self, **kwargs) -> None:
                self.node_id = "retry-cleanup-node"
                self.model_name = kwargs["model_name"]
                self.layer_start = kwargs["layer_start"]
                self.layer_end = kwargs["layer_end"]
                self.dht_prefix = kwargs["dht_prefix"]
                self.rpc_uid_suffix = None
                self.running = False
                self.stop_calls = 0

            def start(self) -> None:
                self.running = True
                cancelled.set()

            def stop(self) -> bool:
                self.stop_calls += 1
                if self.stop_calls == 1:
                    return False
                self.running = False
                return True

            def is_running(self) -> bool:
                return self.running

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": "worker-peer",
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                    "announcement": {"publication_state": "degraded"},
                }

        supervisor = FakeSupervisor(network_snapshot(state="ready", nodes=[]))
        api_server.network_supervisor = supervisor
        api_server.Node = RetryCleanupNode
        api_server.get_local_model_path = lambda _model_name: None

        result = asyncio.run(
            api_server._start_node_runtime(
                api_server.NodeStartRequest(
                    model_name=MODEL_ID,
                    layer_start=0,
                    layer_end=6,
                    device="cpu",
                ),
                external_cancel=cancelled,
            )
        )

        self.assertEqual(result["status"], "cleanup_pending")
        retained = api_server.local_nodes["retry-cleanup-node"]
        self.assertEqual(retained.stop_calls, 1)
        self.assertEqual(api_server._active_node_starts, {})

        deleted = asyncio.run(api_server.delete_node("retry-cleanup-node"))

        self.assertEqual(deleted["status"], "deleted")
        self.assertEqual(retained.stop_calls, 2)
        self.assertEqual(api_server.local_nodes, {})

    def test_node_start_rechecks_committed_overlap_before_claiming_range(self) -> None:
        class ExistingNode:
            node_id = "existing-node"
            model_name = MODEL_ID
            layer_start = 0
            layer_end = 6
            dht_prefix = "distribllm"

        existing = ExistingNode()
        created: list[object] = []

        class CandidateNode:
            def __init__(self, **kwargs) -> None:
                self.node_id = "candidate-node"
                self.model_name = kwargs["model_name"]
                self.layer_start = kwargs["layer_start"]
                self.layer_end = kwargs["layer_end"]
                self.dht_prefix = kwargs["dht_prefix"]
                self.rpc_uid_suffix = None
                self.start_calls = 0
                created.append(self)
                api_server._register_local_node(existing)

            def start(self) -> None:
                self.start_calls += 1

        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        api_server.Node = CandidateNode
        api_server.get_local_model_path = lambda _model_name: None

        result = asyncio.run(
            api_server._start_node_runtime(
                api_server.NodeStartRequest(
                    model_name=MODEL_ID,
                    layer_start=3,
                    layer_end=9,
                    device="cpu",
                )
            )
        )

        self.assertEqual(result["error"], "overlapping_layer_range")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].start_calls, 0)
        self.assertEqual(api_server._active_node_starts, {})

    def test_node_lifecycle_actions_reject_concurrent_delete(self) -> None:
        turn_off_entered = threading.Event()
        release_turn_off = threading.Event()

        class BlockingNode:
            node_id = "blocking-node"
            model_name = MODEL_ID
            layer_start = 0
            layer_end = 6
            dht_prefix = "distribllm"

            def __init__(self) -> None:
                self.running = True
                self.stop_calls = 0

            def is_running(self) -> bool:
                return self.running

            def turn_off(self) -> bool:
                turn_off_entered.set()
                if not release_turn_off.wait(3.0):
                    raise TimeoutError("test did not release turn-off")
                self.running = False
                return True

            def stop(self) -> bool:
                self.stop_calls += 1
                return True

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": "worker-peer",
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                }

        local_node = BlockingNode()
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        api_server._register_local_node(local_node)

        async def run_race() -> tuple[dict, dict]:
            task = asyncio.create_task(api_server.turn_off_node(local_node.node_id))
            for _ in range(200):
                if turn_off_entered.is_set():
                    break
                await asyncio.sleep(0.005)
            self.assertTrue(turn_off_entered.is_set())
            conflict = await api_server.delete_node(local_node.node_id)
            self.assertFalse(task.done())
            release_turn_off.set()
            return conflict, await asyncio.wait_for(task, timeout=3.0)

        try:
            conflict, result = asyncio.run(run_race())
        finally:
            release_turn_off.set()

        self.assertEqual(conflict["error"], "node_lifecycle_operation_in_progress")
        self.assertEqual(result["status"], "turned_off")
        self.assertEqual(local_node.stop_calls, 0)
        self.assertEqual(api_server._active_node_operations, {})

    def test_turn_off_retries_retained_cleanup_after_node_is_no_longer_running(
        self,
    ) -> None:
        class RetryNode:
            node_id = "retry-turn-off"
            model_name = MODEL_ID
            layer_start = 0
            layer_end = 6
            dht_prefix = "distribllm"

            def __init__(self) -> None:
                self.pending = True
                self.turn_off_calls = 0

            def is_running(self) -> bool:
                return False

            def has_pending_serving_cleanup(self) -> bool:
                return self.pending

            def turn_off(self) -> bool:
                self.turn_off_calls += 1
                self.pending = False
                return True

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": "retry-peer",
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                    "announcement": {"publication_state": "degraded"},
                }

        local_node = RetryNode()
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        api_server._register_local_node(local_node)

        result = asyncio.run(api_server.turn_off_node(local_node.node_id))

        self.assertEqual(result["status"], "turned_off")
        self.assertEqual(local_node.turn_off_calls, 1)
        self.assertFalse(local_node.pending)

    def test_turn_off_uses_atomic_node_predicate_for_failed_recovery_state(
        self,
    ) -> None:
        class RecoveryPendingNode:
            node_id = "recovery-pending-node"
            model_name = MODEL_ID
            layer_start = 0
            layer_end = 6
            dht_prefix = "distribllm"

            def __init__(self) -> None:
                self.turn_off_calls = 0

            def requires_turn_off(self) -> bool:
                return True

            def is_running(self) -> bool:
                raise AssertionError(
                    "The atomic predicate must replace the split status read"
                )

            def turn_off(self) -> bool:
                self.turn_off_calls += 1
                return True

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": "recovery-peer",
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                    "running": False,
                    "announcement": {"publication_state": "degraded"},
                }

        local_node = RecoveryPendingNode()
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        api_server._register_local_node(local_node)

        result = asyncio.run(api_server.turn_off_node(local_node.node_id))

        self.assertEqual(result["status"], "turned_off")
        self.assertEqual(local_node.turn_off_calls, 1)

    def test_turn_off_uses_atomic_node_predicate_for_clean_already_off_state(
        self,
    ) -> None:
        class AlreadyOffNode:
            node_id = "already-off-node"
            model_name = MODEL_ID
            layer_start = 0
            layer_end = 6
            dht_prefix = "distribllm"

            def __init__(self) -> None:
                self.predicate_calls = 0

            def requires_turn_off(self) -> bool:
                self.predicate_calls += 1
                return False

            def is_running(self) -> bool:
                raise AssertionError(
                    "The atomic predicate must replace the split status read"
                )

            def turn_off(self) -> bool:
                raise AssertionError("A cleanly off node needs no cleanup")

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": "already-off-peer",
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                    "running": False,
                }

        local_node = AlreadyOffNode()
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        api_server._register_local_node(local_node)

        result = asyncio.run(api_server.turn_off_node(local_node.node_id))

        self.assertEqual(result["status"], "already_off")
        self.assertEqual(local_node.predicate_calls, 1)
        self.assertEqual(api_server._active_node_operations, {})

    def test_generator_start_and_destructive_node_actions_exclude_each_other(
        self,
    ) -> None:
        class LocalNode:
            node_id = "route-node"
            model_name = MODEL_ID
            layer_start = 0
            layer_end = 12
            dht_prefix = "distribllm"

            def __init__(self) -> None:
                self.turn_off_calls = 0

            def is_running(self) -> bool:
                return True

            def get_peer_id(self) -> str:
                return "route-peer"

            def get_visible_maddrs(self) -> list[str]:
                return []

            def turn_off(self) -> bool:
                self.turn_off_calls += 1
                return True

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": "route-peer",
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                }

        supervisor = FakeSupervisor(network_snapshot(state="ready", nodes=[]))
        api_server.network_supervisor = supervisor
        local_node = LocalNode()
        api_server._register_local_node(local_node)
        transaction = api_server._GeneratorStartTransaction("generator-start")
        api_server._active_generator_start = transaction

        blocked_node = asyncio.run(api_server.turn_off_node(local_node.node_id))

        self.assertEqual(blocked_node["error"], "generator_start_in_progress")
        self.assertEqual(local_node.turn_off_calls, 0)

        api_server._active_generator_start = None
        operation = api_server._NodeLifecycleOperation(local_node, "delete")
        api_server._active_node_operations[local_node.node_id] = operation
        api_server.get_local_model_path = lambda _model_name: None

        blocked_generator = asyncio.run(
            api_server.start_generator(
                api_server.GeneratorStartRequest(model_name=MODEL_ID)
            )
        )

        self.assertEqual(
            blocked_generator["error"],
            "node_lifecycle_operation_in_progress",
        )
        self.assertIsNone(api_server.generator)

    def test_cancelled_node_action_finishes_exact_owner_before_propagating(self) -> None:
        turn_off_entered = threading.Event()
        release_turn_off = threading.Event()

        class BlockingNode:
            node_id = "cancelled-node"
            model_name = MODEL_ID
            layer_start = 0
            layer_end = 6
            dht_prefix = "distribllm"

            def __init__(self) -> None:
                self.running = True

            def is_running(self) -> bool:
                return self.running

            def turn_off(self) -> bool:
                turn_off_entered.set()
                if not release_turn_off.wait(3.0):
                    raise TimeoutError("test did not release turn-off")
                self.running = False
                return True

            def get_info(self) -> dict:
                return {
                    "node_id": self.node_id,
                    "peer_id": "worker-peer",
                    "model_name": self.model_name,
                    "layer_start": self.layer_start,
                    "layer_end": self.layer_end,
                }

        local_node = BlockingNode()
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        api_server._register_local_node(local_node)

        async def run_race() -> None:
            task = asyncio.create_task(api_server.turn_off_node(local_node.node_id))
            for _ in range(200):
                if turn_off_entered.is_set():
                    break
                await asyncio.sleep(0.005)
            self.assertTrue(turn_off_entered.is_set())
            task.cancel()
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            release_turn_off.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3.0)

        try:
            asyncio.run(run_race())
        finally:
            release_turn_off.set()

        self.assertFalse(local_node.running)
        self.assertEqual(api_server._active_node_operations, {})

    def test_generator_readiness_never_switches_owner_across_await(self) -> None:
        class LoadedGenerator:
            model_name = MODEL_ID

            def __init__(self) -> None:
                self.sequential = object()

            def is_loaded(self) -> bool:
                return True

            def get_performance_snapshot(self) -> dict:
                return {"startup_duration_ms": 1.0}

        old_generator = LoadedGenerator()
        replacement = LoadedGenerator()
        api_server.generator = old_generator

        async def run_race() -> None:
            status_entered = asyncio.Event()
            release_status = asyncio.Event()

            async def blocking_status() -> dict:
                status_entered.set()
                await release_status.wait()
                return {
                    "ready": True,
                    "state": "ready",
                    "route_ready": True,
                    "components_loaded": True,
                    "reasons": [],
                }

            with patch.object(api_server, "get_generator_status", blocking_status):
                task = asyncio.create_task(api_server._require_generator_ready())
                await status_entered.wait()
                with api_server._generator_lifecycle_lock:
                    api_server.generator = replacement
                release_status.set()
                with self.assertRaises(api_server.HTTPException) as raised:
                    await asyncio.wait_for(task, timeout=3.0)
                self.assertEqual(raised.exception.status_code, 503)

        asyncio.run(run_race())

        self.assertIs(api_server.generator, replacement)

    def test_generator_cleanup_timeout_retains_exact_handles_for_retry(self) -> None:
        class RetryGenerator:
            model_name = MODEL_ID

            def __init__(self) -> None:
                self.loaded = True
                self.unload_calls = 0

            def request_stop(self) -> None:
                return None

            def unload(self, timeout: float = 2.0) -> bool:
                self.unload_calls += 1
                if self.unload_calls == 1:
                    return False
                self.loaded = False
                return True

            def is_loaded(self) -> bool:
                return self.loaded

        class ExactDHT:
            peer_id = "generator-peer"

            def __init__(self) -> None:
                self.shutdown_calls = 0

            def shutdown(self) -> None:
                self.shutdown_calls += 1

        candidate = RetryGenerator()
        dht = ExactDHT()
        api_server.network_supervisor = FakeSupervisor(
            network_snapshot(state="ready", nodes=[])
        )
        api_server.generator = candidate
        api_server.client_dht = dht

        first = asyncio.run(
            api_server._unload_generator_runtime("Retry cleanup test.")
        )

        self.assertEqual(first["status"], "cleanup_pending")
        self.assertEqual(candidate.unload_calls, 1)
        self.assertEqual(dht.shutdown_calls, 0)
        self.assertIsNone(api_server.generator)
        self.assertIsNone(api_server.client_dht)
        self.assertIsNotNone(api_server._pending_generator_cleanup)
        status = asyncio.run(api_server.get_generator_status())
        self.assertEqual(status["state"], "stopping")
        self.assertFalse(status["ready"])

        blocked = asyncio.run(
            api_server.start_generator(
                api_server.GeneratorStartRequest(model_name=MODEL_ID)
            )
        )
        self.assertEqual(blocked["error"], "generator_cleanup_pending")

        second = asyncio.run(
            api_server._unload_generator_runtime("Retry cleanup test.")
        )

        self.assertEqual(second["status"], "unloaded")
        self.assertEqual(candidate.unload_calls, 2)
        self.assertEqual(dht.shutdown_calls, 1)
        self.assertIsNone(api_server._pending_generator_cleanup)

    def test_role_start_endpoints_reject_shutdown_before_runtime_creation(self) -> None:
        supervisor = FakeSupervisor(
            network_snapshot(state="degraded", nodes=[]),
            accepting_roles=False,
        )
        api_server.network_supervisor = supervisor

        class UnexpectedRuntime:
            def __init__(self, *args, **kwargs) -> None:
                raise AssertionError("Runtime construction must not start during shutdown")

        api_server.Node = UnexpectedRuntime
        api_server.hivemind.DHT = UnexpectedRuntime

        node_result = asyncio.run(
            api_server.start_node(
                api_server.NodeStartRequest(
                    model_name=MODEL_ID,
                    layer_start=0,
                    layer_end=6,
                    device="cpu",
                )
            )
        )
        generator_result = asyncio.run(
            api_server.start_generator(
                api_server.GeneratorStartRequest(model_name=MODEL_ID)
            )
        )

        self.assertEqual(node_result["error"], "backend_shutting_down")
        self.assertEqual(generator_result["error"], "backend_shutting_down")

    def test_async_start_submission_is_terminal_when_admission_is_closed(self) -> None:
        store = LifecycleJobStore()
        self.assertTrue(store.cancel_all_and_wait(0))
        api_server._lifecycle_jobs = store
        called = False

        async def unexpected_start(_request) -> dict:
            nonlocal called
            called = True
            return {"status": "ready"}

        original_start_generator = api_server.start_generator
        api_server.start_generator = unexpected_start
        try:
            result = asyncio.run(
                api_server.start_generator_async(
                    api_server.GeneratorStartRequest(model_name=MODEL_ID)
                )
            )
        finally:
            api_server.start_generator = original_start_generator

        self.assertFalse(called)
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(result["stage"], "admission_closed")
        self.assertTrue(result["cancel_requested"])
        self.assertEqual(store.diagnostics()["active_thread_count"], 0)

    def test_late_node_start_cancel_preserves_the_committed_runtime(self) -> None:
        store = LifecycleJobStore()
        api_server._lifecycle_jobs = store
        committed = threading.Event()
        release = threading.Event()

        async def committed_start(_request, *, external_cancel=None) -> dict:
            committed.set()
            if not release.wait(3.0):
                raise TimeoutError("test did not release committed node start")
            return {
                "status": "started",
                "info": {"node_id": "committed-node"},
            }

        async def unexpected_delete(*_args, **_kwargs) -> dict:
            raise AssertionError("late cancellation must not delete a committed node")

        request = api_server.NodeStartRequest(
            model_name=MODEL_ID,
            layer_start=0,
            layer_end=6,
            device="cpu",
        )
        with patch.object(api_server, "_start_node_runtime", committed_start), patch.object(
            api_server,
            "delete_node",
            unexpected_delete,
        ):
            submitted = asyncio.run(api_server.start_node_async(request))
            self.assertTrue(committed.wait(1.0))
            cancelled = store.cancel(submitted["job_id"])
            self.assertIsNotNone(cancelled)
            release.set()
            self.assertTrue(store.cancel_all_and_wait(3.0))

        completed = store.get(submitted["job_id"])
        self.assertIsNotNone(completed)
        self.assertEqual(completed["status"], "ready")
        self.assertTrue(completed["cancel_requested"])
        self.assertEqual(completed["result"]["status"], "started")
        self.assertTrue(completed["result"]["cancel_requested"])

    def test_late_generator_start_cancel_preserves_the_committed_runtime(self) -> None:
        store = LifecycleJobStore()
        api_server._lifecycle_jobs = store
        committed = threading.Event()
        release = threading.Event()

        async def committed_start(_request, *, external_cancel=None) -> dict:
            committed.set()
            if not release.wait(3.0):
                raise TimeoutError("test did not release committed generator start")
            return {"status": "ready", "route_ready": True}

        async def unexpected_unload(*_args, **_kwargs) -> dict:
            raise AssertionError(
                "late cancellation must not unload a committed generator"
            )

        with patch.object(
            api_server,
            "_start_generator_runtime",
            committed_start,
        ), patch.object(
            api_server,
            "_unload_generator_runtime",
            unexpected_unload,
        ):
            submitted = asyncio.run(
                api_server.start_generator_async(
                    api_server.GeneratorStartRequest(model_name=MODEL_ID)
                )
            )
            self.assertTrue(committed.wait(1.0))
            cancelled = store.cancel(submitted["job_id"])
            self.assertIsNotNone(cancelled)
            release.set()
            self.assertTrue(store.cancel_all_and_wait(3.0))

        completed = store.get(submitted["job_id"])
        self.assertIsNotNone(completed)
        self.assertEqual(completed["status"], "ready")
        self.assertTrue(completed["cancel_requested"])
        self.assertEqual(completed["result"]["status"], "ready")
        self.assertTrue(completed["result"]["cancel_requested"])

    def test_lifespan_shutdown_is_best_effort_across_cleanup_failures(self) -> None:
        events: list[str] = []

        class ShutdownSupervisor:
            dht_prefix = "distribllm"
            accepting_roles = True

            def start(self) -> dict:
                events.append("supervisor_start")
                return {}

            def begin_shutdown(self) -> None:
                events.append("supervisor_begin_shutdown")

            def stop(self, timeout: float) -> dict:
                events.append("supervisor_stop")
                raise RuntimeError("supervisor cleanup exploded")

        class FakeGPU:
            def __init__(self, interval: float) -> None:
                self.interval = interval

            def start(self) -> None:
                events.append("gpu_start")

            def stop(self) -> None:
                events.append("gpu_stop")

        async def fail_generator_cleanup(reason: str, timeout: float) -> dict:
            events.append("generator_cleanup")
            raise RuntimeError("generator cleanup exploded")

        def stop_nodes(timeout: float) -> list[dict]:
            events.append("node_cleanup")
            raise RuntimeError("node cleanup exploded")

        api_server.network_supervisor = ShutdownSupervisor()
        api_server._lifecycle_jobs = LifecycleJobStore()

        async def run_lifespan() -> None:
            with (
                patch.object(api_server, "_validate_environment"),
                patch.object(api_server, "GPUMonitor", FakeGPU),
                patch.object(
                    api_server,
                    "_unload_generator_runtime",
                    fail_generator_cleanup,
                ),
                patch.object(api_server, "_shutdown_local_nodes", stop_nodes),
            ):
                async with api_server.lifespan(api_server.app):
                    pass

        asyncio.run(run_lifespan())

        self.assertLess(events.index("generator_cleanup"), events.index("node_cleanup"))
        self.assertLess(events.index("node_cleanup"), events.index("supervisor_stop"))
        self.assertLess(events.index("supervisor_stop"), events.index("gpu_stop"))
        snapshot = api_server._runtime_state.generator_snapshot()
        self.assertEqual(snapshot["state"], "failed")
        reasons = " ".join(snapshot["reasons"])
        self.assertIn("generator cleanup exploded", reasons)
        self.assertIn("node cleanup exploded", reasons)
        self.assertIn("supervisor cleanup exploded", reasons)

    def test_lifespan_keeps_generator_stopping_when_startup_is_unresolved(self) -> None:
        class ShutdownSupervisor:
            dht_prefix = "distribllm"
            accepting_roles = True

            def start(self) -> dict:
                return {}

            def begin_shutdown(self) -> None:
                self.accepting_roles = False

            def stop(self, timeout: float) -> dict:
                return {
                    "status": "stopped",
                    "thread_stopped": True,
                    "dht_stopped": True,
                }

        class FakeGPU:
            def __init__(self, interval: float) -> None:
                self.interval = interval

            def start(self) -> None:
                return None

            def stop(self) -> None:
                return None

        class PendingGenerator:
            model_name = MODEL_ID

            def is_loaded(self) -> bool:
                return True

        transaction = api_server._GeneratorStartTransaction("pending-start")
        transaction.candidate_generator = PendingGenerator()
        api_server.network_supervisor = ShutdownSupervisor()
        api_server._lifecycle_jobs = LifecycleJobStore()

        async def run_lifespan() -> None:
            with (
                patch.object(api_server, "_validate_environment"),
                patch.object(api_server, "GPUMonitor", FakeGPU),
                patch.object(api_server, "DEFAULT_SHUTDOWN_TIMEOUT_SECONDS", 0.01),
            ):
                async with api_server.lifespan(api_server.app):
                    with api_server._generator_lifecycle_lock:
                        api_server._active_generator_start = transaction

        asyncio.run(run_lifespan())

        self.assertTrue(transaction.cancel_event.is_set())
        snapshot = api_server._runtime_state.generator_snapshot()
        self.assertEqual(snapshot["state"], "stopping")
        self.assertTrue(snapshot["components_loaded"])
        self.assertIn("exact generator startup", " ".join(snapshot["reasons"]))


if __name__ == "__main__":
    unittest.main()
