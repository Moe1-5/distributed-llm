import json
import hashlib
import stat
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from acceptance_manifest import main, validate_acceptance_set
from tests.test_architecture_acceptance import matrix as architecture_matrix

RELAY = "/ip4/203.0.113.10/tcp/7001/p2p/QmRelay"
VPS_REPORT_SHA256 = "a" * 64
SOURCE_COMMIT = "b" * 40
ARTIFACT_SHA256 = "c" * 64


def windows_report(
    *,
    version: str = "1.0.0",
    mode: str = "auto",
    source_commit: str = SOURCE_COMMIT,
    artifact_sha256: str = ARTIFACT_SHA256,
) -> dict:
    return {
        "schemaVersion": 2,
        "capturedAt": "2026-08-13T00:00:00.000Z",
        "ok": True,
        "application": {
            "version": version,
            "packaged": True,
            "platform": "win32",
            "arch": "x64",
            "sourceCommit": source_commit,
            "sourceDirty": False,
            "artifactFileName": "DistribLLM-1.0.0-portable.exe",
            "artifactSha256": artifact_sha256,
            "artifactBytes": 87652373,
        },
        "configuration": {
            "distroName": "Ubuntu",
            "backendPathConfigured": True,
            "backendUrl": "http://127.0.0.1:8000",
            "syncDependencies": True,
            "networkMode": mode,
            "initialPeerCount": 1,
            "trustedRelayCount": 1,
            "relayWaitTimeoutSeconds": 90,
        },
        "launcher": {
            "currentStatus": {
                "state": "idle",
                "diagnosticCode": None,
                "updatedAt": "2026-08-13T00:00:00.000Z",
            },
            "transitions": [],
        },
        "checks": {
            "windowsHost": True,
            "packagedApplication": True,
            "sourceCommitIdentified": True,
            "artifactIdentified": True,
            "wslAvailable": True,
            "distroPresent": True,
            "distroWsl2": True,
            "dependencySyncCompleted": True,
            "backendHealthReady": True,
            "backendStoppedCleanly": True,
        },
    }


def vps_report() -> dict:
    return {
        "schema_version": 1,
        "kind": "vps_bootstrap_validation",
        "validated_at": "2026-08-13T00:00:00+00:00",
        "ok": True,
        "checks": {
            "runtime_status_valid": True,
            "relay_flags_observed": True,
        },
        "restart": {
            "requested": True,
            "passed": True,
            "identity_hash_preserved": True,
        },
        "errors": [],
        "status": {
            "peer_id": "QmRelay",
            "deployment_commit": "abc1234",
            "hivemind_version": "1.1.12",
            "visible_maddrs": [RELAY],
        },
    }


def relay_probe() -> dict:
    return {
        "ok": True,
        "peer_id": "QmWorker",
        "circuit_maddrs": [f"{RELAY}/p2p-circuit/p2p/QmWorker"],
        "visible_maddrs": [f"{RELAY}/p2p-circuit/p2p/QmWorker"],
        "elapsed_seconds": 1.5,
        "initial_peers": [RELAY],
        "trusted_relays": [RELAY],
        "python_version": "3.12.3",
        "hivemind_version": "1.1.12",
        "force_reachability": "private",
        "relay_discovery": False,
        "validation_context_sha256": VPS_REPORT_SHA256,
        "error": None,
    }


def inference_report(mode: str, incentives_mode: str = "shadow") -> dict:
    return {
        "schema_version": 1,
        "validated_at": (
            "2026-08-13T00:00:00+00:00"
            if incentives_mode == "off"
            else "2026-08-13T00:01:00+00:00"
        ),
        "ok": True,
        "model_name": "facebook/opt-125m",
        "expected_mode": mode,
        "expected_incentives": incentives_mode,
        "participants": ["device-a", "device-b"],
        "selected_route": [
            {
                "peer_id": "head",
                "layer_start": 0,
                "layer_end": 6,
                "connection_mode": mode,
            },
            {
                "peer_id": "tail",
                "layer_start": 6,
                "layer_end": 12,
                "connection_mode": mode,
            },
        ],
        "standby_nonpayment_verified": [],
        "errors": [],
    }


