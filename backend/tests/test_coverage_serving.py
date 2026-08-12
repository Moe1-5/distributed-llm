import asyncio
import sys
import unittest
from pathlib import Path

from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from client.coverage import (
    build_serving_plan,
    find_route_spans,
    route_requirement_ranges,
    select_route,
)


def node(start: int, end: int, peer: str) -> dict:
    return {
        "peer_id": peer,
        "node_id": peer,
        "rpc_uid": f"uid-{peer}",
        "model_name": "facebook/opt-125m",
        "layer_start": start,
        "layer_end": end,
        "running": True,
        "layers_loaded": True,
        "rpc_running": True,
    }


class CoveragePlannerTests(unittest.TestCase):
    def test_partial_head_alone_needs_tail(self) -> None:
        plan = build_serving_plan([node(0, 6, "head")], 12, 6)

        self.assertFalse(plan["current_runnable"])
        self.assertEqual(plan["missing_ranges"], [{"start": 6, "end": 12}])
        self.assertEqual(
            (plan["recommendation"]["layer_start"], plan["recommendation"]["layer_end"]),
            (6, 12),
        )
        self.assertTrue(plan["recommendation"]["completes_route"])

    def test_duplicate_heads_remain_incomplete(self) -> None:
        plan = build_serving_plan(
            [node(0, 6, "head-a"), node(0, 6, "head-b")],
            12,
            6,
        )

        self.assertFalse(plan["current_runnable"])
        self.assertEqual(plan["missing_ranges"], [{"start": 6, "end": 12}])
        self.assertEqual(plan["segments"][0]["provider_count"], 2)

    def test_adjacent_halves_form_multi_provider_route(self) -> None:
        plan = build_serving_plan(
            [node(0, 6, "head"), node(6, 12, "tail")],
            12,
            6,
        )

        self.assertTrue(plan["current_runnable"])
        self.assertEqual(plan["route_kind"], "multiple_providers")
        self.assertEqual(
            [(item["layer_start"], item["layer_end"]) for item in plan["selected_route"]],
            [(0, 6), (6, 12)],
        )

    def test_full_provider_wins_and_overlap_is_standby(self) -> None:
        plan = build_serving_plan(
            [node(0, 6, "partial"), node(0, 12, "full")],
            12,
            6,
        )

        self.assertEqual([item["peer_id"] for item in plan["selected_route"]], ["full"])
        self.assertEqual([item["peer_id"] for item in plan["standby_ranges"]], ["partial"])
        self.assertEqual(plan["route_kind"], "single_provider")

    def test_same_half_replicas_do_not_advance_when_full_span_is_selected(self) -> None:
        nodes = [
            node(0, 6, "partial-a"),
            node(0, 6, "partial-b"),
            node(0, 12, "full"),
        ]
        cursors: dict[tuple[int, int], int] = {}

        selected, _ = select_route(nodes, 12, cursors, advance_replicas=True)

        self.assertEqual([item["peer_id"] for item in selected], ["full"])
        self.assertNotIn((0, 6), cursors)
        self.assertEqual(cursors[(0, 12)], 0)

    def test_shorter_adjacent_path_beats_longer_dead_end(self) -> None:
        nodes = [
            node(0, 8, "dead-end"),
            node(0, 6, "head"),
            node(6, 12, "tail"),
        ]

        self.assertEqual(find_route_spans(nodes, 12), [(0, 6), (6, 12)])

    def test_overlap_dead_end_reports_route_specific_requirement(self) -> None:
        nodes = [node(0, 5, "head"), node(4, 8, "overlap")]

        self.assertEqual(route_requirement_ranges(nodes, 8), [{"start": 5, "end": 8}])

    def test_empty_network_recommends_prefix_for_capacity(self) -> None:
        plan = build_serving_plan([], 12, 6)

        self.assertEqual(
            (plan["recommendation"]["layer_start"], plan["recommendation"]["layer_end"]),
            (0, 6),
        )
        self.assertTrue(plan["recommendation"]["extends_reachable_prefix"])
        self.assertEqual(plan["projected_missing_ranges"], [{"start": 6, "end": 12}])
        self.assertEqual(
            [(segment["start"], segment["end"], segment["recommended"]) for segment in plan["segments"]],
            [(0, 6, True), (6, 12, False)],
        )


