import json
import stat
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from acceptance_evidence import (
    CaptureOptions,
    EvidenceError,
    capture_evidence,
    validate_evidence,
)


class FakeResponse:
    def __init__(self, document: dict) -> None:
        self.document = document

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return json.dumps(self.document).encode()


def node(peer: str, start: int, end: int, mode: str = "relay") -> dict:
    return {
        "peer_id": peer,
        "node_id": f"node-{peer}",
        "model_name": "facebook/opt-125m",
        "layer_start": start,
        "layer_end": end,
        "device": "cpu",
        "running": True,
        "layers_loaded": True,
        "rpc_running": True,
        "rpc_uid": f"uid-{peer}",
        "connection_mode": mode,
        "direct_reachability": mode == "direct",
        "transport_verified": True,
        "maddrs": ["/ip4/127.0.0.1/tcp/1234"],
    }


def incentives(mode: str = "shadow", accepted: int = 2) -> dict:
    return {
        "mode": mode,
        "protocol_version": 1,
        "application_public_key": "public-key",
        "p2p_peer_id": "generator-peer",
        "settlement_url_configured": True,
        "settlement_connectivity": "connected",
        "pending_submissions": 0,
        "accepted_submissions": accepted,
        "rejected_submissions": 0,
        "verified_credits": 0,
        "ledger_entries": 0,
        "accepted_receipts": accepted,
        "useful_positions_served": 3,
        "token_ui_enabled": False,
        "transfers_enabled": False,
        "withdrawals_enabled": False,
        "private_key": "must-not-leak",
        "local_contributions": [],
    }


def performance() -> dict:
    return {
        "time_to_first_token_ms": 120.0,
        "total_duration_ms": 500.0,
        "generated_tokens": 2,
        "tokens_per_second": 4.0,
        "route_validation_ms_total": 10.0,
        "stopped": False,
        "hop_metrics": [
            {
                "peer_id": "head",
                "rpc_uid": "uid-head",
                "layer_start": 0,
                "layer_end": 6,
                "calls": 2,
                "total_latency_ms": 100.0,
                "average_latency_ms": 50.0,
                "last_latency_ms": 50.0,
            },
            {
                "peer_id": "tail",
                "rpc_uid": "uid-tail",
                "layer_start": 6,
                "layer_end": 12,
                "calls": 2,
                "total_latency_ms": 120.0,
                "average_latency_ms": 60.0,
                "last_latency_ms": 60.0,
            },
        ],
    }


def evidence(participant: str, run_generation: bool = False) -> dict:
    owned_peer = "head" if participant == "device-a" else "tail"
    return {
        "schema_version": 1,
        "captured_at": "2026-08-13T00:00:00+00:00",
        "participant": participant,
        "runtime": {},
        "expectation": {
            "model_name": "facebook/opt-125m",
            "inference_requested": run_generation,
        },
        "backend": {
            "status": {"status": "online"},
            "nodes": [node("head", 0, 6), node("tail", 6, 12)],
            "local_nodes": [
                node(owned_peer, 0, 6) if owned_peer == "head" else node(owned_peer, 6, 12)
            ],
            "model": {
                "id": "facebook/opt-125m",
                "num_layers": 12,
                "runnable": True,
                "route_ready": True,
            },
            "serving_plan": {
                "model_id": "facebook/opt-125m",
                "coverage_revision": "revision",
                "total_layers": 12,
                "current_runnable": True,
                "route_kind": "multiple_providers",
                "missing_ranges": [],
                "uncovered_ranges": [],
                "selected_route": [node("head", 0, 6), node("tail", 6, 12)],
                "standby_ranges": [],
            },
            "generator": {
                "ready": run_generation,
                "model_name": "facebook/opt-125m" if run_generation else None,
                "route_ready": run_generation,
                "node_trace": ["head", "tail"] if run_generation else [],
            },
            "incentives_before": incentives(accepted=0),
            "incentives_after": incentives(),
            "stats": None,
            "stats_error": None,
        },
        "generation": (
            {
                "response": " Paris",
                "node_trace": ["head", "tail"],
                "tokens_generated": 2,
                "error": None,
                "performance": performance(),
            }
            if run_generation
            else None
        ),
    }


