import math
import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from network.publication import (
    PublicationDisposition,
    classify_publication,
    local_transport_failed_outcome,
    unverified_outcome,
)


class PublicationClassificationTests(unittest.TestCase):
    def classify(self, **overrides):
        values = {
            "key": "distribllm.nodes.v2",
            "subkey": "peer-a",
            "store_returned": False,
            "expected_value": {"peer_id": "peer-a", "layer_start": 0},
            "observed_value": {"peer_id": "peer-a", "layer_start": 0},
            "observed_expiration": 140.0,
            "attempted_expiration": 130.0,
            "minimum_safe_expiration": 120.0,
            "equivalent": lambda expected, observed: expected == observed,
        }
        values.update(overrides)
        return classify_publication(**values)

    def test_true_store_result_is_accepted_without_readback(self) -> None:
        outcome = self.classify(
            store_returned=True,
            observed_value=None,
            observed_expiration=None,
        )

        self.assertEqual(outcome.disposition, PublicationDisposition.ACCEPTED)
        self.assertTrue(outcome.publication_safe)
        self.assertFalse(outcome.permits_transport_recovery)

    def test_acknowledged_store_below_safe_horizon_is_not_safe(self) -> None:
        outcome = self.classify(
            store_returned=True,
            attempted_expiration=119.99,
            minimum_safe_expiration=120.0,
            observed_value=None,
            observed_expiration=None,
        )

        self.assertEqual(outcome.disposition, PublicationDisposition.SHORT_HORIZON)
        self.assertEqual(outcome.reason, "acknowledged_record_expires_too_soon")
        self.assertFalse(outcome.publication_safe)
        self.assertFalse(outcome.permits_transport_recovery)

    def test_false_with_safe_equivalent_newer_record_is_safe(self) -> None:
        outcome = self.classify()

        self.assertEqual(
            outcome.disposition,
            PublicationDisposition.SUPERSEDED_EQUIVALENT,
        )
        self.assertTrue(outcome.observed_equivalent)
        self.assertTrue(outcome.publication_safe)
        self.assertFalse(outcome.permits_transport_recovery)

    def test_false_with_conflicting_record_is_not_transport_failure(self) -> None:
        outcome = self.classify(
            observed_value={"peer_id": "peer-b", "layer_start": 0}
        )

        self.assertEqual(outcome.disposition, PublicationDisposition.CONFLICT)
        self.assertFalse(outcome.publication_safe)
        self.assertFalse(outcome.permits_transport_recovery)

    def test_equivalent_record_below_safe_horizon_needs_repair(self) -> None:
        outcome = self.classify(observed_expiration=119.99)

        self.assertEqual(outcome.disposition, PublicationDisposition.SHORT_HORIZON)
        self.assertFalse(outcome.permits_transport_recovery)

    def test_false_without_independent_record_is_unverified(self) -> None:
        outcome = self.classify(observed_value=None, observed_expiration=None)

        self.assertEqual(outcome.disposition, PublicationDisposition.UNVERIFIED)
        self.assertEqual(outcome.reason, "independent_record_missing")
        self.assertFalse(outcome.permits_transport_recovery)

    def test_bad_observed_expiration_is_unverified(self) -> None:
        outcome = self.classify(observed_expiration=math.nan)

        self.assertEqual(outcome.disposition, PublicationDisposition.UNVERIFIED)
        self.assertEqual(
            outcome.reason,
            "independent_expiration_missing_or_invalid",
        )

    def test_equivalence_failure_is_bounded_and_unverified(self) -> None:
        def fails(_expected, _observed):
            raise RuntimeError("secret record contents")

        outcome = self.classify(equivalent=fails)

        self.assertEqual(outcome.disposition, PublicationDisposition.UNVERIFIED)
        self.assertNotIn("secret", str(outcome.to_dict()))

    def test_only_independent_transport_failure_permits_recovery(self) -> None:
        ambiguous = unverified_outcome(
            key="key",
            subkey=None,
            attempted_expiration=130.0,
            minimum_safe_expiration=120.0,
            store_returned=False,
            reason="store acknowledgement missing",
        )
        transport_failed = local_transport_failed_outcome(
            key="key",
            subkey=None,
            attempted_expiration=130.0,
            minimum_safe_expiration=120.0,
            reason="independent local p2p process exited",
        )

        self.assertFalse(ambiguous.permits_transport_recovery)
        self.assertTrue(transport_failed.permits_transport_recovery)

    def test_to_dict_omits_values_and_sanitizes_diagnostics(self) -> None:
        outcome = self.classify(key="key\nwith-control", subkey="x" * 200)

        payload = outcome.to_dict()
        self.assertNotIn("expected_value", payload)
        self.assertNotIn("observed_value", payload)
        self.assertEqual(payload["key"], "keywith-control")
        self.assertEqual(len(payload["subkey"]), 160)
        self.assertEqual(payload["disposition"], "superseded_equivalent")
        self.assertFalse(payload["transport_recovery_permitted"])

    def test_invalid_configuration_is_rejected_at_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "attempted_expiration"):
            self.classify(attempted_expiration=math.inf)
        with self.assertRaisesRegex(TypeError, "equivalent"):
            self.classify(equivalent=None)


if __name__ == "__main__":
    unittest.main()
