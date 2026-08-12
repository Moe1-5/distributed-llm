import unittest
import sys
import asyncio
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from client.coverage import build_serving_plan, coverage_ranges, select_route_spans


def node(start: int, end: int, peer: str) -> dict:
    return {
        "peer_id": peer,
        "rpc_uid": f"test.{start}.{end}.{peer}",
        "layer_start": start,
        "layer_end": end,
    }


class CoveragePlanningTests(unittest.TestCase):
    def test_partial_provider_recommends_complementary_range(self) -> None:
        plan = build_serving_plan(
            model_name="test/model",
            nodes=[node(0, 6, "first")],
            total_layers=12,
            layer_count=6,
        )

        self.assertEqual(plan["recommended_range"]["layer_start"], 6)
        self.assertEqual(plan["recommended_range"]["layer_end"], 12)
        self.assertTrue(plan["recommended_range"]["completes_route"])
        self.assertTrue(plan["runnable_after"])

    def test_duplicate_partial_providers_do_not_make_route_runnable(self) -> None:
        plan = build_serving_plan(
            model_name="test/model",
            nodes=[node(0, 6, "first"), node(0, 6, "replica")],
            total_layers=12,
            layer_count=6,
        )

        self.assertFalse(plan["runnable_before"])
        self.assertEqual(plan["missing_ranges"], [{"layer_start": 6, "layer_end": 12}])
        self.assertEqual(plan["recommended_range"]["layer_start"], 6)

    def test_split_providers_form_complete_route(self) -> None:
        spans = select_route_spans(
            [node(0, 6, "first"), node(6, 12, "second")],
            12,
        )
        self.assertEqual(spans, [(0, 6), (6, 12)])

    def test_full_provider_is_selected_over_overlapping_partial(self) -> None:
        spans = select_route_spans(
            [node(0, 6, "partial"), node(0, 12, "full")],
            12,
        )
        self.assertEqual(spans, [(0, 12)])

    def test_fully_covered_model_recommends_least_replicated_range(self) -> None:
        plan = build_serving_plan(
            model_name="test/model",
            nodes=[node(0, 6, "first"), node(6, 12, "second")],
            total_layers=12,
            layer_count=6,
        )

        self.assertEqual(plan["recommended_range"]["adds"], "redundancy")
        self.assertEqual(plan["recommended_range"]["layer_start"], 0)
        self.assertTrue(plan["runnable_before"])

    def test_coverage_ranges_include_provider_counts(self) -> None:
        ranges = coverage_ranges(
            [node(0, 6, "first"), node(0, 6, "replica")],
            12,
        )
        self.assertEqual(
            ranges,
            [
                {"layer_start": 0, "layer_end": 6, "provider_count": 2, "status": "redundant"},
                {"layer_start": 6, "layer_end": 12, "provider_count": 0, "status": "missing"},
            ],
        )

    def test_revision_changes_with_provider_snapshot(self) -> None:
        first = build_serving_plan(
            model_name="test/model", nodes=[], total_layers=12, layer_count=6
        )
        second = build_serving_plan(
            model_name="test/model",
            nodes=[node(0, 6, "first")],
            total_layers=12,
            layer_count=6,
        )
        self.assertNotEqual(first["coverage_revision"], second["coverage_revision"])


class ServingPlanApiTests(unittest.TestCase):
    def test_stale_coverage_revision_returns_fresh_plan(self) -> None:
        from api import server as api_server

        fresh_plan = build_serving_plan(
            model_name="facebook/opt-125m",
            nodes=[],
            total_layers=12,
            layer_count=6,
        )
        request = api_server.NodeStartRequest(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=6,
            device="cpu",
            coverage_revision="stale",
        )
        with patch.object(api_server, "_get_serving_plan", return_value=fresh_plan):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(api_server.start_node(request))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["error"], "serving_plan_changed")
        self.assertEqual(raised.exception.detail["serving_plan"], fresh_plan)

    def test_redundant_custom_range_requires_confirmation_while_gap_exists(self) -> None:
        from api import server as api_server

        fresh_plan = build_serving_plan(
            model_name="facebook/opt-125m",
            nodes=[node(0, 6, "existing")],
            total_layers=12,
            layer_count=6,
        )
        request = api_server.NodeStartRequest(
            model_name="facebook/opt-125m",
            layer_start=0,
            layer_end=6,
            device="cpu",
            coverage_revision=fresh_plan["coverage_revision"],
        )
        with patch.object(api_server, "_get_serving_plan", return_value=fresh_plan):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(api_server.start_node(request))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(
            raised.exception.detail["error"],
            "redundant_range_confirmation_required",
        )


if __name__ == "__main__":
    unittest.main()