class CaptureEvidenceTests(unittest.TestCase):
    def test_capture_whitelists_fields_and_writes_private_file(self) -> None:
        responses = {
            "/status": {
                "status": "online",
                "node_running": True,
                "gpu_available": False,
                "generator_ready": True,
                "token_set": True,
                "local_models": [{"path": "/secret/model"}],
            },
            "/nodes": {"nodes": [node("head", 0, 12)]},
            "/nodes/local": {"nodes": [node("head", 0, 12)]},
            "/models": {
                "models": [
                    {
                        "id": "facebook/opt-125m",
                        "num_layers": 12,
                        "runnable": True,
                        "route_ready": True,
                        "route_reasons": [],
                        "covered_layers": 12,
                        "missing_layers": [],
                        "compatible_nodes": 1,
                        "route_trace": ["head"],
                        "local_import": {"path": "/secret/model"},
                    }
                ],
                "token_available": True,
            },
            "/generator/status": {
                "ready": True,
                "model_name": "facebook/opt-125m",
                "route_ready": True,
                "reasons": [],
                "node_trace": ["head"],
                "performance": None,
            },
            "/incentives/accounting": incentives(),
            "/stats": {
                "sampled_at": "2026-08-13T00:00:00Z",
                "cpu_percent": 10,
                "ram_percent": 20,
                "ram_used_gb": 2,
                "ram_total_gb": 8,
            },
            "/models/facebook%2Fopt-125m/serving-plan": {
                "model_id": "facebook/opt-125m",
                "coverage_revision": "revision",
                "total_layers": 12,
                "current_runnable": True,
                "route_kind": "single_provider",
                "missing_ranges": [],
                "uncovered_ranges": [],
                "selected_route": [node("head", 0, 12)],
                "standby_ranges": [],
            },
        }

        def opener(request, timeout):
            path = urllib.parse.urlsplit(request.full_url).path
            return FakeResponse(responses[path])

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "device-a.json"
            document = capture_evidence(
                CaptureOptions(
                    participant="device-a",
                    backend_url="http://127.0.0.1:8000",
                    model_name="facebook/opt-125m",
                    output=output,
                ),
                opener=opener,
            )

            serialized = json.dumps(document)
            self.assertNotIn("must-not-leak", serialized)
            self.assertNotIn("/secret/model", serialized)
            self.assertNotIn("token_set", serialized)
            self.assertNotIn("maddrs", serialized)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)

    def test_capture_rejects_backend_url_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EvidenceError, "without credentials"):
                capture_evidence(
                    CaptureOptions(
                        participant="device-a",
                        backend_url="http://user:password@127.0.0.1:8000",
                        model_name="facebook/opt-125m",
                        output=Path(directory) / "evidence.json",
                    )
                )

    def test_capture_rejects_non_positive_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(EvidenceError, "timeout must be positive"):
                capture_evidence(
                    CaptureOptions(
                        participant="device-a",
                        backend_url="http://127.0.0.1:8000",
                        model_name="facebook/opt-125m",
                        output=Path(directory) / "evidence.json",
                        timeout=0,
                    )
                )


