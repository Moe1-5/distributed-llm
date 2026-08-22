from __future__ import annotations

import sys
import threading
import unittest
from concurrent.futures import Future, TimeoutError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from client.coverage import select_route
from client.health import (
    ProviderHealthConfig,
    ProviderHealthMonitor,
    ProviderHealthRegistry,
    ProviderKey,
)
from client.sequential import RemoteSequential


def provider(peer_id: str, *, rpc_uid: str | None = None) -> dict:
    return {
        "peer_id": peer_id,
        "rpc_uid": rpc_uid or f"rpc.{peer_id}",
        "model_name": "test/model",
        "model_revision": "revision-1",
        "layer_start": 0,
        "layer_end": 4,
        "running": True,
        "layers_loaded": True,
        "rpc_running": True,
        "transport_verified": True,
        "timestamp": 100.0,
    }


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ProviderHealthStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = ProviderHealthConfig(
            selected_interval_seconds=5,
            standby_interval_seconds=15,
            probe_timeout_seconds=3,
            failure_threshold=2,
            offline_threshold=4,
            recovery_successes=2,
            dht_stale_seconds=10,
            max_concurrency=2,
            jitter_ratio=0,
            scheduler_interval_seconds=10,
        )
        self.registry = ProviderHealthRegistry(self.config)
        self.node = provider("peer-a")
        self.key = ProviderKey.from_node(self.node)
        self.registry.observe([self.node], {self.key}, now=100)

    def test_failure_thresholds_and_recovery_avoid_route_flapping(self) -> None:
        self.registry.record_success(self.key, now=101, latency_ms=12)
        self.assertEqual(self.registry.get(self.key)["state"], "healthy")

        self.registry.record_failure(self.key, now=102, reason="one transient failure")
        self.assertEqual(self.registry.get(self.key)["state"], "healthy")
        self.registry.record_failure(self.key, now=103, reason="second failure")
        self.assertEqual(self.registry.get(self.key)["state"], "degraded")
        self.registry.record_failure(self.key, now=104, reason="third failure")
        self.registry.record_failure(self.key, now=105, reason="fourth failure")
        self.assertEqual(self.registry.get(self.key)["state"], "offline")

        self.registry.record_success(self.key, now=106, latency_ms=10)
        recovering = self.registry.get(self.key)
        self.assertEqual(recovering["state"], "offline")
        self.assertIn("recovery pending", recovering["reason"])
        self.registry.record_success(self.key, now=107, latency_ms=9)
        self.assertEqual(self.registry.get(self.key)["state"], "healthy")

    def test_dht_expiry_is_distinct_from_rpc_failure(self) -> None:
        self.registry.record_success(self.key, now=100, latency_ms=4)
        self.registry.observe([], set(), now=105)
        present_grace = self.registry.get(self.key)
        self.assertFalse(present_grace["dht_present"])
        self.assertEqual(present_grace["state"], "healthy")

        self.registry.observe([], set(), now=111)
        expired = self.registry.get(self.key)
        self.assertEqual(expired["state"], "offline")
        self.assertIn("DHT advertisement", expired["reason"])

    def test_protocol_incompatibility_has_its_own_signal(self) -> None:
        self.registry.record_protocol_failure(
            peer_id="peer-b",
            model_name="test/model",
            reason="missing rpc_uid",
            now=100,
        )
        incompatible = next(
            item for item in self.registry.snapshot()["providers"] if item["peer_id"] == "peer-b"
        )
        self.assertFalse(incompatible["protocol_compatible"])
        self.assertEqual(incompatible["state"], "offline")
        self.assertIn("Protocol-incompatible", incompatible["reason"])

    def test_health_revision_changes_only_when_route_state_changes(self) -> None:
        initial = self.registry.snapshot()["health_revision"]
        self.registry.record_success(self.key, now=101, latency_ms=10)
        healthy = self.registry.snapshot()["health_revision"]
        self.assertNotEqual(initial, healthy)
        self.registry.record_success(self.key, now=102, latency_ms=8)
        self.assertEqual(healthy, self.registry.snapshot()["health_revision"])


