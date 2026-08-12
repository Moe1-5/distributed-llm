import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from bootstrap import _bootstrap_dht_kwargs, _bootstrap_status, _write_status
from bootstrap_service_validate import validate_status


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

    def test_runtime_status_contains_non_secret_deployment_evidence(self) -> None:
        args = argparse.Namespace(
            host="0.0.0.0",
            port=7001,
            announce_maddr=["/ip4/203.0.113.10/tcp/7001"],
            identity_path="/var/lib/distribllm/bootstrap.id",
            use_relay=True,
            deployment_commit="abc1234",
        )

        with patch("bootstrap.os.getpid", return_value=4321):
            status = _bootstrap_status(
                args,
                "QmRelay",
                ["/ip4/203.0.113.10/tcp/7001/p2p/QmRelay"],
            )

        self.assertEqual(status["schema_version"], 1)
        self.assertEqual(status["peer_id"], "QmRelay")
        self.assertEqual(status["deployment_commit"], "abc1234")
        self.assertEqual(status["pid"], 4321)
        self.assertTrue(status["relay_enabled"])
        self.assertEqual(status["force_reachability"], "public")
        self.assertNotIn("private_key", status)

    def test_status_write_is_valid_json_with_restricted_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "bootstrap-status.json"
            _write_status(str(target), {"peer_id": "QmRelay"})

            self.assertEqual(json.loads(target.read_text()), {"peer_id": "QmRelay"})
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.assertEqual(list(target.parent.glob(".*.tmp")), [])

    def test_service_status_validator_accepts_expected_restart_evidence(self) -> None:
        status = {
            "schema_version": 1,
            "pid": 200,
            "peer_id": "QmRelay",
            "visible_maddrs": ["/ip4/203.0.113.10/tcp/7001/p2p/QmRelay"],
            "python_version": "3.12.3",
            "hivemind_version": "1.1.12",
            "deployment_commit": "abc1234",
            "identity_path": "/var/lib/distribllm/bootstrap.id",
            "relay_enabled": True,
            "force_reachability": "public",
            "port": 7001,
        }
        expected = {
            "peer_id": "QmRelay",
            "hivemind_version": "1.1.12",
            "deployment_commit": "abc1234",
            "identity_path": "/var/lib/distribllm/bootstrap.id",
            "public_maddr": "/ip4/203.0.113.10/tcp/7001/p2p/QmRelay",
            "port": 7001,
            "python_prefix": "3.12",
            "before_restart": {
                "pid": 100,
                "peer_id": "QmRelay",
                "identity_path": "/var/lib/distribllm/bootstrap.id",
            },
        }

        self.assertEqual(validate_status(status, expected), [])

    def test_service_status_validator_rejects_identity_and_relay_regressions(self) -> None:
        status = {
            "schema_version": 1,
            "pid": 100,
            "peer_id": "QmChanged",
            "visible_maddrs": [],
            "python_version": "3.13.0",
            "hivemind_version": "1.2.0",
            "deployment_commit": "wrong",
            "identity_path": "/tmp/new.id",
            "relay_enabled": False,
            "force_reachability": "private",
            "port": 9000,
        }
        errors = validate_status(
            status,
            {
                "peer_id": "QmRelay",
                "hivemind_version": "1.1.12",
                "deployment_commit": "abc1234",
                "identity_path": "/var/lib/distribllm/bootstrap.id",
                "public_maddr": "/ip4/203.0.113.10/tcp/7001/p2p/QmRelay",
                "port": 7001,
                "python_prefix": "3.12",
                "before_restart": {
                    "pid": 100,
                    "peer_id": "QmRelay",
                    "identity_path": "/var/lib/distribllm/bootstrap.id",
                },
            },
        )

        self.assertGreaterEqual(len(errors), 10)


if __name__ == "__main__":
    unittest.main()