def validate(**overrides) -> dict:
    inputs = {
        "windows_reports": [windows_report(), windows_report()],
        "vps_report": vps_report(),
        "relay_probe": relay_probe(),
        "relay_report": inference_report("relay"),
        "direct_report": inference_report("direct"),
        "model_name": "facebook/opt-125m",
        "expected_app_version": "1.0.0",
        "vps_report_sha256": VPS_REPORT_SHA256,
    }
    inputs.update(overrides)
    return validate_acceptance_set(**inputs)


class AcceptanceManifestTests(unittest.TestCase):
    def test_compatible_artifact_set_is_ready_only_for_manual_review(self) -> None:
        report = validate()

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["final_approval"], "pending_manual_review")
        self.assertEqual(report["windows"]["report_count"], 2)
        self.assertEqual(report["windows"]["source_commits"], [SOURCE_COMMIT])
        self.assertEqual(report["windows"]["artifact_sha256"], [ARTIFACT_SHA256])
        self.assertEqual(report["vps"]["peer_id"], "QmRelay")
        self.assertEqual(len(report["manual_gates"]), 4)

    def test_rejects_incompatible_windows_and_inference_reports(self) -> None:
        mismatched_direct = inference_report("direct")
        mismatched_direct["participants"] = ["device-a", "device-c"]
        report = validate(
            windows_reports=[windows_report(), windows_report(version="2.0.0")],
            direct_report=mismatched_direct,
        )

        self.assertFalse(report["ok"])
        rendered = "\n".join(report["errors"])
        self.assertIn("application version is 2.0.0", rendered)
        self.assertIn("different application versions", rendered)
        self.assertIn("different participant labels", rendered)

    def test_rejects_windows_reports_from_different_executables_or_commits(self) -> None:
        report = validate(
            windows_reports=[
                windows_report(),
                windows_report(source_commit="d" * 40, artifact_sha256="e" * 64),
            ]
        )

        self.assertFalse(report["ok"])
        self.assertIn("Windows reports use different source commits", report["errors"])
        self.assertIn("Windows reports use different executable hashes", report["errors"])

    def test_rejects_probe_not_bound_to_validated_vps(self) -> None:
        probe = relay_probe()
        probe["trusted_relays"] = [
            "/ip4/198.51.100.20/tcp/7001/p2p/QmDifferent"
        ]
        probe["elapsed_seconds"] = 91
        report = validate(relay_probe=probe)

        self.assertFalse(report["ok"])
        rendered = "\n".join(report["errors"])
        self.assertIn("maximum is 90", rendered)
        self.assertIn("does not reference the validated VPS", rendered)
        self.assertIn("no complete circuit through the validated VPS", rendered)

    def test_rejects_vps_report_without_completed_restart(self) -> None:
        vps = vps_report()
        vps["restart"]["passed"] = False
        vps["restart"]["identity_hash_preserved"] = False
        report = validate(vps_report=vps)

        self.assertFalse(report["ok"])
        rendered = "\n".join(report["errors"])
        self.assertIn("restart continuity was not validated", rendered)
        self.assertIn("identity hash continuity was not validated", rendered)

    def test_rejects_relay_probe_from_another_vps_validation_run(self) -> None:
        probe = relay_probe()
        probe["validation_context_sha256"] = "b" * 64
        report = validate(relay_probe=probe)

        self.assertFalse(report["ok"])
        self.assertIn(
            "Relay probe is not bound to the supplied VPS validation report",
            report["errors"],
        )

    def test_architecture_matrix_is_bound_to_windows_artifact_commit(self) -> None:
        architecture = architecture_matrix()
        architecture["participant_source_commit"] = SOURCE_COMMIT
        for component in architecture["components"]:
            component["deployment_commit"] = SOURCE_COMMIT
        off_report_hash = "f" * 64
        architecture["topologies"]["incentives_off"][
            "evidence_sha256"
        ] = off_report_hash

        report = validate(
            architecture_report=architecture,
            incentives_off_report=inference_report("relay", "off"),
            incentives_off_report_sha256=off_report_hash,
        )

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["schema_version"], 2)
        self.assertTrue(report["architecture"]["ok"])
        self.assertEqual(len(report["manual_gates"]), 5)

        architecture["participant_source_commit"] = "d" * 40
        rejected = validate(
            architecture_report=architecture,
            incentives_off_report=inference_report("relay", "off"),
            incentives_off_report_sha256=off_report_hash,
        )
        self.assertFalse(rejected["ok"])
        self.assertTrue(
            any("does not match the Windows artifact" in error for error in rejected["errors"])
        )

    def test_architecture_requires_hash_bound_incentives_off_report(self) -> None:
        architecture = architecture_matrix()
        architecture["participant_source_commit"] = SOURCE_COMMIT
        for component in architecture["components"]:
            component["deployment_commit"] = SOURCE_COMMIT

        missing = validate(architecture_report=architecture)
        self.assertFalse(missing["ok"])
        self.assertIn(
            "Architecture acceptance requires the incentives-off relay report",
            missing["errors"],
        )

        mismatched = validate(
            architecture_report=architecture,
            incentives_off_report=inference_report("relay", "off"),
            incentives_off_report_sha256="f" * 64,
        )
        self.assertFalse(mismatched["ok"])
        self.assertIn(
            "Architecture incentives-off evidence hash does not match the supplied relay report",
            mismatched["errors"],
        )

        wrong_mode = validate(
            architecture_report=architecture,
            incentives_off_report=inference_report("relay", "shadow"),
            incentives_off_report_sha256="b" * 64,
        )
        self.assertFalse(wrong_mode["ok"])
        self.assertIn(
            "Incentives-off relay report does not validate off incentives",
            wrong_mode["errors"],
        )

        out_of_order = inference_report("relay", "off")
        out_of_order["validated_at"] = "2026-08-13T00:02:00+00:00"
        rejected_order = validate(
            architecture_report=architecture,
            incentives_off_report=out_of_order,
            incentives_off_report_sha256="b" * 64,
        )
        self.assertFalse(rejected_order["ok"])
        self.assertIn(
            "Incentives-off relay evidence was not validated before shadow relay evidence",
            rejected_order["errors"],
        )

    def test_malformed_numeric_evidence_becomes_errors_instead_of_exceptions(self) -> None:
        windows = windows_report()
        windows["configuration"]["initialPeerCount"] = "invalid"
        probe = relay_probe()
        probe["elapsed_seconds"] = float("nan")
        report = validate(windows_reports=[windows, windows_report()], relay_probe=probe)

        self.assertFalse(report["ok"])
        rendered = "\n".join(report["errors"])
        self.assertIn("has no configured bootstrap peer", rendered)
        self.assertIn("Relay reservation took nan seconds", rendered)

    def test_cli_writes_restricted_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = {
                "windows-a.json": windows_report(),
                "windows-b.json": windows_report(),
                "vps.json": vps_report(),
                "probe.json": relay_probe(),
                "relay.json": inference_report("relay"),
                "direct.json": inference_report("direct"),
            }
            for name, document in inputs.items():
                (root / name).write_text(json.dumps(document), encoding="utf-8")
            inputs["probe.json"]["validation_context_sha256"] = hashlib.sha256(
                (root / "vps.json").read_bytes()
            ).hexdigest()
            (root / "probe.json").write_text(
                json.dumps(inputs["probe.json"]), encoding="utf-8"
            )
            output = root / "final.json"

            exit_code = main(
                [
                    "--windows-report",
                    str(root / "windows-a.json"),
                    "--windows-report",
                    str(root / "windows-b.json"),
                    "--vps-report",
                    str(root / "vps.json"),
                    "--relay-probe",
                    str(root / "probe.json"),
                    "--relay-report",
                    str(root / "relay.json"),
                    "--direct-report",
                    str(root / "direct.json"),
                    "--model",
                    "facebook/opt-125m",
                    "--expected-app-version",
                    "1.0.0",
                    "--output",
                    str(output),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(json.loads(output.read_text())["ok"])
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
