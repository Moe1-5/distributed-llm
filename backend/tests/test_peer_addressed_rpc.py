import asyncio
import importlib.metadata
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import hivemind
import torch
import torch.nn as nn
from hivemind.moe.client.remote_expert_worker import RemoteExpertWorker
from hivemind.moe.expert_uid import is_valid_uid
from hivemind.p2p import PeerID
from hivemind.utils import get_dht_time

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from client.sequential import (  # noqa: E402
    RemoteSequential,
    get_peer_expert,
    get_ready_peer_expert,
    shutdown_remote_expert_p2p,
)
from client.rpc_policy import (  # noqa: E402
    RPCPreExecutionError,
    classify_rpc_error,
    is_safe_receipt_fallback,
)
from constants import EXPERT_RPC_UID_SCHEMA_VERSION  # noqa: E402
from incentives.config import IncentivesConfig  # noqa: E402
from node.rpc_server import RPCServer, _run_with_timeout  # noqa: E402


class _OffsetHandler:
    model_name = "facebook/opt-125m"
    layer_start = 0
    layer_end = 1
    device = "cpu"

    def __init__(self, offset: float) -> None:
        self.offset = offset
        self.layers = nn.ModuleList([nn.Linear(8, 8, bias=False)])

    def is_loaded(self) -> bool:
        return True

    def forward(self, hidden_states: torch.Tensor, **_kwargs) -> torch.Tensor:
        return hidden_states + self.offset


class PeerAddressedRPCUnitTests(unittest.TestCase):
    def test_get_peer_expert_constructs_the_exact_peer_without_a_dht_lookup(self) -> None:
        self.assertEqual(importlib.metadata.version("hivemind"), "1.1.12")
        peer_id = str(PeerID(b"\x12\x20" + b"p" * 32))
        replicated_p2p = object()

        class DHTWithoutLookup:
            async def replicate_p2p(self):
                return replicated_p2p

        with patch.object(
            RemoteExpertWorker,
            "run_coroutine",
            side_effect=asyncio.run,
        ):
            expert = get_peer_expert(
                DHTWithoutLookup(),
                "peer-addressed.0.0.1.0",
                peer_id,
            )

        self.assertEqual(expert.uid, "peer-addressed.0.0.1.0")
        self.assertEqual(str(expert.peer_id), peer_id)
        self.assertIs(expert.p2p, replicated_p2p)

    def test_peer_scoped_uids_are_valid_and_metadata_preserves_ownership(self) -> None:
        peer_a = str(PeerID(b"\x12\x20" + b"a" * 32))
        peer_b = str(PeerID(b"\x12\x20" + b"b" * 32))
        uid_a = RPCServer.build_rpc_uid(
            "peer-addressed",
            0,
            1,
            provider_peer_id=peer_a,
        )
        uid_b = RPCServer.build_rpc_uid(
            "peer-addressed",
            0,
            1,
            provider_peer_id=peer_b,
        )
        receipt_uid_a = RPCServer.build_receipt_rpc_uid(
            "peer-addressed",
            0,
            1,
            provider_peer_id=peer_a,
        )

        self.assertNotEqual(uid_a, uid_b)
        self.assertNotEqual(uid_a, receipt_uid_a)
        self.assertTrue(is_valid_uid(uid_a))
        self.assertTrue(is_valid_uid(uid_b))
        self.assertTrue(is_valid_uid(receipt_uid_a))
        self.assertIn(peer_a, uid_a)
        self.assertIn(peer_b, uid_b)

        sequential = RemoteSequential(
            object(),
            "peer-addressed",
            num_layers=1,
            model_name="facebook/opt-125m",
        )
        metadata = sequential._validate_node_metadata(
            {
                "peer_id": peer_a,
                "rpc_peer_id": peer_a,
                "rpc_uid_schema_version": EXPERT_RPC_UID_SCHEMA_VERSION,
                "rpc_uid": uid_a,
                "model_name": "facebook/opt-125m",
                "layer_start": 0,
                "layer_end": 1,
                "running": True,
                "layers_loaded": True,
                "rpc_running": True,
            },
            peer_a,
        )

        self.assertEqual(metadata["peer_id"], peer_a)
        self.assertEqual(metadata["rpc_peer_id"], peer_a)
        self.assertEqual(metadata["rpc_uid"], uid_a)
        self.assertEqual(
            metadata["rpc_uid_schema_version"],
            EXPERT_RPC_UID_SCHEMA_VERSION,
        )


