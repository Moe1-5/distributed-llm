import unittest

import torch

from client.coverage import plan_health_aware_routes
from client.failover import RouteAttemptError, RouteFailoverConfig
from client.sequential import RemoteSequential


def provider(
    peer_id: str,
    start: int,
    end: int,
    *,
    mode: str = "direct",
) -> dict:
    return {
        "peer_id": peer_id,
        "node_id": peer_id,
        "rpc_uid": f"rpc.{peer_id}.{start}.{end}",
        "model_name": "test/model",
        "model_revision": "revision-1",
        "layer_start": start,
        "layer_end": end,
        "layers_loaded": True,
        "rpc_running": True,
        "running": True,
        "connection_mode": mode,
        "transport_verified": True,
    }


def health_snapshot(nodes: list[dict], states: dict[str, str]) -> dict:
    return {
        "health_revision": "health-1",
        "providers": [
            {
                **node,
                "state": states[node["peer_id"]],
                "latency_ms": 5 if node["peer_id"].startswith("fast") else 20,
            }
            for node in nodes
        ],
    }


class HealthAwarePlannerTests(unittest.TestCase):
    def test_direct_complete_route_precedes_relay_and_exposes_alternate(self) -> None:
        nodes = [
            provider("direct-head", 0, 2),
            provider("direct-tail", 2, 4),
            provider("relay-full", 0, 4, mode="relay"),
        ]
        plan = plan_health_aware_routes(
            nodes,
            4,
            health_snapshot(nodes, {node["peer_id"]: "healthy" for node in nodes}),
        )
        self.assertEqual(
            [node["peer_id"] for node in plan["active"]["route"]],
            ["direct-head", "direct-tail"],
        )
        self.assertEqual(plan["active"]["transport"], "direct")
        self.assertEqual(plan["alternates"][0]["route"][0]["peer_id"], "relay-full")

    def test_healthy_replica_precedes_degraded_replica(self) -> None:
        nodes = [provider("degraded", 0, 4), provider("healthy", 0, 4)]
        plan = plan_health_aware_routes(
            nodes,
            4,
            health_snapshot(nodes, {"degraded": "degraded", "healthy": "healthy"}),
        )
        self.assertEqual(plan["active"]["route"][0]["peer_id"], "healthy")
        self.assertTrue(plan["alternates"][0]["degraded"])

    def test_offline_gap_is_reported_exactly(self) -> None:
        nodes = [provider("head", 0, 2), provider("tail", 2, 4)]
        plan = plan_health_aware_routes(
            nodes,
            4,
            health_snapshot(nodes, {"head": "healthy", "tail": "offline"}),
        )
        self.assertIsNone(plan["active"])
        self.assertEqual(plan["unavailable_ranges"], [{"start": 2, "end": 4}])

    def test_health_revision_invalidates_route_revision(self) -> None:
        nodes = [provider("worker", 0, 4)]
        first = health_snapshot(nodes, {"worker": "healthy"})
        second = {**first, "health_revision": "health-2"}
        self.assertNotEqual(
            plan_health_aware_routes(nodes, 4, first)["route_revision"],
            plan_health_aware_routes(nodes, 4, second)["route_revision"],
        )

    def test_dense_replicas_keep_only_bounded_deterministic_candidates(self) -> None:
        nodes = [
            provider(f"head-{index}", 0, 2) for index in range(20)
        ] + [
            provider(f"tail-{index}", 2, 4) for index in range(20)
        ]
        health = health_snapshot(
            nodes,
            {node["peer_id"]: "healthy" for node in nodes},
        )
        first = plan_health_aware_routes(nodes, 4, health, max_alternates=2)
        second = plan_health_aware_routes(nodes, 4, health, max_alternates=2)
        self.assertEqual(first, second)
        self.assertEqual(first["candidate_count"], 3)
        self.assertLessEqual(first["evaluated_candidates"], 80)


