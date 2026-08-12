import concurrent.futures
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch
import torch.nn as nn

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from incentives.identity import get_or_create_identity, verify_signature
from incentives.protocol import (
    create_generator_acceptance,
    create_work_request,
    create_worker_receipt,
    decode_envelope,
    encode_envelope,
    validate_work_request,
    validate_worker_receipt,
)
from incentives.settlement import (
    SettlementPolicy,
    SettlementStore,
    create_settlement_app,
    validate_receipt_pair,
)
from incentives.runtime import reset_runtime_for_tests
from node.rpc_server import RPCServer


MODEL_NAME = "facebook/opt-125m"


class IncentiveProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.generator = get_or_create_identity(root / "generator")
        self.worker = get_or_create_identity(root / "worker")
        self.hidden = torch.arange(16, dtype=torch.float32).reshape(1, 2, 8)
        self.output = self.hidden + 1
        self.route = [{"peer_id": "worker-peer", "layer_start": 0, "layer_end": 12}]

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def make_pair(self) -> dict:
        request = create_work_request(
            identity=self.generator,
            session_id="session-a",
            model_name=MODEL_NAME,
            model_revision="registry",
            route=self.route,
            peer_id="worker-peer",
            layer_start=0,
            layer_end=12,
            hidden_states=self.hidden,
        )
        validated = validate_work_request(request, self.hidden)
        receipt = create_worker_receipt(
            identity=self.worker,
            request=validated,
            peer_id="worker-peer",
            output=self.output,
            position_count=2,
        )
        validate_worker_receipt(
            envelope=receipt,
            request_envelope=request,
            worker_public_key=self.worker.public_key,
            peer_id="worker-peer",
            output=self.output,
        )
        acceptance = create_generator_acceptance(
            identity=self.generator,
            worker_receipt=receipt,
            route=self.route,
        )
        return {"worker_receipt": receipt, "generator_acceptance": acceptance}

    def test_identity_persists_and_signatures_reject_tampering(self) -> None:
        identity_dir = Path(self.tempdir.name) / "persistent"
        first = get_or_create_identity(identity_dir)
        second = get_or_create_identity(identity_dir)
        payload = {"value": 1}
        signature = first.sign(payload)

        self.assertEqual(first.public_key, second.public_key)
        self.assertTrue(verify_signature(second.public_key, payload, signature))
        self.assertFalse(verify_signature(second.public_key, {"value": 2}, signature))
        self.assertEqual(os.stat(identity_dir / "ed25519.key").st_mode & 0o777, 0o600)

    def test_receipt_tensor_encoding_round_trip(self) -> None:
        pair = self.make_pair()
        encoded = encode_envelope(pair["worker_receipt"])
        self.assertEqual(decode_envelope(encoded[0]), pair["worker_receipt"])

    def test_request_rejects_changed_tensor(self) -> None:
        request = create_work_request(
            identity=self.generator,
            session_id="session-a",
            model_name=MODEL_NAME,
            model_revision="registry",
            route=self.route,
            peer_id="worker-peer",
            layer_start=0,
            layer_end=12,
            hidden_states=self.hidden,
        )
        with self.assertRaisesRegex(ValueError, "input commitment"):
            validate_work_request(request, self.hidden + 1)

    def test_worker_receipt_rejects_changed_output(self) -> None:
        pair = self.make_pair()
        request = create_work_request(
            identity=self.generator,
            session_id="session-a",
            model_name=MODEL_NAME,
            model_revision="registry",
            route=self.route,
            peer_id="worker-peer",
            layer_start=0,
            layer_end=12,
            hidden_states=self.hidden,
        )
        pair["worker_receipt"]["payload"]["request_id"] = request["payload"]["request_id"]
        with self.assertRaises(ValueError):
            validate_worker_receipt(
                envelope=pair["worker_receipt"],
                request_envelope=request,
                worker_public_key=self.worker.public_key,
                peer_id="worker-peer",
                output=self.output + 1,
            )

    def test_worker_receipt_rejects_overclaimed_positions(self) -> None:
        request = create_work_request(
            identity=self.generator,
            session_id="session-a",
            model_name=MODEL_NAME,
            model_revision="registry",
            route=self.route,
            peer_id="worker-peer",
            layer_start=0,
            layer_end=12,
            hidden_states=self.hidden,
        )
        receipt = create_worker_receipt(
            identity=self.worker,
            request=validate_work_request(request, self.hidden),
            peer_id="worker-peer",
            output=self.output,
            position_count=999,
        )
        with self.assertRaisesRegex(ValueError, "position_count"):
            validate_worker_receipt(
                envelope=receipt,
                request_envelope=request,
                worker_public_key=self.worker.public_key,
                peer_id="worker-peer",
                output=self.output,
            )


