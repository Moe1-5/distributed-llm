import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from network.infrastructure import summarize_infrastructure


class InfrastructureConfigTests(unittest.TestCase):
    def test_ordered_independent_candidates_are_reported_as_configured(self) -> None:
        summary = summarize_infrastructure(
            ["dht-b", "dht-a"],
            ["relay-b", "relay-a"],
            network_state="ready",
        )

        self.assertEqual(summary["runtime_state"], "ready")
        self.assertEqual(summary["configuration_state"], "redundant_configured")
        self.assertEqual(summary["dht"]["ordered_peers"], ["dht-b", "dht-a"])
        self.assertEqual(summary["relay"]["ordered_relays"], ["relay-b", "relay-a"])

    def test_single_combined_peer_is_explicitly_degraded(self) -> None:
        address = "combined-peer"
        summary = summarize_infrastructure(
            [address, address],
            [address],
            network_state="ready",
        )

        self.assertEqual(summary["runtime_state"], "degraded")
        self.assertEqual(summary["configuration_state"], "single_failure_domain")
        self.assertEqual(summary["dht"]["duplicate_count"], 1)

    def test_runtime_failure_cannot_be_hidden_by_redundant_configuration(self) -> None:
        summary = summarize_infrastructure(
            ["dht-a", "dht-b"],
            ["relay-a", "relay-b"],
            network_state="degraded",
        )

        self.assertEqual(summary["runtime_state"], "degraded")


if __name__ == "__main__":
    unittest.main()
