import asyncio
import concurrent.futures
import sys
import tempfile
import threading
import unittest
from pathlib import Path

import httpx

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from placement.service import (
    JoiningMutation,
    OnlineMutation,
    PlacementConflict,
    PlacementStore,
    ReleaseMutation,
    RenewMutation,
    ReservationRequest,
    create_placement_app,
)


AUTH_TOKEN = "placement-auth-token-for-tests-000000000000"
TOKEN_SECRET = "placement-token-secret-for-tests-0000000000"
MODEL_NAME = "facebook/opt-125m"
MODEL_REVISION = "model-revision-test"


class FakeClock:
    def __init__(self, initial: float = 1_700_000_000.0) -> None:
        self._value = initial
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._value += seconds


def request_asgi(app, method: str, path: str, **kwargs):
    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(request())


class TransactionalPlacementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "placement.sqlite3"
        self.clock = FakeClock()
        self.store = PlacementStore(
            self.database_path,
            token_secret=TOKEN_SECRET,
            startup_ttl_seconds=10,
            online_ttl_seconds=30,
            clock=self.clock,
        )

    @staticmethod
    def request(
        participant_id: str,
        idempotency_key: str,
        *,
        placement_mode: str = "recommended",
        layer_start: int | None = None,
        layer_end: int | None = None,
        expected_topology_revision: int | None = None,
    ) -> ReservationRequest:
        return ReservationRequest(
            participant_id=participant_id,
            idempotency_key=idempotency_key,
            model_name=MODEL_NAME,
            model_revision=MODEL_REVISION,
            layer_capacity=6,
            placement_mode=placement_mode,
            layer_start=layer_start,
            layer_end=layer_end,
            expected_topology_revision=expected_topology_revision,
        )

    def reserve(self, participant_id: str, key: str) -> dict:
        return self.store.reserve(self.request(participant_id, key))

    @staticmethod
    def mutation_fields(result: dict) -> dict[str, str]:
        reservation = result["reservation"]
        return {
            "participant_id": reservation["participant_id"],
            "reservation_token": reservation["reservation_token"],
        }

    def make_online(self, result: dict, suffix: str) -> dict:
        reservation = result["reservation"]
        fields = self.mutation_fields(result)
        node_id = f"node-{suffix}"
        peer_id = f"peer-{suffix}"
        rpc_uid = f"rpc-{suffix}"
        self.store.mark_joining(
            reservation["reservation_id"],
            JoiningMutation(**fields, node_id=node_id),
        )
        return self.store.mark_online(
            reservation["reservation_id"],
            OnlineMutation(
                **fields,
                node_id=node_id,
                peer_id=peer_id,
                rpc_uid=rpc_uid,
                advertised_peer_id=peer_id,
                advertised_rpc_uid=rpc_uid,
                model_revision=MODEL_REVISION,
                advertised_model_revision=MODEL_REVISION,
                rpc_ready=True,
                publication_ready=True,
            ),
        )

    def test_concurrent_six_layer_requests_are_atomic_and_complementary(self) -> None:
        barrier = threading.Barrier(2)

        def allocate(participant_id: str) -> dict:
            barrier.wait(timeout=2)
            return self.reserve(participant_id, f"request-{participant_id}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(allocate, ("device-one", "device-two")))

        ranges = {
            (result["reservation"]["layer_start"], result["reservation"]["layer_end"])
            for result in results
        }
        self.assertEqual(ranges, {(0, 6), (6, 12)})
        self.assertEqual(len({result["topology_revision"] for result in results}), 2)

    def test_sequential_online_reservations_complete_the_route(self) -> None:
        first = self.reserve("device-one", "request-one")
        second = self.reserve("device-two", "request-two")
        self.make_online(first, "one")
        self.make_online(second, "two")

        plan = self.store.plan(MODEL_NAME, MODEL_REVISION, 6)

        self.assertIsNone(plan["recommendation"])
        self.assertTrue(plan["online_plan"]["current_runnable"])
        self.assertEqual(plan["online_plan"]["missing_ranges"], [])

    def test_released_and_expired_starts_make_the_range_allocatable(self) -> None:
        released = self.reserve("device-one", "release-me")
        fields = self.mutation_fields(released)
        self.store.release(
            released["reservation"]["reservation_id"],
            ReleaseMutation(**fields, reason="load_failed"),
        )
        replacement = self.reserve("device-two", "after-release")
        self.assertEqual(
            (
                replacement["reservation"]["layer_start"],
                replacement["reservation"]["layer_end"],
            ),
            (0, 6),
        )

        self.clock.advance(11)
        expired_replacement = self.reserve("device-three", "after-expiry")
        self.assertEqual(
            (
                expired_replacement["reservation"]["layer_start"],
                expired_replacement["reservation"]["layer_end"],
            ),
            (0, 6),
        )
        participant = self.store.participant_reservations("device-two")
        self.assertEqual(participant["reservations"][0]["state"], "EXPIRED")

    def test_create_mutations_and_release_are_idempotent(self) -> None:
        original = self.reserve("device-one", "stable-request")
        replay = self.reserve("device-one", "stable-request")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(
            original["reservation"]["reservation_id"],
            replay["reservation"]["reservation_id"],
        )
        self.assertEqual(
            original["reservation"]["reservation_token"],
            replay["reservation"]["reservation_token"],
        )

        fields = self.mutation_fields(original)
        reservation_id = original["reservation"]["reservation_id"]
        joining = JoiningMutation(**fields, node_id="node-one")
        self.assertFalse(
            self.store.mark_joining(reservation_id, joining)["idempotent_replay"]
        )
        self.assertTrue(
            self.store.mark_joining(reservation_id, joining)["idempotent_replay"]
        )

        online = OnlineMutation(
            **fields,
            node_id="node-one",
            peer_id="peer-one",
            rpc_uid="rpc-one",
            advertised_peer_id="peer-one",
            advertised_rpc_uid="rpc-one",
            model_revision=MODEL_REVISION,
            advertised_model_revision=MODEL_REVISION,
            rpc_ready=True,
            publication_ready=True,
        )
        self.assertFalse(
            self.store.mark_online(reservation_id, online)["idempotent_replay"]
        )
        self.assertTrue(
            self.store.mark_online(reservation_id, online)["idempotent_replay"]
        )
        renewal = RenewMutation(**fields, peer_id="peer-one", rpc_uid="rpc-one")
        renewed = self.store.renew(reservation_id, renewal)
        self.assertEqual(renewed["reservation"]["state"], "ONLINE")

        release = ReleaseMutation(**fields, reason="operator_stop")
        self.assertFalse(
            self.store.release(reservation_id, release)["idempotent_replay"]
        )
        self.assertTrue(
            self.store.release(reservation_id, release)["idempotent_replay"]
        )

    def test_stale_revision_and_custom_overlap_cannot_commit(self) -> None:
        plan = self.store.plan(MODEL_NAME, MODEL_REVISION, 6)
        first = self.store.reserve(
            self.request(
                "device-one",
                "revision-owner",
                expected_topology_revision=plan["topology_revision"],
            )
        )
        with self.assertRaisesRegex(PlacementConflict, "topology changed"):
            self.store.reserve(
                self.request(
                    "device-two",
                    "stale-request",
                    expected_topology_revision=plan["topology_revision"],
                )
            )
        with self.assertRaisesRegex(PlacementConflict, "overlaps"):
            self.store.reserve(
                self.request(
                    "device-two",
                    "custom-overlap",
                    placement_mode="custom",
                    layer_start=0,
                    layer_end=6,
                )
            )
        self.assertEqual(first["reservation"]["state"], "RESERVED")

    def test_coordinator_restart_recovers_same_owner_without_duplicate(self) -> None:
        original = self.reserve("device-one", "restart-safe")
        restarted = PlacementStore(
            self.database_path,
            token_secret=TOKEN_SECRET,
            startup_ttl_seconds=10,
            online_ttl_seconds=30,
            clock=self.clock,
        )

        replay = restarted.reserve(self.request("device-one", "restart-safe"))
        reservations = restarted.plan(MODEL_NAME, MODEL_REVISION, 6)["reservations"]

        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(reservations), 1)
        self.assertEqual(
            replay["reservation"]["reservation_id"],
            original["reservation"]["reservation_id"],
        )
        self.assertEqual(
            replay["reservation"]["reservation_token"],
            original["reservation"]["reservation_token"],
        )

    def test_online_requires_exact_peer_rpc_and_readiness_attestation(self) -> None:
        result = self.reserve("device-one", "readiness-check")
        reservation_id = result["reservation"]["reservation_id"]
        fields = self.mutation_fields(result)
        self.store.mark_joining(
            reservation_id,
            JoiningMutation(**fields, node_id="node-one"),
        )

        with self.assertRaisesRegex(Exception, "advertised peer"):
            self.store.mark_online(
                reservation_id,
                OnlineMutation(
                    **fields,
                    node_id="node-one",
                    peer_id="peer-one",
                    rpc_uid="rpc-one",
                    advertised_peer_id="another-peer",
                    advertised_rpc_uid="rpc-one",
                    model_revision=MODEL_REVISION,
                    advertised_model_revision=MODEL_REVISION,
                    rpc_ready=True,
                    publication_ready=True,
                ),
            )
        with self.assertRaisesRegex(Exception, "both RPC readiness"):
            self.store.mark_online(
                reservation_id,
                OnlineMutation(
                    **fields,
                    node_id="node-one",
                    peer_id="peer-one",
                    rpc_uid="rpc-one",
                    advertised_peer_id="peer-one",
                    advertised_rpc_uid="rpc-one",
                    model_revision=MODEL_REVISION,
                    advertised_model_revision=MODEL_REVISION,
                    rpc_ready=False,
                    publication_ready=True,
                ),
            )
        with self.assertRaisesRegex(Exception, "model revision"):
            self.store.mark_online(
                reservation_id,
                OnlineMutation(
                    **fields,
                    node_id="node-one",
                    peer_id="peer-one",
                    rpc_uid="rpc-one",
                    advertised_peer_id="peer-one",
                    advertised_rpc_uid="rpc-one",
                    model_revision="another-revision",
                    advertised_model_revision="another-revision",
                    rpc_ready=True,
                    publication_ready=True,
                ),
            )

    def test_http_mutations_require_auth_and_audit_never_contains_tokens(self) -> None:
        app = create_placement_app(
            self.database_path,
            auth_token=AUTH_TOKEN,
            token_secret=TOKEN_SECRET,
            startup_ttl_seconds=10,
            online_ttl_seconds=30,
            clock=self.clock,
        )
        payload = self.request("device-one", "api-request").model_dump()
        unauthorized = request_asgi(app, "POST", "/v1/reservations", json=payload)
        self.assertEqual(unauthorized.status_code, 401)

        authorized = request_asgi(
            app,
            "POST",
            "/v1/reservations",
            json=payload,
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(authorized.status_code, 200)
        token = authorized.json()["reservation"]["reservation_token"]
        audit = request_asgi(
            app,
            "GET",
            "/v1/audit",
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(audit.status_code, 200)
        self.assertNotIn(token, audit.text)
        self.assertNotIn(AUTH_TOKEN, audit.text)
        self.assertNotIn("details_json", audit.json()["events"][0])

        plan = request_asgi(
            app,
            "GET",
            "/v1/models/facebook%2Fopt-125m/plan",
            params={"model_revision": MODEL_REVISION, "layer_capacity": 6},
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
        )
        self.assertEqual(plan.status_code, 200)
        self.assertNotIn("idempotency_key", plan.text)

    def test_health_binds_coordinator_protocol_and_deployment(self) -> None:
        app = create_placement_app(
            self.database_path,
            auth_token=AUTH_TOKEN,
            token_secret=TOKEN_SECRET,
            deployment_commit="abc1234",
            failure_domain="provider-one",
        )

        response = request_asgi(app, "GET", "/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["component_role"], "coordinator")
        self.assertEqual(response.json()["service_protocol_version"], 1)
        self.assertEqual(response.json()["deployment_commit"], "abc1234")
        self.assertEqual(response.json()["failure_domain"], "provider-one")


if __name__ == "__main__":
    unittest.main()
