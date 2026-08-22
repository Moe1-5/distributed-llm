import sys
import unittest
from pathlib import Path

import torch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from client.failover import RouteAttemptError
from tensor_payload_probe import (
    DEFAULT_SEQUENCE_LENGTHS,
    ProbeOptions,
    TensorPayloadProbeError,
    _request_measurements,
    _route_evidence,
    _run_payload_sweep,
    _validate_options,
    _validate_route_identity,
)
from node.rpc_server import RPCServer


def options(**overrides) -> ProbeOptions:
    values = {
        "initial_peers": ("/ip4/127.0.0.1/tcp/7001/p2p/bootstrap",),
        "expected_peer": "worker-peer",
        "expected_transport": "relay",
    }
    values.update(overrides)
    return ProbeOptions(**values)


def provider(**overrides) -> dict:
    values = {
        "peer_id": "worker-peer",
        "rpc_uid": "distribllm.0.12",
        "model_name": "facebook/opt-125m",
        "layer_start": 0,
        "layer_end": 12,
        "connection_mode": "relay",
        "transport_verified": True,
        "maddrs": ["/ip4/192.0.2.1/tcp/1234"],
    }
    values.update(overrides)
    return values


class TensorPayloadProbeTests(unittest.TestCase):
    def test_defaults_repeat_canary_before_increasing_payload(self) -> None:
        self.assertEqual(DEFAULT_SEQUENCE_LENGTHS[:4], (1, 1, 2, 4))
        self.assertIn(96, DEFAULT_SEQUENCE_LENGTHS)
        self.assertEqual(
            _validate_options(options()),
            (
                12,
                768,
                RPCServer.build_rpc_uid(
                    "distribllm",
                    0,
                    12,
                    provider_peer_id="worker-peer",
                ),
            ),
        )

    def test_options_require_safe_explicit_target(self) -> None:
        with self.assertRaisesRegex(TensorPayloadProbeError, "initial peer"):
            _validate_options(options(initial_peers=()))
        with self.assertRaisesRegex(TensorPayloadProbeError, "expected_peer"):
            _validate_options(options(expected_peer=""))
        with self.assertRaisesRegex(TensorPayloadProbeError, "expected_transport"):
            _validate_options(options(expected_transport="auto"))
        with self.assertRaisesRegex(TensorPayloadProbeError, "sequence length"):
            _validate_options(options(sequence_lengths=()))
        with self.assertRaisesRegex(TensorPayloadProbeError, "positive"):
            _validate_options(options(sequence_lengths=(0,)))
        with self.assertRaisesRegex(TensorPayloadProbeError, "bounded probe limit"):
            _validate_options(options(sequence_lengths=(2049,)))

    def test_route_must_match_one_exact_verified_provider(self) -> None:
        selected = _validate_route_identity(
            [provider()],
            options(),
            total_layers=12,
            expected_rpc_uid="distribllm.0.12",
        )
        self.assertEqual(selected["peer_id"], "worker-peer")

        with self.assertRaisesRegex(TensorPayloadProbeError, "peer_id"):
            _validate_route_identity(
                [provider(peer_id="wrong")],
                options(),
                total_layers=12,
                expected_rpc_uid="distribllm.0.12",
            )
        with self.assertRaisesRegex(TensorPayloadProbeError, "connection_mode"):
            _validate_route_identity(
                [provider(connection_mode="direct")],
                options(),
                total_layers=12,
                expected_rpc_uid="distribllm.0.12",
            )
        with self.assertRaisesRegex(TensorPayloadProbeError, "one full-model"):
            _validate_route_identity(
                [provider(), provider()],
                options(),
                total_layers=12,
                expected_rpc_uid="distribllm.0.12",
            )

    def test_route_evidence_omits_addresses_and_receipt_capability(self) -> None:
        evidence = _route_evidence(
            provider(
                receipt_rpc_uid="distribllm.999999.0.12",
                application_public_key="secret-adjacent-value",
            )
        )
        self.assertNotIn("maddrs", evidence)
        self.assertNotIn("receipt_rpc_uid", evidence)
        self.assertNotIn("application_public_key", evidence)
        self.assertEqual(evidence["connection_mode"], "relay")
        self.assertEqual(evidence["rpc_peer_id"], "worker-peer")
        self.assertEqual(evidence["rpc_uid_schema_version"], 1)

    def test_request_measurements_reflect_float16_activation_wire_format(self) -> None:
        hidden = torch.zeros((1, 8, 768), dtype=torch.float32)
        mask = torch.ones((1, 8), dtype=torch.long)
        positions = torch.arange(8, dtype=torch.long).reshape(1, -1)
        measurements = _request_measurements(hidden, mask, positions)
        self.assertEqual(measurements["logical_tensor_bytes"], 24_704)
        self.assertGreater(measurements["serialized_tensor_protobuf_bytes"], 12_288)
        self.assertLess(measurements["serialized_tensor_protobuf_bytes"], 24_704)

    def test_sweep_uses_legacy_single_hop_calls_and_preserves_repeated_cases(self) -> None:
        calls: list[dict] = []
        checkpoints: list[dict] = []

        def forward(**kwargs):
            calls.append(kwargs)
            return kwargs["hidden_states"] + 1

        cases, failure = _run_payload_sweep(
            sequence_lengths=(1, 1, 2),
            hidden_size=4,
            node=provider(),
            forward_call=forward,
            checkpoint=checkpoints.append,
        )

        self.assertIsNone(failure)
        self.assertEqual([case["sequence_length"] for case in cases], [1, 1, 2])
        self.assertTrue(all(case["status"] == "passed" for case in cases))
        self.assertEqual(len({case["request_id"] for case in cases}), 3)
        self.assertTrue(all(call["receipt_route"] is None for call in calls))
        self.assertTrue(all(call["hop_index"] == 0 for call in calls))
        self.assertEqual(checkpoints[0]["cases"][0]["status"], "running")

    def test_sweep_stops_immediately_after_first_ambiguous_failure(self) -> None:
        calls: list[int] = []

        def forward(**kwargs):
            sequence_length = int(kwargs["hidden_states"].shape[1])
            calls.append(sequence_length)
            if sequence_length == 2:
                raise RouteAttemptError(
                    "stream reset after dispatch",
                    failure_class="ambiguous_transport",
                    node=kwargs["node_info"],
                )
            return kwargs["hidden_states"]

        cases, failure = _run_payload_sweep(
            sequence_lengths=(1, 2, 4),
            hidden_size=4,
            node=provider(),
            forward_call=forward,
        )

        self.assertEqual(calls, [1, 2])
        self.assertEqual([case["status"] for case in cases], ["passed", "failed"])
        self.assertIsNotNone(failure)
        self.assertEqual(failure["failure_class"], "ambiguous_transport")
        self.assertEqual(failure["stage"], "remote_forward")


if __name__ == "__main__":
    unittest.main()
