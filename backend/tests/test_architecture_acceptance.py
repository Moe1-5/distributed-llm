import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from architecture_acceptance import main, validate_architecture_matrix

COMMIT = "a" * 40
EVIDENCE = "b" * 64


def matrix() -> dict:
    components = []
    for role, count in (("dht", 2), ("relay", 2), ("coordinator", 1), ("settlement", 1)):
        for index in range(count):
            components.append(
                {
                    "role": role,
                    "instance_id": f"{role}-{index + 1}",
                    "failure_domain": f"provider-{role}-{index + 1}",
                    "peer_id": f"peer-{role}-{index + 1}" if role in {"dht", "relay"} else None,
                    "deployment_commit": COMMIT,
                    "service_protocol_version": 1,
                }
            )
    topologies = {
        name: {"ok": True, "evidence_sha256": EVIDENCE}
        for name in (
            "direct",
            "relay",
            "complementary_split",
            "redundant",
            "session",
            "package",
            "incentives_off",
            "shadow_accounting",
        )
    }
    topologies["complementary_split"]["complete_alternate"] = False
    topologies["redundant"].update(complete_alternate=True, provider_count=3)
    return {
        "schema_version": 1,
        "kind": "distributed_architecture_failure_matrix",
        "participant_source_commit": COMMIT,
        "protocols": {
            "hivemind_version": "1.1.12",
            "infrastructure_protocol_version": 1,
            "expert_uid_protocol": "peer-addressed-v1",
            "placement_protocol_version": 1,
            "session_protocol_version": 1,
            "receipt_protocol_version": 1,
        },
        "components": components,
        "scenarios": {
            name: {"ok": True, "evidence_sha256": EVIDENCE}
            for name in (
                "coordinator_outage",
                "dht_loss",
                "discovery_before_roles",
                "relay_loss",
                "worker_loss",
                "generator_recovery",
                "packaged_lifecycle",
                "placement_race",
                "role_identity",
                "service_restart_isolation",
            )
        },
        "topologies": topologies,
        "rollout_phases": {
            "incentives_off_passed": True,
            "shadow_passed": True,
            "shadow_started_after_off": True,
        },
    }


class ArchitectureAcceptanceTests(unittest.TestCase):
    def test_complete_independent_failure_matrix_passes(self) -> None:
        report = validate_architecture_matrix(matrix(), expected_source_commit=COMMIT)

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(len(report["components"]), 6)

    def test_rejects_colocated_or_incomplete_redundancy(self) -> None:
        document = matrix()
        document["components"][1]["failure_domain"] = document["components"][0]["failure_domain"]
        document["components"][3]["peer_id"] = document["components"][2]["peer_id"]
        document["topologies"]["redundant"]["provider_count"] = 2
        document["topologies"]["redundant"]["complete_alternate"] = False

        report = validate_architecture_matrix(document)

        self.assertFalse(report["ok"])
        rendered = "\n".join(report["errors"])
        self.assertIn("independent failure domains", rendered)
        self.assertIn("distinct peer identities", rendered)
        self.assertIn("at least three providers", rendered)

    def test_rejects_protocol_mismatch_and_unordered_shadow(self) -> None:
        document = matrix()
        document["protocols"]["hivemind_version"] = "1.2.0"
        document["rollout_phases"]["shadow_started_after_off"] = False

        report = validate_architecture_matrix(document)

        self.assertFalse(report["ok"])
        self.assertTrue(any("hivemind_version" in error for error in report["errors"]))
        self.assertIn(
            "Shadow acceptance was not ordered after incentives-off acceptance",
            report["errors"],
        )

    def test_cli_writes_private_validation_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "matrix.json"
            output = root / "validated.json"
            source.write_text(json.dumps(matrix()), encoding="utf-8")

            exit_code = main(
                [
                    "--matrix",
                    str(source),
                    "--expected-source-commit",
                    COMMIT,
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(json.loads(output.read_text())["ok"])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
