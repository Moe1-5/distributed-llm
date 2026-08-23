import json
import sys
import threading
import time
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from placement.client import (
    PlacementClientError,
    PlacementConfig,
    PlacementCoordinatorClient,
    PlacementRuntime,
)


class FakeResponse:
    def __init__(self, document: dict) -> None:
        self.document = document

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.document).encode("utf-8")


class FakeCoordinatorClient:
    def __init__(self) -> None:
        self.online_payload: dict | None = None
        self.renewed = 0
        self.released = 0

    def joining(self, reservation: dict, node_id: str) -> dict:
        return {
            "reservation": {
                **reservation,
                "state": "JOINING",
                "node_id": node_id,
            }
        }

    def online(self, reservation: dict, **payload) -> dict:
        self.online_payload = payload
        return {
            "reservation": {
                **reservation,
                **payload,
                "state": "ONLINE",
            }
        }

    def renew(self, reservation: dict) -> dict:
        self.renewed += 1
        return {"reservation": dict(reservation)}

    def release(self, reservation: dict, reason: str) -> dict:
        self.released += 1
        return {"reservation": {**reservation, "state": "OFFLINE"}}


class PlacementClientTests(unittest.TestCase):
    def test_runtime_rejects_malformed_coordinator_reservation(self) -> None:
        class MalformedClient(FakeCoordinatorClient):
            def joining(self, reservation: dict, node_id: str) -> dict:
                return {"reservation": {"state": "JOINING"}}

        config = PlacementConfig(
            base_url="https://placement.example.test",
            auth_token="auth-token-0000000000000000000000000000",
            model_revision="revision-one",
        )
        runtime = PlacementRuntime(config, client=MalformedClient())
        self.addCleanup(runtime.shutdown)

        with self.assertRaisesRegex(PlacementClientError, "reservation_id"):
            runtime.begin_joining(
                {
                    "reservation_token": "reservation-secret-0000000000000000000",
                    "participant_id": "participant",
                    "state": "RESERVED",
                },
                "node-one",
            )

    def test_transient_outage_stops_worker_before_server_lease_expiry(self) -> None:
        class UnavailableClient(FakeCoordinatorClient):
            def renew(self, reservation: dict) -> dict:
                raise PlacementClientError(
                    "placement_unavailable",
                    "coordinator unavailable",
                )

        config = PlacementConfig(
            base_url="https://placement.example.test",
            auth_token="auth-token-0000000000000000000000000000",
            model_revision="revision-one",
            heartbeat_interval_seconds=0.05,
        )
        runtime = PlacementRuntime(config, client=UnavailableClient())
        self.addCleanup(runtime.shutdown)
        revoked = threading.Event()
        observed = {}
        runtime.set_lease_lost_callback(
            lambda node_id, error: (
                observed.update(node_id=node_id, error=error.code),
                revoked.set(),
            )
        )
        now = time.time()
        runtime.begin_joining(
            {
                "reservation_id": "reservation-expiring",
                "reservation_token": "reservation-secret-0000000000000000000",
                "participant_id": "participant",
                "state": "RESERVED",
                "updated_at": now,
                "expires_at": now + 0.25,
            },
            "node-expiring",
        )

        self.assertTrue(revoked.wait(1))
        self.assertEqual(
            observed,
            {
                "node_id": "node-expiring",
                "error": "placement_lease_deadline_elapsed",
            },
        )
        self.assertEqual(runtime.status()["active_leases"], [])

    def test_non_transient_renewal_rejection_revokes_local_ownership(self) -> None:
        class RejectingClient(FakeCoordinatorClient):
            def renew(self, reservation: dict) -> dict:
                raise PlacementClientError(
                    "reservation_not_active",
                    "lease expired",
                    status_code=409,
                )

        config = PlacementConfig(
            base_url="https://placement.example.test",
            auth_token="auth-token-0000000000000000000000000000",
            model_revision="revision-one",
            heartbeat_interval_seconds=60,
        )
        runtime = PlacementRuntime(config, client=RejectingClient())
        self.addCleanup(runtime.shutdown)
        revoked = threading.Event()
        observed = {}
        runtime.set_lease_lost_callback(
            lambda node_id, error: (
                observed.update(node_id=node_id, error=error.code),
                revoked.set(),
            )
        )
        runtime.begin_joining(
            {
                "reservation_id": "reservation-one",
                "reservation_token": "reservation-secret-0000000000000000000",
                "participant_id": "participant",
                "state": "RESERVED",
            },
            "node-one",
        )

        self.assertTrue(revoked.wait(2))
        self.assertEqual(observed, {"node_id": "node-one", "error": "reservation_not_active"})
        self.assertEqual(runtime.status()["active_leases"], [])

    def test_http_client_sends_bearer_auth_and_model_revision(self) -> None:
        captured = {}

        def opener(request, timeout):
            captured["authorization"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(
                {
                    "topology_revision": 1,
                    "reservation": {"reservation_id": "one"},
                }
            )

        config = PlacementConfig(
            base_url="https://placement.example.test",
            auth_token="auth-token-0000000000000000000000000000",
            model_revision="revision-one",
            request_timeout_seconds=3,
        )
        client = PlacementCoordinatorClient(config, opener=opener)

        client.reserve(
            participant_id="participant",
            idempotency_key="request-one",
            model_name="facebook/opt-125m",
            layer_capacity=6,
            placement_mode="recommended",
            layer_start=None,
            layer_end=None,
            expected_topology_revision=0,
        )

        self.assertEqual(
            captured["authorization"],
            f"Bearer {config.auth_token}",
        )
        self.assertEqual(captured["timeout"], 3)
        self.assertEqual(captured["payload"]["model_revision"], "revision-one")
        self.assertNotIn("reservation_token", captured["payload"])

    def test_runtime_attests_exact_publication_and_hides_lease_secret(self) -> None:
        config = PlacementConfig(
            base_url="https://placement.example.test",
            auth_token="auth-token-0000000000000000000000000000",
            model_revision="revision-one",
            heartbeat_interval_seconds=60,
        )
        client = FakeCoordinatorClient()
        runtime = PlacementRuntime(config, client=client)
        self.addCleanup(runtime.shutdown)
        reservation = {
            "reservation_id": "reservation-one",
            "reservation_token": "reservation-secret-0000000000000000000",
            "participant_id": "participant",
            "state": "RESERVED",
            "model_revision": "revision-one",
        }
        runtime.begin_joining(reservation, "node-one")
        rpc_uid = "distribllm-peer.0.0.6.node-one"

        public = runtime.mark_online(
            "node-one",
            {
                "peer_id": "peer-one",
                "rpc_peer_id": "peer-one",
                "rpc_uid": rpc_uid,
                "rpc_running": True,
                "placement_model_revision": "revision-one",
                "announcement": {"fresh": True},
                "rpc_publication": {"fresh": True, "uids": [rpc_uid]},
            },
        )

        self.assertEqual(client.online_payload["advertised_rpc_uid"], rpc_uid)
        self.assertEqual(client.online_payload["model_revision"], "revision-one")
        self.assertTrue(client.online_payload["publication_ready"])
        self.assertNotIn("reservation_token", public)
        self.assertNotIn("reservation-secret", json.dumps(runtime.status()))


if __name__ == "__main__":
    unittest.main()
