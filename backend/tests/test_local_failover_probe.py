import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from local_failover_probe import (
    LocalFailoverProbeError,
    ProbeOptions,
    _node_evidence,
    _validate_options,
)


class LocalFailoverProbeTests(unittest.TestCase):
    def test_default_options_use_registered_opt_dimensions(self) -> None:
        self.assertEqual(_validate_options(ProbeOptions()), (12, 768))

    def test_probe_bounds_must_be_positive(self) -> None:
        with self.assertRaisesRegex(LocalFailoverProbeError, "sequence_length"):
            _validate_options(ProbeOptions(sequence_length=0))
        with self.assertRaisesRegex(LocalFailoverProbeError, "route_timeout"):
            _validate_options(ProbeOptions(route_timeout=0))

    def test_node_evidence_excludes_transport_addresses(self) -> None:
        evidence = _node_evidence(
            {
                "peer_id": "peer",
                "rpc_uid": "rpc.0.12",
                "layer_start": 0,
                "layer_end": 12,
                "maddrs": ["/ip4/127.0.0.1/tcp/1234"],
            }
        )
        self.assertEqual(evidence["peer_id"], "peer")
        self.assertNotIn("maddrs", evidence)


if __name__ == "__main__":
    unittest.main()