class PeerAddressedRPCIntegrationTests(unittest.TestCase):
    def test_same_range_providers_execute_on_the_explicit_selected_peers(self) -> None:
        dht_a = None
        dht_b = None
        client_dht = None
        rpc_a = None
        rpc_b = None
        try:
            dht_a = hivemind.DHT(
                start=True,
                use_ipfs=False,
                host_maddrs=["/ip4/127.0.0.1/tcp/0"],
            )
            dht_b = hivemind.DHT(
                start=True,
                use_ipfs=False,
                initial_peers=dht_a.get_visible_maddrs(),
                host_maddrs=["/ip4/127.0.0.1/tcp/0"],
            )
            rpc_a = RPCServer(
                _OffsetHandler(1.0),
                dht_a,
                "peer-addressed",
                incentives_config=IncentivesConfig("off", "", "test"),
            )
            rpc_b = RPCServer(
                _OffsetHandler(2.0),
                dht_b,
                "peer-addressed",
                incentives_config=IncentivesConfig("off", "", "test"),
            )
            rpc_a.start()
            rpc_b.start()

            peer_a = str(dht_a.peer_id)
            peer_b = str(dht_b.peer_id)
            uid_a = rpc_a.get_uid()
            uid_b = rpc_b.get_uid()
            self.assertIsNotNone(uid_a)
            self.assertIsNotNone(uid_b)
            self.assertNotEqual(peer_a, peer_b)
            self.assertNotEqual(uid_a, uid_b)
            self.assertTrue(is_valid_uid(uid_a))
            self.assertTrue(is_valid_uid(uid_b))

            client_dht = hivemind.DHT(
                start=True,
                use_ipfs=False,
                client_mode=True,
                initial_peers=[
                    *dht_a.get_visible_maddrs(),
                    *dht_b.get_visible_maddrs(),
                ],
                host_maddrs=["/ip4/127.0.0.1/tcp/0"],
            )
            expert_a = get_peer_expert(client_dht, uid_a, peer_a)
            expert_b = get_peer_expert(client_dht, uid_b, peer_b)
            hidden = torch.zeros((1, 2, 8), dtype=torch.float32)
            attention_mask = torch.ones((1, 2), dtype=torch.long)
            position_ids = torch.arange(2, dtype=torch.long).unsqueeze(0)

            output_a = expert_a.forward(
                hidden,
                attention_mask=attention_mask,
                position_ids=position_ids,
            )
            output_b = expert_b.forward(
                hidden,
                attention_mask=attention_mask,
                position_ids=position_ids,
            )

            self.assertEqual(str(expert_a.peer_id), peer_a)
            self.assertEqual(str(expert_b.peer_id), peer_b)
            self.assertIsInstance(output_a, tuple)
            self.assertIsInstance(output_b, tuple)
            self.assertEqual(len(output_a), 1)
            self.assertEqual(len(output_b), 1)
            self.assertTrue(
                torch.allclose(output_a[0], hidden + 1, atol=1e-3, rtol=1e-3)
            )
            self.assertTrue(
                torch.allclose(output_b[0], hidden + 2, atol=1e-3, rtol=1e-3)
            )

            cached_route_a = {"peer_id": peer_a, "rpc_uid": uid_a}
            cached_route_client = RemoteSequential(
                client_dht,
                "peer-addressed",
                num_layers=1,
                model_name="facebook/opt-125m",
            )
            publication_started = threading.Event()
            publication_results: list[bool] = []
            publication_errors: list[Exception] = []

            def republish_peer_b() -> None:
                publication_started.set()
                try:
                    for offset in range(3):
                        publication_results.append(
                            bool(
                                dht_b.store(
                                    key=uid_b,
                                    value=peer_b,
                                    expiration_time=get_dht_time() + 300 + offset,
                                )
                            )
                        )
                        time.sleep(0.01)
                except Exception as exc:
                    publication_errors.append(exc)

            publisher = threading.Thread(target=republish_peer_b)
            publisher.start()
            self.assertTrue(publication_started.wait(timeout=2.0))
            cached_outputs = [
                cached_route_client._rpc_forward(
                    rpc_uid=cached_route_a["rpc_uid"],
                    peer_id=cached_route_a["peer_id"],
                    hidden_states=hidden,
                    attention_mask=attention_mask,
                    position_ids=position_ids,
                )
                for _ in range(3)
            ]
            publisher.join(timeout=5.0)

            self.assertFalse(publisher.is_alive())
            self.assertEqual(publication_errors, [])
            self.assertTrue(any(publication_results))
            self.assertTrue(
                all(
                    torch.allclose(output, hidden + 1, atol=1e-3, rtol=1e-3)
                    for output in cached_outputs
                )
            )

            missing_normal_uid = RPCServer.build_rpc_uid(
                "peer-addressed",
                0,
                1,
                uid_suffix=99,
                provider_peer_id=peer_a,
            )
            with self.assertRaises(RPCPreExecutionError) as normal_error:
                get_ready_peer_expert(client_dht, missing_normal_uid, peer_a)
            self.assertEqual(
                classify_rpc_error(normal_error.exception),
                "pre_execution_transport",
            )
            self.assertFalse(is_safe_receipt_fallback(normal_error.exception))

            missing_receipt_uid = RPCServer.build_receipt_rpc_uid(
                "peer-addressed",
                0,
                1,
                provider_peer_id=peer_a,
            )
            with self.assertRaises(RPCPreExecutionError) as receipt_error:
                get_ready_peer_expert(
                    client_dht,
                    missing_receipt_uid,
                    peer_a,
                    rpc_role="receipt",
                )
            self.assertEqual(
                classify_rpc_error(receipt_error.exception),
                "pre_execution_transport",
            )
            self.assertTrue(is_safe_receipt_fallback(receipt_error.exception))
        finally:
            if client_dht is not None:
                shutdown_remote_expert_p2p(client_dht)
                _run_with_timeout("peer-rpc-client-dht-shutdown", client_dht.shutdown, 5.0)
            if rpc_b is not None:
                rpc_b.stop(timeout=5.0)
            if rpc_a is not None:
                rpc_a.stop(timeout=5.0)
            if dht_b is not None:
                _run_with_timeout("peer-rpc-provider-b-dht-shutdown", dht_b.shutdown, 5.0)
            if dht_a is not None:
                _run_with_timeout("peer-rpc-provider-a-dht-shutdown", dht_a.shutdown, 5.0)


if __name__ == "__main__":
    unittest.main()