class SettlementStoreTests(IncentiveProtocolTests):
    def setUp(self) -> None:
        super().setUp()
        self.database_path = Path(self.tempdir.name) / "ledger.sqlite3"

    def store(self, mode: str = "credit") -> SettlementStore:
        with patch.dict(os.environ, {}, clear=False):
            return SettlementStore(self.database_path, SettlementPolicy(mode))

    def test_credit_mode_appends_reward_and_account_balance(self) -> None:
        store = self.store()
        result = store.submit(self.make_pair())
        account = store.account(self.worker.public_key)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["credited"], 24)
        self.assertEqual(account["verified_credits"], 24)

    def test_shadow_mode_validates_without_crediting(self) -> None:
        store = self.store("shadow")
        result = store.submit(self.make_pair())
        self.assertEqual(result["calculated_reward"], 24)
        self.assertEqual(result["credited"], 0)
        self.assertEqual(store.account(self.worker.public_key)["verified_credits"], 0)

    def test_duplicate_receipt_is_rejected_after_restart(self) -> None:
        pair = self.make_pair()
        self.store().submit(pair)
        restarted = self.store()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            restarted.submit(pair)
        self.assertEqual(restarted.account(self.worker.public_key)["verified_credits"], 24)

    def test_tampered_receipt_is_rejected(self) -> None:
        pair = self.make_pair()
        pair["worker_receipt"]["payload"]["position_count"] = 999
        with self.assertRaisesRegex(ValueError, "worker signature"):
            self.store().submit(pair)

    def test_self_dealing_is_rejected(self) -> None:
        pair = self.make_pair()
        worker_payload = pair["worker_receipt"]["payload"]
        worker_payload["generator_public_key"] = self.worker.public_key
        pair["worker_receipt"]["signature"] = self.worker.sign(worker_payload)
        with self.assertRaisesRegex(ValueError, "distinct"):
            validate_receipt_pair(pair, SettlementPolicy("credit"))

    def test_worker_outside_route_is_rejected(self) -> None:
        pair = self.make_pair()
        acceptance_payload = pair["generator_acceptance"]["payload"]
        acceptance_payload["route"] = [
            {"peer_id": "other-peer", "layer_start": 0, "layer_end": 12}
        ]
        pair["generator_acceptance"]["signature"] = self.generator.sign(acceptance_payload)
        with self.assertRaisesRegex(ValueError, "route commitment"):
            self.store().submit(pair)

    def test_incomplete_route_is_rejected(self) -> None:
        incomplete_route = [{"peer_id": "worker-peer", "layer_start": 0, "layer_end": 6}]
        request = create_work_request(
            identity=self.generator,
            session_id="session-a",
            model_name=MODEL_NAME,
            model_revision="registry",
            route=incomplete_route,
            peer_id="worker-peer",
            layer_start=0,
            layer_end=6,
            hidden_states=self.hidden,
        )
        receipt = create_worker_receipt(
            identity=self.worker,
            request=validate_work_request(request, self.hidden),
            peer_id="worker-peer",
            output=self.output,
            position_count=2,
        )
        pair = {
            "worker_receipt": receipt,
            "generator_acceptance": create_generator_acceptance(
                identity=self.generator,
                worker_receipt=receipt,
                route=incomplete_route,
            ),
        }
        with self.assertRaisesRegex(ValueError, "complete model"):
            self.store().submit(pair)

    def test_concurrent_distinct_receipts_are_append_only(self) -> None:
        store = self.store()
        pairs = [self.make_pair() for _ in range(8)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(store.submit, pairs))
        self.assertEqual(len(results), 8)
        self.assertEqual(store.account(self.worker.public_key)["verified_credits"], 192)
        first_page = store.entries(self.worker.public_key, cursor=0, limit=3)
        second_page = store.entries(
            self.worker.public_key,
            cursor=first_page["next_cursor"],
            limit=10,
        )
        self.assertEqual(len(first_page["entries"]), 3)
        self.assertEqual(len(second_page["entries"]), 5)

    def test_public_settlement_routes_are_registered(self) -> None:
        app = create_settlement_app(database_path=self.database_path, mode="credit")
        paths = {route.path for route in app.routes}
        self.assertTrue(
            {
                "/v1/receipts",
                "/v1/accounts/{public_key}",
                "/v1/accounts/{public_key}/entries",
                "/v1/policy",
            }.issubset(paths)
        )


class ReceiptRpcRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        reset_runtime_for_tests()

    def tearDown(self) -> None:
        reset_runtime_for_tests()
        self.tempdir.cleanup()

    def make_rpc(self, captured: dict) -> RPCServer:
        class Handler:
            model_name = MODEL_NAME
            layer_start = 0
            layer_end = 1
            device = "cpu"
            layers = nn.ModuleList([nn.Linear(8, 8)])

            def is_loaded(self) -> bool:
                return True

        class DHT:
            peer_id = "worker-peer"

        class FakeServer:
            def __init__(self, **kwargs) -> None:
                captured.update(kwargs)

            def run_in_background(self, await_ready: bool) -> None:
                captured["await_ready"] = await_ready

            def shutdown(self) -> None:
                return None

        rpc = RPCServer(Handler(), DHT(), "test-prefix")
        captured["server_class"] = FakeServer
        return rpc

    def test_off_mode_registers_only_legacy_expert(self) -> None:
        import node.rpc_server as rpc_module

        captured: dict = {}
        rpc = self.make_rpc(captured)
        with (
            patch.dict(
                os.environ,
                {
                    "DISTRIBLLM_INCENTIVE_MODE": "off",
                    "DISTRIBLLM_DATA_DIR": self.tempdir.name,
                },
            ),
            patch.object(rpc_module.hivemind.moe, "Server", captured["server_class"]),
        ):
            rpc.start()

        self.assertEqual(list(captured["module_backends"]), ["test-prefix.0.1"])
        self.assertIsNone(rpc.get_receipt_uid())

    def test_shadow_mode_registers_receipt_expert(self) -> None:
        import node.rpc_server as rpc_module

        captured: dict = {}
        rpc = self.make_rpc(captured)
        with (
            patch.dict(
                os.environ,
                {
                    "DISTRIBLLM_INCENTIVE_MODE": "shadow",
                    "DISTRIBLLM_DATA_DIR": self.tempdir.name,
                },
            ),
            patch.object(rpc_module.hivemind.moe, "Server", captured["server_class"]),
        ):
            rpc.start()

        self.assertEqual(
            set(captured["module_backends"]),
            {"test-prefix.0.1", "test-prefix.receipt.0.1"},
        )
        self.assertEqual(rpc.get_receipt_uid(), "test-prefix.receipt.0.1")

if __name__ == "__main__":
    unittest.main()
