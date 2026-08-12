import concurrent.futures
import sqlite3
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

import torch
import torch.nn as nn
import hivemind
from fastapi.testclient import TestClient
from hivemind.moe import get_experts
from hivemind.moe.client.remote_expert_worker import RemoteExpertWorker

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from incentives.identity import load_application_identity
from incentives.protocol import (
    ProtocolError,
    canonical_json,
    decode_metadata_tensor,
    encode_metadata_tensor,
    hash_document,
    tensor_commitment,
    verify_signed_document,
)
from incentives.receipts import (
    accept_worker_receipt,
    build_submission,
    create_inference_request,
    create_worker_receipt,
    verify_inference_request,
)
from incentives.settlement import (
    SettlementStore,
    create_settlement_app,
    default_policy,
    validate_submission,
)
from incentives.config import IncentivesConfig
from incentives.runtime import UsefulWorkRuntime
from client.sequential import RemoteSequential
from node.rpc_server import RPCServer, _ReceiptHandlerModule


class ReceiptFixture:
    def __init__(
        self,
        root: Path,
        request_id: str = "request-1",
        identity_tag: str | None = None,
        peer_tag: str | None = None,
    ) -> None:
        self.now = int(time.time())
        identity_tag = identity_tag or request_id
        peer_tag = peer_tag or identity_tag
        self.generator = load_application_identity(root / f"generator-{identity_tag}.json")
        self.worker = load_application_identity(root / f"worker-{identity_tag}.json")
        self.generator_peer = f"generator-peer-{peer_tag}"
        self.worker_peer = f"worker-peer-{peer_tag}"
        self.route = [
            {
                "peer_id": self.worker_peer,
                "application_public_key": self.worker.public_key,
                "rpc_uid": "distribllm.receipt.0.12",
                "layer_start": 0,
                "layer_end": 12,
            }
        ]
        self.hidden = torch.arange(24, dtype=torch.bfloat16).reshape(1, 3, 8)
        self.response = self.hidden + 1
        self.request = create_inference_request(
            self.generator,
            generator_peer_id=self.generator_peer,
            session_id="session-1",
            model_name="facebook/opt-125m",
            model_revision="main",
            route=self.route,
            worker=self.route[0],
            hidden_states=self.hidden,
            position_count=3,
            request_id=request_id,
            timestamp=self.now,
        )
        request_payload = verify_inference_request(
            self.request,
            worker_public_key=self.worker.public_key,
            worker_peer_id=self.worker_peer,
            rpc_uid="distribllm.receipt.0.12",
            layer_start=0,
            layer_end=12,
            hidden_states=self.hidden,
        )
        self.receipt = create_worker_receipt(
            self.worker,
            request_payload,
            self.response,
            worker_peer_id=self.worker_peer,
            timestamp=self.now,
        )
        self.acceptance = accept_worker_receipt(
            self.generator,
            self.request,
            self.receipt,
            self.response,
            generator_peer_id=self.generator_peer,
            timestamp=self.now,
        )
        self.submission = build_submission(
            worker_receipt=self.receipt,
            generator_acceptance=self.acceptance,
            worker_presence=self.worker.presence(self.worker_peer, timestamp=self.now),
            generator_presence=self.generator.presence(
                self.generator_peer,
                timestamp=self.now,
            ),
            route=self.route,
        )


