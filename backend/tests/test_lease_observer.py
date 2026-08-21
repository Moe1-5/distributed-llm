import sys
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from lease_observer import observe_once


class Result:
    def __init__(self, value, expiration_time: float) -> None:
        self.value = value
        self.expiration_time = expiration_time


class Expert:
    def __init__(self, peer_id: str) -> None:
        self.peer_id = peer_id


class DHT:
    peer_id = "observer"

    def __init__(self, metadata_expiration: float = 150.0) -> None:
        self.metadata_expiration = metadata_expiration

    def get(self, key: str, latest: bool = True):
        if key == "test.members.v2":
            return Result(
                {
                    "worker": Result(
                        {"peer_id": "worker", "timestamp": 99.0},
                        150.0,
                    )
                },
                150.0,
            )
        if key == "test.members":
            return None
        if key == "test.node_info.worker":
            return Result(
                {
                    "peer_id": "worker",
                    "node_id": "node-1",
                    "model_name": "facebook/opt-125m",
                    "layer_start": 0,
                    "layer_end": 12,
                    "running": True,
                    "rpc_running": True,
                    "rpc_uid": "test.0.12",
                    "receipt_rpc_uid": "test.999999.0.12",
                },
                self.metadata_expiration,
            )
        return None


class LeaseObserverTests(unittest.TestCase):
    def test_observer_requires_member_metadata_and_both_experts(self) -> None:
        with (
            patch("lease_observer.get_dht_time", return_value=100.0),
            patch(
                "lease_observer.get_experts",
                return_value=[Expert("worker"), Expert("worker")],
            ) as lookup,
        ):
            snapshot = observe_once(
                DHT(),
                dht_prefix="test",
                model_name="facebook/opt-125m",
                layer_start=0,
                layer_end=12,
                expected_peer="worker",
                required_horizon_seconds=20.0,
                require_receipt=True,
            )

        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["observer_peer_id"], "observer")
        self.assertEqual(snapshot["providers"][0]["member_source"], "members.v2")
        lookup.assert_called_once_with(
            ANY,
            ["test.0.12", "test.999999.0.12"],
            expiration_time=120.0,
        )

    def test_observer_fails_short_metadata_horizon_and_wrong_expert_owner(self) -> None:
        with (
            patch("lease_observer.get_dht_time", return_value=100.0),
            patch(
                "lease_observer.get_experts",
                return_value=[Expert("other-peer")],
            ),
        ):
            snapshot = observe_once(
                DHT(metadata_expiration=110.0),
                dht_prefix="test",
                model_name="facebook/opt-125m",
                layer_start=0,
                layer_end=12,
                expected_peer="worker",
                required_horizon_seconds=20.0,
                require_receipt=False,
            )

        self.assertFalse(snapshot["ok"])
        self.assertTrue(any("did not resolve" in reason for reason in snapshot["reasons"]))
        self.assertTrue(any("metadata" in reason for reason in snapshot["reasons"]))


if __name__ == "__main__":
    unittest.main()
