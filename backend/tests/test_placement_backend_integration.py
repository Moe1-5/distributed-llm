import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from client.coverage import build_serving_plan
from placement.client import PlacementClientError


def coordinator_plan() -> dict:
    occupancy_nodes = [
        {
            "peer_id": "reservation-one",
            "rpc_uid": "reservation-one",
            "node_id": "reservation-one",
            "layer_start": 0,
            "layer_end": 6,
        }
    ]
    online_nodes: list[dict] = []
    occupancy = build_serving_plan(occupancy_nodes, 12, 6)
    online = build_serving_plan(online_nodes, 12, 6)
    return {
        "model_name": "facebook/opt-125m",
        "model_revision": "main",
        "total_layers": 12,
        "requested_layer_count": 6,
        "topology_revision": 4,
        "captured_at": "2026-08-23T00:00:00+00:00",
        "capacity_available": True,
        "recommendation": {
            **occupancy["recommendation"],
            "layer_start": 6,
            "layer_end": 12,
        },
        "occupancy_plan": occupancy,
        "online_plan": online,
        "reservations": [],
    }


class FakePlacementRuntime:
    enabled = True
    config = SimpleNamespace(model_revision="main")

    def __init__(self, *, fail_reserve: bool = False, fail_plan: bool = False) -> None:
        self.fail_reserve = fail_reserve
        self.fail_plan = fail_plan
        self.reserve_calls: list[dict] = []
        self.joining_calls: list[tuple[str, str]] = []
        self.online_calls: list[tuple[str, dict]] = []
        self.release_calls: list[tuple[str, str]] = []
        self._leases: dict[str, dict] = {}

    def status(self) -> dict:
        return {
            "enabled": True,
            "connectivity": "unavailable" if self.fail_plan or self.fail_reserve else "connected",
            "active_leases": [],
        }

    def plan(self, model_name: str, layer_capacity: int) -> dict:
        if self.fail_plan:
            raise PlacementClientError("placement_unavailable", "coordinator offline")
        self.last_plan = (model_name, layer_capacity)
        return coordinator_plan()

    def reserve(self, **kwargs) -> dict:
        if self.fail_reserve:
            raise PlacementClientError("placement_unavailable", "coordinator offline")
        self.reserve_calls.append(kwargs)
        reservation = {
            "reservation_id": "reservation-two",
            "reservation_token": "secret-reservation-token-000000000000",
            "participant_id": kwargs["participant_id"],
            "model_name": kwargs["model_name"],
            "model_revision": "main",
            "layer_start": 6,
            "layer_end": 12,
            "state": "RESERVED",
            "topology_revision": 5,
        }
        return {"topology_revision": 5, "reservation": reservation}

    def begin_joining(self, reservation: dict, node_id: str) -> dict:
        tracked = {**reservation, "state": "JOINING", "node_id": node_id}
        self._leases[node_id] = tracked
        self.joining_calls.append((reservation["reservation_id"], node_id))
        return {key: value for key, value in tracked.items() if key != "reservation_token"}

    def mark_online(self, node_id: str, info: dict) -> dict:
        self.online_calls.append((node_id, info))
        tracked = {
            **self._leases[node_id],
            "state": "ONLINE",
            "peer_id": info["peer_id"],
            "rpc_uid": info["rpc_uid"],
        }
        self._leases[node_id] = tracked
        return {key: value for key, value in tracked.items() if key != "reservation_token"}

    def release_node(self, node_id: str, reason: str):
        self.release_calls.append((node_id, reason))
        self._leases.pop(node_id, None)
        return None

    def release_reservation(self, reservation: dict, reason: str):
        self.release_calls.append((reservation["reservation_id"], reason))
        return None


class FakeNode:
    fail_start = False

    def __init__(self, **kwargs) -> None:
        self.node_id = "coordinated-node"
        self.model_name = kwargs["model_name"]
        self.layer_start = kwargs["layer_start"]
        self.layer_end = kwargs["layer_end"]
        self.dht_prefix = kwargs["dht_prefix"]
        self.rpc_uid_suffix = None
        self.dht = None
        self.running = False
        self.placement_model_revision = None
        self.placement_lease = None

    def start(self) -> None:
        if self.fail_start:
            raise RuntimeError("simulated layer load failure")
        self.running = True

    def stop(self) -> bool:
        self.running = False
        return True

    def is_running(self) -> bool:
        return self.running

    def get_info(self) -> dict:
        rpc_uid = "distribllm.expert.6.12.coordinated-node"
        return {
            "peer_id": "worker-peer",
            "node_id": self.node_id,
            "rpc_uid": rpc_uid,
            "rpc_peer_id": "worker-peer",
            "model_name": self.model_name,
            "placement_model_revision": self.placement_model_revision,
            "layer_start": self.layer_start,
            "layer_end": self.layer_end,
            "device": "cpu",
            "running": self.running,
            "layers_loaded": self.running,
            "rpc_running": self.running,
            "maddrs": [],
            "announcement": {"fresh": self.running},
            "rpc_publication": {"fresh": self.running, "uids": [rpc_uid]},
            "placement": self.placement_lease,
        }


class PlacementBackendIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        from api import server as api_server

        self.api_server = api_server
        self.original_runtime = api_server.placement_runtime
        self.original_node_class = api_server.Node
        self.original_network_snapshot = api_server._network_snapshot
        self.original_active_nodes = api_server._active_serving_nodes
        self.original_local_node_path = api_server.get_local_model_path
        self.original_node = api_server.node
        self.original_local_nodes = dict(api_server.local_nodes)
        api_server.Node = FakeNode
        api_server.get_local_model_path = lambda _model_name: None
        api_server._active_serving_nodes = lambda *_args, **_kwargs: []
        api_server._network_snapshot = lambda: {
            "state": "disconnected",
            "control_peer_id": "control-peer",
            "resources": {},
            "nodes": [],
            "revision": 1,
            "topology_revision": "dht-revision",
        }
        api_server.node = None
        api_server.local_nodes.clear()

    def tearDown(self) -> None:
        self.api_server.placement_runtime = self.original_runtime
        self.api_server.Node = self.original_node_class
        self.api_server._network_snapshot = self.original_network_snapshot
        self.api_server._active_serving_nodes = self.original_active_nodes
        self.api_server.get_local_model_path = self.original_local_node_path
        self.api_server.local_nodes.clear()
        self.api_server.local_nodes.update(self.original_local_nodes)
        self.api_server.node = self.original_node
        FakeNode.fail_start = False

    def test_serving_plan_uses_coordinator_not_dht_snapshot(self) -> None:
        runtime = FakePlacementRuntime()
        self.api_server.placement_runtime = runtime
        self.api_server._active_serving_nodes = lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("DHT coverage must not decide placement")
        )

        plan = asyncio.run(
            self.api_server.get_model_serving_plan("facebook/opt-125m", 6)
        )

        self.assertEqual(plan["snapshot_source"], "placement_coordinator")
        self.assertEqual(plan["coverage_revision"], "placement:4")
        self.assertEqual(
            (plan["recommendation"]["layer_start"], plan["recommendation"]["layer_end"]),
            (6, 12),
        )
        self.assertTrue(plan["placement"]["authoritative"])

    def test_recommended_start_loads_only_coordinator_allocated_range(self) -> None:
        runtime = FakePlacementRuntime()
        self.api_server.placement_runtime = runtime
        request = self.api_server.NodeStartRequest(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=6,
            layer_capacity=6,
            placement_mode="recommended",
            placement_revision=4,
            placement_idempotency_key="renderer-request-one",
            dht_prefix="distribllm",
            device="cpu",
        )

        result = asyncio.run(self.api_server.start_node(request))

        self.assertEqual(result["status"], "started")
        self.assertEqual(
            (result["info"]["layer_start"], result["info"]["layer_end"]),
            (6, 12),
        )
        self.assertEqual(runtime.reserve_calls[0]["placement_mode"], "recommended")
        self.assertEqual(runtime.reserve_calls[0]["expected_topology_revision"], 4)
        self.assertEqual(len(runtime.joining_calls), 1)
        self.assertEqual(len(runtime.online_calls), 1)
        self.assertEqual(result["info"]["placement"]["state"], "ONLINE")

    def test_failed_load_releases_joining_reservation(self) -> None:
        runtime = FakePlacementRuntime()
        self.api_server.placement_runtime = runtime
        FakeNode.fail_start = True
        request = self.api_server.NodeStartRequest(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=6,
            layer_capacity=6,
            placement_mode="recommended",
            dht_prefix="distribllm",
            device="cpu",
        )

        result = asyncio.run(self.api_server.start_node(request))

        self.assertEqual(result["status"], "error")
        self.assertEqual(runtime.release_calls, [("coordinated-node", "startup_rollback")])

    def test_coordinator_unavailable_fails_closed_for_plan_and_start(self) -> None:
        runtime = FakePlacementRuntime(fail_plan=True, fail_reserve=True)
        self.api_server.placement_runtime = runtime
        with self.assertRaises(HTTPException) as plan_error:
            asyncio.run(self.api_server.get_model_serving_plan("facebook/opt-125m", 6))
        self.assertEqual(plan_error.exception.status_code, 503)

        request = self.api_server.NodeStartRequest(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=6,
            layer_capacity=6,
            placement_mode="recommended",
            dht_prefix="distribllm",
            device="cpu",
        )
        with self.assertRaises(HTTPException) as start_error:
            asyncio.run(self.api_server.start_node(request))
        self.assertEqual(start_error.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