class ProviderHealthMonitorTests(unittest.TestCase):
    def config(self, **overrides) -> ProviderHealthConfig:
        values = {
            "selected_interval_seconds": 5,
            "standby_interval_seconds": 15,
            "probe_timeout_seconds": 3,
            "failure_threshold": 2,
            "offline_threshold": 4,
            "recovery_successes": 2,
            "dht_stale_seconds": 10,
            "max_concurrency": 2,
            "jitter_ratio": 0,
            "scheduler_interval_seconds": 10,
        }
        values.update(overrides)
        return ProviderHealthConfig(**values)

    def test_selected_and_standby_scheduling_use_different_intervals(self) -> None:
        clock = FakeClock()
        nodes = [provider("peer-a"), provider("peer-b")]
        config = self.config()
        registry = ProviderHealthRegistry(config)
        probed: list[str] = []
        monitor = ProviderHealthMonitor(
            config=config,
            registry=registry,
            discover=lambda: (nodes, []),
            classify=lambda found: select_route(found, 4),
            probe=lambda node: probed.append(node["peer_id"]),
            clock=clock,
            jitter=lambda _start, _end: 0,
        )

        monitor.start()
        self.assertTrue(monitor.wait_for_idle())
        records = {item["peer_id"]: item for item in registry.snapshot()["providers"]}
        self.assertEqual(set(probed), {"peer-a", "peer-b"})
        self.assertEqual(records["peer-a"]["role"], "selected")
        self.assertEqual(records["peer-a"]["next_probe_at"], 105)
        self.assertEqual(records["peer-b"]["role"], "standby")
        self.assertEqual(records["peer-b"]["next_probe_at"], 115)
        self.assertTrue(monitor.stop())
        self.assertFalse(monitor.running)

    def test_hung_probe_consumes_one_bounded_slot_and_records_one_timeout(self) -> None:
        clock = FakeClock()
        nodes = [provider("peer-a"), provider("peer-b")]
        release = threading.Event()
        calls: list[str] = []
        config = self.config(max_concurrency=1)
        registry = ProviderHealthRegistry(config)

        def blocking_probe(node: dict) -> None:
            calls.append(node["peer_id"])
            release.wait(1)

        monitor = ProviderHealthMonitor(
            config=config,
            registry=registry,
            discover=lambda: (nodes, []),
            classify=lambda found: select_route(found, 4),
            probe=blocking_probe,
            clock=clock,
            jitter=lambda _start, _end: 0,
        )
        monitor.start()
        clock.advance(3.1)
        monitor.run_cycle()
        clock.advance(3.1)
        monitor.run_cycle()

        peer_a = registry.get(ProviderKey.from_node(nodes[0]))
        self.assertEqual(calls, ["peer-a"])
        snapshot = monitor.snapshot()
        self.assertEqual(snapshot["active_probes"], 1)
        self.assertEqual(snapshot["active_probe_details"][0]["peer_id"], "peer-a")
        self.assertEqual(snapshot["active_probe_details"][0]["rpc_uid"], "rpc.peer-a")
        self.assertTrue(snapshot["active_probe_details"][0]["thread_alive"])
        self.assertTrue(snapshot["active_probe_details"][0]["timeout_recorded"])
        self.assertEqual(peer_a["state"], "checking")
        self.assertEqual(peer_a["consecutive_failures"], 1)
        self.assertIn("exceeded 3 seconds", peer_a["reason"])

        clock.advance(6.0)
        monitor.run_cycle()
        peer_a = registry.get(ProviderKey.from_node(nodes[0]))
        self.assertEqual(peer_a["state"], "offline")
        self.assertEqual(peer_a["consecutive_failures"], 1)
        self.assertIn("remained stuck for 12 seconds", peer_a["reason"])

        release.set()
        self.assertTrue(monitor.wait_for_idle())
        self.assertTrue(monitor.stop())

    def test_scheduler_ticks_do_not_rescan_dht_before_discovery_interval(self) -> None:
        clock = FakeClock()
        nodes = [provider("peer-a")]
        discoveries = 0
        config = self.config(discovery_interval_seconds=2)

        def discover():
            nonlocal discoveries
            discoveries += 1
            return nodes, []

        monitor = ProviderHealthMonitor(
            config=config,
            registry=ProviderHealthRegistry(config),
            discover=discover,
            classify=lambda found: select_route(found, 4),
            probe=lambda _node: None,
            clock=clock,
            jitter=lambda _start, _end: 0,
        )
        monitor.start()
        self.assertTrue(monitor.wait_for_idle())
        clock.advance(0.5)
        monitor.run_cycle()
        clock.advance(0.5)
        monitor.run_cycle()
        self.assertEqual(discoveries, 1)
        clock.advance(1.1)
        monitor.run_cycle()
        self.assertEqual(discoveries, 2)
        self.assertTrue(monitor.stop())