class RouteFailoverTests(unittest.TestCase):
    def sequential(self, nodes: list[dict], *, max_attempts: int = 2) -> RemoteSequential:
        instance = RemoteSequential(
            type("DHT", (), {"peer_id": "generator"})(),
            "test",
            4,
            "test/model",
            failover_config=RouteFailoverConfig(
                max_attempts=max_attempts,
                max_alternates=3,
                backoff_seconds=0,
                quarantine_seconds=30,
            ),
        )
        instance._discover_nodes = lambda: nodes
        return instance

    def test_safe_failure_restarts_complete_attempt_on_alternate(self) -> None:
        nodes = [
            provider("direct-head", 0, 2),
            provider("direct-tail", 2, 4),
            provider("relay-full", 0, 4, mode="relay"),
        ]
        instance = self.sequential(nodes)
        calls: list[tuple[str, float]] = []

        def call_node(**kwargs):
            peer_id = kwargs["peer_id"]
            calls.append((peer_id, float(kwargs["hidden_states"].sum())))
            if peer_id == "direct-tail":
                raise RouteAttemptError(
                    "not found before execution",
                    failure_class="pre_execution_transport",
                    node=kwargs["node_info"],
                )
            return kwargs["hidden_states"] + 1

        instance._call_node = call_node
        output, trace = instance.forward(torch.zeros(1, 1, 2))
        self.assertEqual([peer for peer, _ in calls], ["direct-head", "direct-tail", "relay-full"])
        self.assertEqual(calls[-1][1], 0.0)
        self.assertTrue(torch.equal(output, torch.ones(1, 1, 2)))
        self.assertEqual(trace, ["relay-fu… (layers 0→4)"])
        self.assertTrue(instance.get_last_forward_metrics()["failed_over"])

    def test_ambiguous_failure_never_uses_alternate(self) -> None:
        nodes = [provider("a", 0, 4), provider("b", 0, 4)]
        instance = self.sequential(nodes)
        calls: list[str] = []

        def call_node(**kwargs):
            calls.append(kwargs["peer_id"])
            raise RouteAttemptError(
                "connection closed after dispatch",
                failure_class="ambiguous_transport",
                node=kwargs["node_info"],
            )

        instance._call_node = call_node
        with self.assertRaisesRegex(RuntimeError, "failover was suppressed"):
            instance.forward(torch.zeros(1, 1, 2))
        self.assertEqual(calls, ["a"])

    def test_failed_attempt_receipts_are_discarded(self) -> None:
        class Runtime:
            enabled = False
            identity = None

            def __init__(self) -> None:
                self.submissions: list[dict] = []

            def submit(self, submission: dict) -> bool:
                self.submissions.append(submission)
                return True

        nodes = [provider("a", 0, 4), provider("b", 0, 4)]
        runtime = Runtime()
        instance = self.sequential(nodes)
        instance.useful_work_runtime = runtime
        instance._receipt_route = lambda route: [{"route": route[0]["peer_id"]}]

        def call_node(**kwargs):
            kwargs["pending_receipts"].append({"peer_id": kwargs["peer_id"]})
            if kwargs["peer_id"] == "a":
                raise RouteAttemptError(
                    "not found before execution",
                    failure_class="pre_execution_transport",
                    node=kwargs["node_info"],
                )
            return kwargs["hidden_states"]

        instance._call_node = call_node
        instance.forward(torch.zeros(1, 1, 2))
        self.assertEqual(runtime.submissions, [{"peer_id": "b"}])

    def test_attempt_budget_prevents_retry_storm(self) -> None:
        nodes = [provider("a", 0, 4), provider("b", 0, 4), provider("c", 0, 4)]
        instance = self.sequential(nodes, max_attempts=2)
        calls: list[str] = []

        def call_node(**kwargs):
            calls.append(kwargs["peer_id"])
            raise RouteAttemptError(
                "not found before execution",
                failure_class="pre_execution_transport",
                node=kwargs["node_info"],
            )

        instance._call_node = call_node
        with self.assertRaisesRegex(RuntimeError, "within 2 attempt"):
            instance.forward(torch.zeros(1, 1, 2))
        self.assertEqual(calls, ["a", "b"])

    def test_unmonitored_session_retains_alternates_without_rediscovery(self) -> None:
        nodes = [provider("a", 0, 4), provider("b", 0, 4)]
        instance = self.sequential(nodes)
        discoveries = 0
        calls: list[str] = []

        def discover() -> list[dict]:
            nonlocal discoveries
            discoveries += 1
            return nodes

        def call_node(**kwargs):
            calls.append(kwargs["peer_id"])
            if len(calls) > 1 and kwargs["peer_id"] == "a":
                raise RouteAttemptError(
                    "not found before execution",
                    failure_class="pre_execution_transport",
                    node=kwargs["node_info"],
                )
            return kwargs["hidden_states"]

        instance._discover_nodes = discover
        instance._call_node = call_node
        instance.start_session("session")
        instance.forward(torch.zeros(1, 1, 2))
        instance.forward(torch.zeros(1, 1, 2))
        self.assertEqual(discoveries, 1)
        self.assertEqual(calls, ["a", "a", "b"])


if __name__ == "__main__":
    unittest.main()