class IdentityAndProtocolTests(unittest.TestCase):
    def test_identity_persists_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            first = load_application_identity(path)
            second = load_application_identity(path)

            self.assertEqual(first.public_key, second.public_key)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertNotIn("private", first.__dict__ | {"public_key": first.public_key})

    def test_signed_canonical_document_rejects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identity = load_application_identity(Path(directory) / "identity.json")
            document = identity.sign({"document_type": "test", "b": 2, "a": 1})

            self.assertEqual(canonical_json({"b": 2, "a": 1}), b'{"a":1,"b":2}')
            self.assertEqual(verify_signed_document(document)["a"], 1)
            document["payload"]["a"] = 9
            with self.assertRaises(ProtocolError):
                verify_signed_document(document)

    def test_metadata_tensor_and_bfloat16_commitment_are_stable(self) -> None:
        document = {"hello": "world", "count": 2}
        tensor = torch.arange(12, dtype=torch.bfloat16).reshape(1, 3, 4)

        self.assertEqual(decode_metadata_tensor(encode_metadata_tensor(document)), document)
        self.assertEqual(tensor_commitment(tensor), tensor_commitment(tensor.clone()))
        self.assertNotEqual(tensor_commitment(tensor), tensor_commitment(tensor + 1))

    def test_generator_rejects_tampered_worker_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fixture = ReceiptFixture(Path(directory))

            with self.assertRaisesRegex(ProtocolError, "response commitment"):
                accept_worker_receipt(
                    fixture.generator,
                    fixture.request,
                    fixture.receipt,
                    fixture.response + 1,
                    generator_peer_id=fixture.generator_peer,
                    timestamp=fixture.now,
                )

    def test_receipt_rpc_wrapper_returns_verifiable_signed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = ReceiptFixture(root)

            class Handler:
                model_name = "facebook/opt-125m"
                layer_start = 0
                layer_end = 12

                def forward(self, hidden_states, **kwargs):
                    return hidden_states + 1

            module = _ReceiptHandlerModule(
                Handler(),
                fixture.worker,
                fixture.worker_peer,
                fixture.route[0]["rpc_uid"],
                "main",
            )

            output, metadata = module.forward(
                fixture.hidden,
                encode_metadata_tensor(fixture.request),
            )
            decoded = decode_metadata_tensor(metadata)

            self.assertTrue(torch.equal(output, fixture.response))
            self.assertEqual(
                verify_signed_document(
                    decoded["worker_receipt"],
                    expected_type="worker_receipt",
                )["request_id"],
                "request-1",
            )
            self.assertNotEqual(
                RPCServer.build_rpc_uid("distribllm", 0, 12),
                RPCServer.build_receipt_rpc_uid("distribllm", 0, 12),
            )

            malformed_request = dict(fixture.request)
            malformed_request["payload"] = {
                **fixture.request["payload"],
                "position_count": 3.5,
            }
            malformed_request = fixture.generator.sign(malformed_request["payload"])
            with self.assertRaisesRegex(ProtocolError, "positive integer"):
                verify_inference_request(
                    malformed_request,
                    worker_public_key=fixture.worker.public_key,
                    worker_peer_id=fixture.worker_peer,
                    rpc_uid=fixture.route[0]["rpc_uid"],
                    layer_start=0,
                    layer_end=12,
                    hidden_states=fixture.hidden,
                )

    def test_receipts_submit_only_after_every_route_hop_succeeds(self) -> None:
        class Runtime:
            def __init__(self) -> None:
                self.submissions = []

            def submit(self, submission):
                self.submissions.append(submission)
                return True

        nodes = [
            {
                "peer_id": "head-peer",
                "rpc_uid": "legacy.0.6",
                "layer_start": 0,
                "layer_end": 6,
            },
            {
                "peer_id": "tail-peer",
                "rpc_uid": "legacy.6.12",
                "layer_start": 6,
                "layer_end": 12,
            },
        ]

        def sequential(runtime: Runtime, fail_tail: bool) -> RemoteSequential:
            instance = RemoteSequential(
                type("DHT", (), {"peer_id": "generator-peer"})(),
                "distribllm",
                12,
                "facebook/opt-125m",
                useful_work_runtime=runtime,
            )
            instance._discover_nodes = lambda: nodes
            instance.validate_route = lambda discovered: nodes
            instance._receipt_route = lambda route: [{"route": "signed"}]

            def call_node(**kwargs):
                kwargs["pending_receipts"].append(
                    {"peer_id": kwargs["peer_id"]}
                )
                if fail_tail and kwargs["peer_id"] == "tail-peer":
                    raise RuntimeError("tail failed")
                return kwargs["hidden_states"]

            instance._call_node = call_node
            return instance

        failed_runtime = Runtime()
        with self.assertRaisesRegex(RuntimeError, "tail failed"):
            sequential(failed_runtime, True).forward(torch.zeros(1, 2, 8))
        self.assertEqual(failed_runtime.submissions, [])

        successful_runtime = Runtime()
        sequential(successful_runtime, False).forward(torch.zeros(1, 2, 8))
        self.assertEqual(
            successful_runtime.submissions,
            [{"peer_id": "head-peer"}, {"peer_id": "tail-peer"}],
        )

    def test_real_hivemind_receipt_rpc_settles_verified_work(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = load_application_identity(root / "rpc-worker.json")
            generator = load_application_identity(root / "rpc-generator.json")

            class Handler:
                model_name = "facebook/opt-125m"
                layer_start = 0
                layer_end = 12
                device = "cpu"
                layers = nn.ModuleList([nn.Linear(768, 768, bias=False)])

                def is_loaded(self) -> bool:
                    return True

                def forward(self, hidden_states, **kwargs):
                    return hidden_states + 1

            dht = hivemind.DHT(
                start=True,
                use_ipfs=False,
                host_maddrs=["/ip4/127.0.0.1/tcp/0"],
            )
            rpc = RPCServer(
                Handler(),
                dht,
                "receiptintegration",
                incentives_config=IncentivesConfig("shadow", "", "main"),
                application_identity=worker,
            )
            client_dht = None
            expert = None
            try:
                rpc.start()
                worker_peer = str(dht.peer_id)
                capability = rpc.get_receipt_capability(worker_peer)
                self.assertIsNotNone(capability)
                client_dht = hivemind.DHT(
                    start=True,
                    use_ipfs=False,
                    client_mode=True,
                    initial_peers=dht.get_visible_maddrs(),
                    host_maddrs=["/ip4/127.0.0.1/tcp/0"],
                )
                generator_peer = str(client_dht.peer_id)
                route = [
                    {
                        "peer_id": worker_peer,
                        "application_public_key": worker.public_key,
                        "rpc_uid": capability["receipt_rpc_uid"],
                        "layer_start": 0,
                        "layer_end": 12,
                    }
                ]
                hidden = torch.linspace(
                    -1,
                    1,
                    50 * 768,
                    dtype=torch.float32,
                ).reshape(1, 50, 768)
                self.assertGreater(
                    hidden.numel() * hidden.element_size(),
                    131072,
                )
                request = create_inference_request(
                    generator,
                    generator_peer_id=generator_peer,
                    session_id="integration-session",
                    model_name="facebook/opt-125m",
                    model_revision="main",
                    route=route,
                    worker=route[0],
                    hidden_states=hidden,
                    position_count=50,
                    request_id="integration-request",
                )
                expert = get_experts(
                    client_dht,
                    [capability["receipt_rpc_uid"]],
                )[0]
                self.assertIsNotNone(expert)

                output, metadata_tensor = expert.forward(
                    hidden,
                    encode_metadata_tensor(request),
                    attention_mask=torch.ones((1, 50), dtype=torch.bool),
                    position_ids=torch.arange(50).unsqueeze(0),
                )
                metadata = decode_metadata_tensor(metadata_tensor)
                acceptance = accept_worker_receipt(
                    generator,
                    request,
                    metadata["worker_receipt"],
                    output,
                    generator_peer_id=generator_peer,
                )
                submission = build_submission(
                    worker_receipt=metadata["worker_receipt"],
                    generator_acceptance=acceptance,
                    worker_presence=metadata["worker_presence"],
                    generator_presence=generator.presence(generator_peer),
                    route=route,
                )
                store = SettlementStore(root / "integration.sqlite3")
                verified = validate_submission(submission, store.active_policy())
                result = store.append(verified, "shadow", int(time.time()))

                self.assertTrue(
                    torch.allclose(output, hidden + 1, atol=1e-3, rtol=1e-3)
                )
                self.assertEqual(result["status"], "shadow_accepted")
                self.assertEqual(result["reward_units"], 600)
            finally:
                if expert is not None:
                    RemoteExpertWorker.run_coroutine(expert.p2p.shutdown())
                if client_dht is not None:
                    client_dht.shutdown()
                rpc.stop()
                dht.shutdown()


class SettlementTests(unittest.TestCase):
    def test_credit_formula_and_restart_durability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = ReceiptFixture(root)
            database = root / "settlement.sqlite3"
            store = SettlementStore(database)
            verified = validate_submission(
                fixture.submission,
                store.active_policy(),
                now=fixture.now,
            )

            result = store.append(verified, "credit", fixture.now)
            reopened = SettlementStore(database)
            account = reopened.account(fixture.worker.public_key)

            self.assertEqual(result["reward_units"], 36)
            self.assertEqual(account["verified_credits"], 36)
            self.assertEqual(account["useful_positions_served"], 3)
            self.assertEqual(stat.S_IMODE(database.stat().st_mode), 0o600)
            with sqlite3.connect(database) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)

    def test_unknown_database_schema_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "future.sqlite3"
            with sqlite3.connect(database) as connection:
                connection.execute("PRAGMA user_version=99")

            with self.assertRaisesRegex(RuntimeError, "schema version 99"):
                SettlementStore(database)

    def test_shadow_accepts_without_changing_balance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = ReceiptFixture(root)
            store = SettlementStore(root / "settlement.sqlite3")
            verified = validate_submission(
                fixture.submission,
                store.active_policy(),
                now=fixture.now,
            )

            result = store.append(verified, "shadow", fixture.now)
            account = store.account(fixture.worker.public_key)

            self.assertEqual(result["status"], "shadow_accepted")
            self.assertEqual(account["verified_credits"], 0)
            self.assertEqual(account["accepted_receipts"], 1)

    def test_p2p_peer_cannot_rebind_to_another_application_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = ReceiptFixture(root, "binding-1", peer_tag="shared-peer")
            second = ReceiptFixture(root, "binding-2", peer_tag="shared-peer")
            store = SettlementStore(root / "settlement.sqlite3")
            store.append(
                validate_submission(
                    first.submission,
                    store.active_policy(),
                    now=first.now,
                ),
                "shadow",
                first.now,
            )

            with self.assertRaisesRegex(ProtocolError, "already bound"):
                store.append(
                    validate_submission(
                        second.submission,
                        store.active_policy(),
                        now=second.now,
                    ),
                    "shadow",
                    second.now,
                )

    def test_replay_self_dealing_and_incomplete_route_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = ReceiptFixture(root)
            store = SettlementStore(root / "settlement.sqlite3")
            verified = validate_submission(
                fixture.submission,
                store.active_policy(),
                now=fixture.now,
            )
            store.append(verified, "credit", fixture.now)

            with self.assertRaisesRegex(ProtocolError, "replay"):
                store.append(verified, "credit", fixture.now)

            self_dealing = fixture.submission.copy()
            self_dealing["generator_acceptance"] = fixture.worker.sign(
                {
                    **fixture.acceptance["payload"],
                    "generator_public_key": fixture.worker.public_key,
                }
            )
            with self.assertRaises(ProtocolError):
                validate_submission(self_dealing, default_policy(), now=fixture.now)

            same_peer_worker = fixture.worker.sign(
                {
                    **fixture.receipt["payload"],
                    "generator_peer_id": fixture.worker_peer,
                }
            )
            same_peer_acceptance = fixture.generator.sign(
                {
                    **fixture.acceptance["payload"],
                    "worker_receipt_hash": hash_document(same_peer_worker),
                }
            )
            same_peer = {
                **fixture.submission,
                "worker_receipt": same_peer_worker,
                "generator_acceptance": same_peer_acceptance,
            }
            with self.assertRaisesRegex(ProtocolError, "p2p peers"):
                validate_submission(same_peer, default_policy(), now=fixture.now)

            partial_route = [dict(fixture.route[0], layer_end=6)]
            partial_request = create_inference_request(
                fixture.generator,
                generator_peer_id=fixture.generator_peer,
                session_id="partial-session",
                model_name="facebook/opt-125m",
                model_revision="main",
                route=partial_route,
                worker=partial_route[0],
                hidden_states=fixture.hidden,
                position_count=3,
                request_id="partial-request",
                timestamp=fixture.now,
            )
            partial_payload = verify_signed_document(partial_request)
            partial_receipt = create_worker_receipt(
                fixture.worker,
                partial_payload,
                fixture.response,
                worker_peer_id=fixture.worker_peer,
                timestamp=fixture.now,
            )
            partial_acceptance = accept_worker_receipt(
                fixture.generator,
                partial_request,
                partial_receipt,
                fixture.response,
                generator_peer_id=fixture.generator_peer,
                timestamp=fixture.now,
            )
            partial_submission = build_submission(
                worker_receipt=partial_receipt,
                generator_acceptance=partial_acceptance,
                worker_presence=fixture.submission["worker_presence"],
                generator_presence=fixture.submission["generator_presence"],
                route=partial_route,
            )
            with self.assertRaisesRegex(ProtocolError, "complete model"):
                validate_submission(partial_submission, default_policy(), now=fixture.now)

    def test_concurrent_distinct_submissions_and_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "settlement.sqlite3"
            store = SettlementStore(database)
            fixtures = [ReceiptFixture(root, f"request-{index}") for index in range(4)]

            def append(fixture: ReceiptFixture) -> None:
                verified = validate_submission(
                    fixture.submission,
                    store.active_policy(),
                    now=fixture.now,
                )
                store.append(verified, "credit", fixture.now)

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                list(executor.map(append, fixtures))

            first = store.entries(fixtures[0].worker.public_key, None, 1)
            self.assertEqual(len(first["entries"]), 1)
            self.assertIsNone(first["next_cursor"])

            shared = [
                ReceiptFixture(root, f"shared-{index}", identity_tag="shared")
                for index in range(3)
            ]
            for fixture in shared:
                store.append(
                    validate_submission(
                        fixture.submission,
                        store.active_policy(),
                        now=fixture.now,
                    ),
                    "credit",
                    fixture.now,
                )
            first_page = store.entries(shared[0].worker.public_key, None, 2)
            second_page = store.entries(
                shared[0].worker.public_key,
                first_page["next_cursor"],
                2,
            )
            self.assertEqual(len(first_page["entries"]), 2)
            self.assertIsNotNone(first_page["next_cursor"])
            self.assertEqual(len(second_page["entries"]), 1)

    def test_api_modes_policy_and_validation_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = ReceiptFixture(root)
            off_client = TestClient(create_settlement_app(root / "off.sqlite3", "off"))
            shadow_client = TestClient(
                create_settlement_app(root / "shadow.sqlite3", "shadow")
            )

            self.assertEqual(off_client.post("/v1/receipts", json=fixture.submission).status_code, 503)
            accepted = shadow_client.post("/v1/receipts", json=fixture.submission)
            replay = shadow_client.post("/v1/receipts", json=fixture.submission)
            policy = shadow_client.get("/v1/policy").json()

            self.assertEqual(accepted.status_code, 200)
            self.assertEqual(replay.status_code, 409)
            self.assertEqual(policy["mode"], "shadow")
            self.assertEqual(policy["protocol_version"], 1)

    def test_runtime_submission_status_and_public_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            identity = load_application_identity(Path(directory) / "runtime.json")
            responses = [
                {"status": "shadow_accepted"},
                {
                    "verified_credits": 7,
                    "ledger_entries": 1,
                    "accepted_receipts": 1,
                    "useful_positions_served": 3,
                },
            ]

            class Response:
                def __init__(self, body: dict) -> None:
                    self.body = body

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                def read(self) -> bytes:
                    import json

                    return json.dumps(self.body).encode()

            def opener(request, timeout):
                return Response(responses.pop(0))

            runtime = UsefulWorkRuntime(
                IncentivesConfig("shadow", "https://settlement.example", "main"),
                identity,
                opener,
            )
            runtime.bind_peer_id("runtime-peer")

            self.assertTrue(runtime.submit({"receipt": "document"}))
            runtime._queue.join()
            snapshot = runtime.snapshot()

            self.assertEqual(snapshot["settlement_connectivity"], "connected")
            self.assertEqual(snapshot["accepted_submissions"], 1)
            self.assertEqual(snapshot["verified_credits"], 7)
            self.assertNotIn("private_key", snapshot)


if __name__ == "__main__":
    unittest.main()
