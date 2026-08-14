import asyncio
from types import SimpleNamespace
import unittest

import torch
from fastapi import HTTPException

from api.runtime_state import RuntimeStateStore
from client.sequential import RemoteSequential


class RuntimeStateStoreTests(unittest.TestCase):
    def test_generator_readiness_is_one_authoritative_state(self) -> None:
        store = RuntimeStateStore(event_limit=4)
        store.transition_generator(
            "loading",
            model_name="facebook/opt-125m",
            components_loaded=False,
            route_ready=True,
        )
        ready = store.transition_generator(
            "ready",
            model_name="facebook/opt-125m",
            components_loaded=True,
            route_ready=True,
            reasons=[],
        )
        suspended = store.transition_generator(
            "suspended",
            model_name="facebook/opt-125m",
            components_loaded=True,
            route_ready=False,
            reasons=["Provider RPC is offline."],
        )

        self.assertEqual(ready["state"], "ready")
        self.assertEqual(suspended["state"], "suspended")
        self.assertTrue(suspended["components_loaded"])
        self.assertFalse(suspended["route_ready"])
        self.assertLessEqual(len(store.snapshot()["events"]), 4)


class TensorCanaryTests(unittest.TestCase):
    def test_tensor_canary_requires_expected_finite_shape(self) -> None:
        sequential = object.__new__(RemoteSequential)
        sequential.validate_reachable_route = lambda: [
            {
                "peer_id": "peer-a",
                "rpc_uid": "rpc-a",
                "layer_start": 0,
                "layer_end": 12,
            }
        ]
        sequential.forward = lambda hidden_states, **kwargs: (
            hidden_states + 1,
            ["peer-a"],
        )

        result = sequential.validate_tensor_route(8)

        self.assertTrue(result["ok"])
        self.assertEqual(result["shape"], [1, 1, 8])
        self.assertEqual(result["node_trace"], ["peer-a"])

    def test_tensor_canary_rejects_non_finite_output(self) -> None:
        sequential = object.__new__(RemoteSequential)
        sequential.validate_reachable_route = lambda: []
        sequential.forward = lambda hidden_states, **kwargs: (
            torch.full_like(hidden_states, float("nan")),
            [],
        )

        with self.assertRaisesRegex(RuntimeError, "non-finite"):
            sequential.validate_tensor_route(4)


class RuntimeApiValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        from api import server as api_server

        self.api_server = api_server
        self.original_generator = api_server.generator
        self.original_node = api_server.node
        self.original_local_nodes = dict(api_server.local_nodes)
        self.original_client_dht = api_server.client_dht
        api_server.generator = None
        api_server.node = None
        api_server.local_nodes.clear()
        api_server.client_dht = None

    def tearDown(self) -> None:
        self.api_server.generator = self.original_generator
        self.api_server.node = self.original_node
        self.api_server.local_nodes.clear()
        self.api_server.local_nodes.update(self.original_local_nodes)
        self.api_server.client_dht = self.original_client_dht

    @staticmethod
    def _node(node_id: str = "node-a") -> SimpleNamespace:
        node = SimpleNamespace(
            node_id=node_id,
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=6,
            dht_prefix="distribllm",
            rpc_uid_suffix=None,
            stopped=False,
        )
        node.get_peer_id = lambda: "peer-a"
        node.stop = lambda: setattr(node, "stopped", True)
        node.is_running = lambda: True
        node.get_info = lambda: {
            "node_id": node.node_id,
            "peer_id": "peer-a",
            "model_name": node.model_name,
            "layer_start": node.layer_start,
            "layer_end": node.layer_end,
        }
        return node

    def test_second_exact_local_replica_requires_confirmation(self) -> None:
        first = self._node()
        self.api_server.local_nodes[first.node_id] = first
        request = self.api_server.NodeStartRequest(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=6,
            device="cpu",
        )

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.api_server.start_node(request))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail["error"],
            "local_replica_confirmation_required",
        )

    def test_third_exact_local_replica_is_rejected(self) -> None:
        self.api_server.local_nodes["node-a"] = self._node("node-a")
        self.api_server.local_nodes["node-b"] = self._node("node-b")
        request = self.api_server.NodeStartRequest(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=6,
            device="cpu",
            confirm_local_replica=True,
        )

        result = asyncio.run(self.api_server.start_node(request))

        self.assertEqual(result["error"], "local_replica_limit_reached")

    def test_loaded_generator_with_unhealthy_route_is_suspended(self) -> None:
        sequential = SimpleNamespace(
            get_health_readiness=lambda: {
                "enabled": True,
                "route_ready": False,
                "reasons": ["Provider RPC is offline."],
                "selected_route": [],
            }
        )
        self.api_server.generator = SimpleNamespace(
            model_name="facebook/opt-125m",
            sequential=sequential,
            is_loaded=lambda: True,
        )

        result = asyncio.run(self.api_server.get_generator_status())

        self.assertFalse(result["ready"])
        self.assertTrue(result["components_loaded"])
        self.assertEqual(result["state"], "suspended")

    def test_deleting_required_node_requires_confirmation_and_unloads_generator(self) -> None:
        local_node = self._node()
        self.api_server.local_nodes[local_node.node_id] = local_node
        sequential = SimpleNamespace(
            get_health_readiness=lambda: {
                "selected_route": [
                    {
                        "peer_id": "peer-a",
                        "layer_start": 0,
                        "layer_end": 6,
                    }
                ],
                "alternate_routes": [],
            }
        )
        fake_generator = SimpleNamespace(
            model_name="facebook/opt-125m",
            sequential=sequential,
            stopped=False,
            unloaded=False,
        )
        fake_generator.is_loaded = lambda: True
        fake_generator.request_stop = lambda: setattr(fake_generator, "stopped", True)
        fake_generator.unload = lambda: setattr(fake_generator, "unloaded", True)
        self.api_server.generator = fake_generator
        original_to_thread = self.api_server.asyncio.to_thread

        async def run_inline(function, *args, **kwargs):
            return function(*args, **kwargs)

        self.api_server.asyncio.to_thread = run_inline
        try:
            blocked = asyncio.run(self.api_server.delete_node(local_node.node_id))
            deleted = asyncio.run(
                self.api_server.delete_node(local_node.node_id, confirm_generator_stop=True)
            )
        finally:
            self.api_server.asyncio.to_thread = original_to_thread

        self.assertEqual(
            blocked["error"],
            "generator_dependency_confirmation_required",
        )
        self.assertEqual(deleted["status"], "deleted")
        self.assertTrue(fake_generator.stopped)
        self.assertTrue(fake_generator.unloaded)
        self.assertTrue(local_node.stopped)
        self.assertIsNone(self.api_server.generator)


if __name__ == "__main__":
    unittest.main()