class ValidateEvidenceTests(unittest.TestCase):
    def test_two_device_relay_shadow_evidence_passes(self) -> None:
        report = validate_evidence(
            [evidence("device-a"), evidence("device-b", run_generation=True)],
            model_name="facebook/opt-125m",
            expected_mode="relay",
            expected_incentives="shadow",
        )

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(
            [(item["layer_start"], item["layer_end"]) for item in report["selected_route"]],
            [(0, 6), (6, 12)],
        )

    def test_incomplete_wrong_mode_and_unsettled_evidence_fails(self) -> None:
        first = evidence("device-a")
        second = evidence("device-b", run_generation=True)
        first["backend"]["nodes"] = [node("head", 0, 6, mode="direct")]
        second["backend"]["nodes"] = [node("head", 0, 6, mode="direct")]
        second["backend"]["incentives_after"]["settlement_connectivity"] = "error"
        second["backend"]["incentives_after"]["accepted_submissions"] = 0

        report = validate_evidence(
            [first, second],
            model_name="facebook/opt-125m",
            expected_mode="relay",
            expected_incentives="shadow",
        )

        self.assertFalse(report["ok"])
        rendered = "\n".join(report["errors"])
        self.assertIn("No complete adjacent route", rendered)
        self.assertIn("settlement is not connected", rendered)
        self.assertIn("no accepted receipt", rendered)

    def test_duplicate_participant_and_single_peer_route_fails(self) -> None:
        first = evidence("same")
        second = evidence("same", run_generation=True)
        for document in (first, second):
            document["backend"]["nodes"] = [node("full", 0, 12)]
            document["backend"]["local_nodes"] = [node("full", 0, 12)]

        report = validate_evidence(
            [first, second],
            model_name="facebook/opt-125m",
            expected_mode="relay",
            expected_incentives="shadow",
        )

        self.assertFalse(report["ok"])
        rendered = "\n".join(report["errors"])
        self.assertIn("Participant labels must be unique", rendered)
        self.assertIn("requires at least 2", rendered)

    def test_generation_hops_must_match_selected_route(self) -> None:
        first = evidence("device-a")
        second = evidence("device-b", run_generation=True)
        second["generation"]["performance"]["hop_metrics"].reverse()

        report = validate_evidence(
            [first, second],
            model_name="facebook/opt-125m",
            expected_mode="relay",
            expected_incentives="shadow",
        )

        self.assertFalse(report["ok"])
        self.assertIn(
            "timed hops do not match the selected route",
            "\n".join(report["errors"]),
        )

    def test_executed_exact_range_replica_is_accepted(self) -> None:
        first = evidence("device-a")
        second = evidence("device-b", run_generation=True)
        head_replica = node("head-replica", 0, 6)
        for document in (first, second):
            document["backend"]["nodes"].append(head_replica)
        first["backend"]["local_nodes"] = [head_replica]
        first_hop = second["generation"]["performance"]["hop_metrics"][0]
        first_hop["peer_id"] = "head-replica"
        first_hop["rpc_uid"] = "uid-head-replica"

        report = validate_evidence(
            [first, second],
            model_name="facebook/opt-125m",
            expected_mode="relay",
            expected_incentives="shadow",
        )

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["selected_route"][0]["peer_id"], "head-replica")

    def test_selected_route_requires_local_ownership_evidence(self) -> None:
        first = evidence("device-a")
        second = evidence("device-b", run_generation=True)
        second["backend"]["local_nodes"] = []

        report = validate_evidence(
            [first, second],
            model_name="facebook/opt-125m",
            expected_mode="relay",
            expected_incentives="shadow",
        )

        self.assertFalse(report["ok"])
        self.assertIn(
            "lack local ownership evidence",
            "\n".join(report["errors"]),
        )

    def test_standby_nonpayment_evidence_passes_for_unchanged_counters(self) -> None:
        first = evidence("device-a")
        second = evidence("device-b", run_generation=True)
        standby_before = evidence("standby-before")
        standby_after = evidence("standby-after")
        standby_after["captured_at"] = "2026-08-13T00:01:00+00:00"
        contribution = {
            "peer_id": "standby",
            "model_name": "facebook/opt-125m",
            "layer_start": 0,
            "layer_end": 6,
            "requests_served": 4,
            "token_positions_served": 20,
        }
        for document in (standby_before, standby_after):
            document["backend"]["incentives_after"]["local_contributions"] = [
                contribution
            ]
            document["backend"]["serving_plan"]["standby_ranges"] = [
                node("standby", 0, 6)
            ]

        report = validate_evidence(
            [first, second],
            model_name="facebook/opt-125m",
            expected_mode="relay",
            expected_incentives="shadow",
            standby_before=standby_before,
            standby_after=standby_after,
        )

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["standby_nonpayment_verified"], ["standby"])

    def test_standby_nonpayment_rejects_changed_counters(self) -> None:
        first = evidence("device-a")
        second = evidence("device-b", run_generation=True)
        standby_before = evidence("standby-before")
        standby_after = evidence("standby-after")
        standby_after["captured_at"] = "2026-08-13T00:01:00+00:00"
        base = {
            "peer_id": "standby",
            "model_name": "facebook/opt-125m",
            "layer_start": 0,
            "layer_end": 6,
            "requests_served": 4,
            "token_positions_served": 20,
        }
        standby_before["backend"]["incentives_after"]["local_contributions"] = [base]
        standby_after["backend"]["incentives_after"]["local_contributions"] = [
            {**base, "requests_served": 5}
        ]
        for document in (standby_before, standby_after):
            document["backend"]["serving_plan"]["standby_ranges"] = [
                node("standby", 0, 6)
            ]

        report = validate_evidence(
            [first, second],
            model_name="facebook/opt-125m",
            expected_mode="relay",
            expected_incentives="shadow",
            standby_before=standby_before,
            standby_after=standby_after,
        )

        self.assertFalse(report["ok"])
        self.assertIn("accounting changed", "\n".join(report["errors"]))


if __name__ == "__main__":
    unittest.main()