class HealthReadinessIntegrationTests(unittest.TestCase):
    def test_degraded_selected_provider_yields_to_healthy_replica(self) -> None:
        class DHT:
            pass

        config = ProviderHealthConfig(
            selected_interval_seconds=5,
            standby_interval_seconds=15,
            probe_timeout_seconds=3,
            failure_threshold=2,
            offline_threshold=4,
            recovery_successes=2,
            dht_stale_seconds=10,
            max_concurrency=2,
            jitter_ratio=0,
            scheduler_interval_seconds=10,
        )
        sequential = RemoteSequential(
            DHT(),
            "test-prefix",
            num_layers=4,
            model_name="test/model",
            health_config=config,
        )
        nodes = [provider("peer-a"), provider("peer-b")]
        selected, _ = select_route(nodes, 4)
        selected_keys = {ProviderKey.from_node(node) for node in selected}
        sequential.health_registry.observe(nodes, selected_keys, now=100)
        for node in nodes:
            sequential.health_registry.record_success(
                ProviderKey.from_node(node), now=101, latency_ms=5
            )

        class Monitor:
            running = True

            def latest_nodes(self):
                return nodes

            def snapshot(self):
                return {
                    **sequential.health_registry.snapshot(),
                    "monitor_running": True,
                    "active_probes": 0,
                    "last_discovery_error": None,
                    "detection_window_seconds": config.detection_window_seconds,
                }

        sequential.health_monitor = Monitor()
        self.assertTrue(sequential.get_health_readiness()["route_ready"])

        selected_key = ProviderKey.from_node(selected[0])
        sequential.health_registry.record_failure(selected_key, now=102, reason="reset")
        self.assertTrue(sequential.get_health_readiness()["route_ready"])
        sequential.health_registry.record_failure(selected_key, now=103, reason="reset")
        degraded = sequential.get_health_readiness()
        self.assertTrue(degraded["route_ready"])
        self.assertEqual(degraded["selected_route"][0]["peer_id"], "peer-b")
        self.assertEqual(degraded["alternate_routes"][0]["route"][0]["peer_id"], "peer-a")

        sequential.health_registry.record_success(selected_key, now=104, latency_ms=5)
        self.assertEqual(
            sequential.get_health_readiness()["selected_route"][0]["peer_id"],
            "peer-b",
        )
        sequential.health_registry.record_success(selected_key, now=105, latency_ms=5)
        self.assertTrue(sequential.get_health_readiness()["route_ready"])

    def test_metadata_scan_reports_protocol_error_separately(self) -> None:
        class Result:
            def __init__(self, value):
                self.value = value

        class DHT:
            def get(self, key: str, latest: bool = True):
                if key.endswith(".members"):
                    return Result(["bad-peer"])
                return Result({"peer_id": "bad-peer", "model_name": "test/model"})

        sequential = RemoteSequential(
            DHT(), "test-prefix", num_layers=4, model_name="test/model"
        )
        nodes, errors = sequential._scan_node_metadata()
        self.assertEqual(nodes, [])
        self.assertEqual(errors[0]["kind"], "protocol_incompatible")
        self.assertIn("missing fields", errors[0]["reason"])

    def test_production_probe_uses_synchronous_lookup_without_an_event_loop(self) -> None:
        class DHT:
            pass

        config = ProviderHealthConfig(
            selected_interval_seconds=0.1,
            standby_interval_seconds=0.1,
            probe_timeout_seconds=0.1,
            failure_threshold=2,
            offline_threshold=4,
            recovery_successes=2,
            dht_stale_seconds=1,
            max_concurrency=1,
            jitter_ratio=0,
            scheduler_interval_seconds=0.05,
        )
        sequential = RemoteSequential(
            DHT(), "test-prefix", 4, "test/model", health_config=config
        )
        expert = SimpleNamespace(
            uid="rpc.peer-a",
            stub=SimpleNamespace(rpc_info=lambda _request: None),
        )
        rpc_info = Future()
        rpc_info.set_result(SimpleNamespace())
        errors: list[BaseException] = []

        def run_probe() -> None:
            try:
                sequential._probe_provider(provider("peer-a"))
            except BaseException as exc:
                errors.append(exc)

        with (
            patch("client.sequential.get_peer_expert", return_value=expert) as lookup,
            patch.object(
                sys.modules["client.sequential"].RemoteExpertWorker,
                "run_coroutine",
                return_value=rpc_info,
            ),
        ):
            probe_thread = threading.Thread(target=run_probe)
            probe_thread.start()
            probe_thread.join(timeout=1)

        self.assertFalse(probe_thread.is_alive())
        self.assertEqual(errors, [])
        lookup.assert_called_once_with(sequential.dht, "rpc.peer-a", "peer-a")

    def test_production_probe_cancels_timed_out_rpc_info(self) -> None:
        class DHT:
            pass

        config = ProviderHealthConfig(
            selected_interval_seconds=0.1,
            standby_interval_seconds=0.1,
            probe_timeout_seconds=0.1,
            failure_threshold=2,
            offline_threshold=4,
            recovery_successes=2,
            dht_stale_seconds=1,
            max_concurrency=1,
            jitter_ratio=0,
            scheduler_interval_seconds=0.05,
        )
        sequential = RemoteSequential(
            DHT(), "test-prefix", 4, "test/model", health_config=config
        )
        expert = SimpleNamespace(
            uid="rpc.peer-a",
            stub=SimpleNamespace(rpc_info=lambda _request: None),
        )
        rpc_info = Future()
        with (
            patch("client.sequential.get_peer_expert", return_value=expert),
            patch.object(
                sys.modules["client.sequential"].RemoteExpertWorker,
                "run_coroutine",
                return_value=rpc_info,
            ),
        ):
            with self.assertRaises(TimeoutError):
                sequential._probe_provider(provider("peer-a"))
        self.assertTrue(rpc_info.cancelled())


class GeneratorHealthLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_unload_stops_generator_before_client_dht(self) -> None:
        from api import server as api_server

        events: list[str] = []

        class Generator:
            def request_stop(self) -> None:
                events.append("request_stop")

            def unload(self) -> None:
                events.append("unload")

        class DHT:
            _p2p_replica = None

            def shutdown(self) -> None:
                events.append("dht_shutdown")

        original_generator = api_server.generator
        original_dht = api_server.client_dht
        api_server.generator = Generator()
        api_server.client_dht = DHT()
        try:
            result = await api_server.unload_generator()
        finally:
            api_server.generator = original_generator
            api_server.client_dht = original_dht

        self.assertEqual(result["status"], "unloaded")
        self.assertEqual(events, ["request_stop", "unload", "dht_shutdown"])


if __name__ == "__main__":
    unittest.main()
