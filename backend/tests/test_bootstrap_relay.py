import argparse
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from bootstrap import _bootstrap_dht_kwargs


class BootstrapRelayConfigTests(unittest.TestCase):
    def test_public_bootstrap_forces_public_reachability(self) -> None:
        args = argparse.Namespace(
            host="0.0.0.0",
            port=7001,
            announce_maddr=["/ip4/203.0.113.10/tcp/7001"],
            identity_path="/var/lib/distribllm/bootstrap.id",
            use_relay=True,
        )

        kwargs = _bootstrap_dht_kwargs(args)

        self.assertEqual(kwargs["force_reachability"], "public")
        self.assertTrue(kwargs["use_relay"])
        self.assertEqual(kwargs["initial_peers"], [])
        self.assertFalse(kwargs["use_ipfs"])

    def test_local_bootstrap_keeps_automatic_reachability(self) -> None:
        args = argparse.Namespace(
            host="127.0.0.1",
            port=7001,
            announce_maddr=[],
            identity_path="bootstrap.id",
            use_relay=True,
        )

        self.assertIsNone(_bootstrap_dht_kwargs(args)["force_reachability"])


if __name__ == "__main__":
    unittest.main()