class CoverageApiConflictTests(unittest.TestCase):
    def setUp(self) -> None:
        from api import server as api_server

        self.api_server = api_server
        self.original_active_nodes = api_server._active_serving_nodes

    def tearDown(self) -> None:
        self.api_server._active_serving_nodes = self.original_active_nodes

    def test_stale_revision_returns_http_409_with_fresh_plan(self) -> None:
        self.api_server._active_serving_nodes = lambda model_id, dht_prefix=None: [
            node(0, 6, "head")
        ]
        request = self.api_server.NodeStartRequest(
            model_name="facebook/opt-125m",
            layer_start=6,
            layer_end=12,
            dht_prefix="distribllm",
            initial_peers=[],
            device="cpu",
            coverage_revision="stale-revision",
        )

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.api_server.start_node(request))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["error"], "coverage_revision_stale")
        self.assertEqual(raised.exception.detail["plan"]["missing_ranges"], [{"start": 6, "end": 12}])

    def test_unhelpful_replica_requires_redundancy_confirmation(self) -> None:
        self.api_server._active_serving_nodes = lambda model_id, dht_prefix=None: [
            node(0, 6, "head")
        ]
        request = self.api_server.NodeStartRequest(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=6,
            dht_prefix="distribllm",
            initial_peers=[],
            device="cpu",
        )

        with self.assertRaises(HTTPException) as raised:
            asyncio.run(self.api_server.start_node(request))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail["error"],
            "redundancy_confirmation_required",
        )
        self.assertEqual(
            raised.exception.detail["plan"]["recommendation"]["layer_start"],
            6,
        )

    def test_serving_plan_endpoint_returns_selected_and_standby_ranges(self) -> None:
        self.api_server._active_serving_nodes = lambda model_id, dht_prefix=None: [
            node(0, 6, "partial"),
            node(0, 12, "full"),
        ]

        plan = asyncio.run(
            self.api_server.get_model_serving_plan("facebook/opt-125m", 6)
        )

        self.assertEqual(plan["route_kind"], "single_provider")
        self.assertEqual([item["peer_id"] for item in plan["selected_route"]], ["full"])
        self.assertEqual([item["peer_id"] for item in plan["standby_ranges"]], ["partial"])

    def test_confirmed_redundancy_preserves_legacy_node_start(self) -> None:
        created: list[object] = []

        class FakeNode:
            def __init__(self, **kwargs) -> None:
                self.node_id = "local-replica"
                self.model_name = kwargs["model_name"]
                self.layer_start = kwargs["layer_start"]
                self.layer_end = kwargs["layer_end"]
                self.dht_prefix = kwargs["dht_prefix"]
                self.dht = None
                self.rpc_uid_suffix = None
                self.running = False
                created.append(self)

            def start(self) -> None:
                self.running = True

            def is_running(self) -> bool:
                return self.running

            def get_info(self) -> dict:
                return {
                    **node(self.layer_start, self.layer_end, self.node_id),
                    "node_id": self.node_id,
                }

        original_node_class = self.api_server.Node
        original_get_local_model_path = self.api_server.get_local_model_path
        original_node = self.api_server.node
        original_local_nodes = dict(self.api_server.local_nodes)
        self.api_server.Node = FakeNode
        self.api_server.get_local_model_path = lambda model_name: None
        self.api_server.node = None
        self.api_server.local_nodes.clear()
        self.api_server._active_serving_nodes = lambda model_id, dht_prefix=None: [
            node(0, 6, "remote-head")
        ]
        try:
            result = asyncio.run(
                self.api_server.start_node(
                    self.api_server.NodeStartRequest(
                        model_name="facebook/opt-125m",
                        layer_start=0,
                        layer_end=6,
                        dht_prefix="distribllm",
                        initial_peers=[],
                        device="cpu",
                        confirm_redundancy=True,
                    )
                )
            )
        finally:
            self.api_server.Node = original_node_class
            self.api_server.get_local_model_path = original_get_local_model_path
            self.api_server.local_nodes.clear()
            self.api_server.local_nodes.update(original_local_nodes)
            self.api_server.node = original_node

        self.assertEqual(result["status"], "started")
        self.assertEqual(len(created), 1)


if __name__ == "__main__":
    unittest.main()
