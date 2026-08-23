import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from tinyllama_performance_probe import (
    ProbeOptions,
    TinyLlamaProbeError,
    _baseline_ok,
    _performance_evidence,
    _validate_options,
)


class TinyLlamaPerformanceProbeTests(unittest.TestCase):
    def test_default_options_target_registered_full_model(self) -> None:
        self.assertEqual(_validate_options(ProbeOptions()), 22)

    def test_invalid_bounds_are_rejected(self) -> None:
        with self.assertRaisesRegex(TinyLlamaProbeError, "max_new_tokens"):
            _validate_options(ProbeOptions(max_new_tokens=0))
        with self.assertRaisesRegex(TinyLlamaProbeError, "route_timeout"):
            _validate_options(ProbeOptions(route_timeout=0))

    def test_performance_evidence_omits_unapproved_fields(self) -> None:
        evidence = _performance_evidence(
            {
                "time_to_first_token_ms": 100,
                "total_duration_ms": 200,
                "generated_tokens": 2,
                "tokens_per_second": 10,
                "route_validation_ms_total": 5,
                "stopped": False,
                "session_protocol_version": 1,
                "session_prefill_bytes": 1024,
                "session_decode_bytes": 256,
                "session_decode_calls": 2,
                "secret": "not-for-evidence",
                "hop_metrics": [
                    {
                        "peer_id": "peer",
                        "rpc_uid": "uid.0.22",
                        "layer_start": 0,
                        "layer_end": 22,
                        "calls": 2,
                        "total_latency_ms": 80,
                        "average_latency_ms": 40,
                        "last_latency_ms": 40,
                        "maddrs": ["private"],
                    }
                ],
            }
        )

        self.assertNotIn("secret", evidence)
        self.assertNotIn("maddrs", evidence["hop_metrics"][0])
        self.assertEqual(evidence["session_protocol_version"], 1)
        self.assertEqual(evidence["session_decode_bytes"], 256)

    def test_baseline_requires_visible_output_metrics_and_accounting(self) -> None:
        route = [{"layer_start": 0, "layer_end": 22}]
        generation = {
            "response": "Paris",
            "node_trace": ["peer"],
            "metrics": {
                "time_to_first_token_ms": 100,
                "total_duration_ms": 200,
                "generated_tokens": 2,
                "tokens_per_second": 10,
                "hop_metrics": [{"calls": 2}],
            },
        }
        accounting = {
            "requests_served": 2,
            "token_positions_served": 20,
            "failed_requests": 0,
        }

        self.assertTrue(_baseline_ok(route, generation, accounting, 22))
        self.assertFalse(
            _baseline_ok(route, {**generation, "response": ""}, accounting, 22)
        )


if __name__ == "__main__":
    unittest.main()
