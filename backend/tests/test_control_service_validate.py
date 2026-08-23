import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from control_service_validate import build_report, validate_health


class ControlServiceValidationTests(unittest.TestCase):
    def test_coordinator_health_and_restart_pass(self) -> None:
        health = {
            "status": "ok",
            "component_role": "coordinator",
            "service_protocol_version": 1,
            "schema_version": 1,
            "deployment_commit": "abc1234",
            "failure_domain": "provider-one",
            "topology_revision": 7,
        }
        errors = validate_health(
            health,
            role="coordinator",
            deployment_commit="abc1234",
            failure_domain="provider-one",
        )
        report = build_report(
            health,
            errors,
            role="coordinator",
            restart_requested=True,
            restart_pid_changed=True,
            journal_observed=True,
        )

        self.assertTrue(report["ok"])
        self.assertTrue(report["restart"]["passed"])

    def test_settlement_protocol_drift_and_missing_journal_fail(self) -> None:
        health = {
            "status": "ok",
            "component_role": "settlement",
            "service_protocol_version": 1,
            "schema_version": 1,
            "deployment_commit": "abc1234",
            "failure_domain": "provider-two",
            "receipt_protocol_version": 2,
            "reward_version": 1,
            "mode": "shadow",
        }
        errors = validate_health(
            health,
            role="settlement",
            deployment_commit="abc1234",
            failure_domain="provider-two",
        )
        report = build_report(
            health,
            errors,
            role="settlement",
            restart_requested=False,
            restart_pid_changed=False,
            journal_observed=False,
        )

        self.assertFalse(report["ok"])
        self.assertIn("Settlement receipt protocol version is unsupported", errors)


if __name__ == "__main__":
    unittest.main()
