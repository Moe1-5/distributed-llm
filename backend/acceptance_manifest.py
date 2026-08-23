"""Validate one compatible artifact set for final live acceptance review."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from acceptance_evidence import EvidenceError, load_documents, write_private_json
from architecture_acceptance import validate_architecture_matrix

SCHEMA_VERSION = 1
REQUIRED_WINDOWS_CHECKS = (
    "windowsHost",
    "packagedApplication",
    "sourceCommitIdentified",
    "artifactIdentified",
    "wslAvailable",
    "distroPresent",
    "distroWsl2",
    "dependencySyncCompleted",
    "backendHealthReady",
    "backendStoppedCleanly",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _utc_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validate_windows_reports(
    reports: Sequence[Mapping[str, Any]],
    *,
    expected_app_version: str | None,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    versions: set[str] = set()
    source_commits: set[str] = set()
    artifact_hashes: set[str] = set()
    network_modes: list[str] = []
    if len(reports) < 2:
        errors.append("At least two Windows acceptance reports are required")

    for index, report in enumerate(reports, start=1):
        prefix = f"Windows report {index}"
        if report.get("schemaVersion") != 2:
            errors.append(f"{prefix} has an unsupported schema version")
        if report.get("ok") is not True:
            errors.append(f"{prefix} did not pass its managed WSL lifecycle")
        application = _mapping(report.get("application"))
        version = str(application.get("version", "")).strip()
        if version:
            versions.add(version)
        else:
            errors.append(f"{prefix} has no application version")
        if application.get("platform") != "win32":
            errors.append(f"{prefix} was not captured on Windows")
        if application.get("packaged") is not True:
            errors.append(f"{prefix} was not captured from a packaged application")
        source_commit = str(application.get("sourceCommit", "")).strip()
        if re.fullmatch(r"[0-9a-f]{40}", source_commit):
            source_commits.add(source_commit)
        else:
            errors.append(f"{prefix} has no valid source commit")
        if application.get("sourceDirty") is not False:
            errors.append(f"{prefix} was built from dirty tracked source")
        artifact_sha256 = str(application.get("artifactSha256", "")).strip()
        if re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
            artifact_hashes.add(artifact_sha256)
        else:
            errors.append(f"{prefix} has no valid executable SHA-256")
        artifact_bytes = _integer(application.get("artifactBytes"))
        if artifact_bytes is None or artifact_bytes <= 0:
            errors.append(f"{prefix} has no valid executable byte size")
        if expected_app_version and version != expected_app_version:
            errors.append(
                f"{prefix} application version is {version or '<missing>'}; "
                f"expected {expected_app_version}"
            )

        configuration = _mapping(report.get("configuration"))
        mode = str(configuration.get("networkMode", ""))
        network_modes.append(mode)
        if mode not in {"auto", "relay", "direct"}:
            errors.append(f"{prefix} has an invalid network mode")
        if configuration.get("syncDependencies") is not True:
            errors.append(f"{prefix} did not enable dependency synchronization")
        if mode in {"auto", "relay"}:
            initial_peer_count = _integer(configuration.get("initialPeerCount"))
            trusted_relay_count = _integer(configuration.get("trustedRelayCount"))
            if initial_peer_count is None or initial_peer_count <= 0:
                errors.append(f"{prefix} has no configured bootstrap peer")
            if trusted_relay_count is None or trusted_relay_count <= 0:
                errors.append(f"{prefix} has no configured trusted relay")

        checks = _mapping(report.get("checks"))
        missing_checks = [
            field for field in REQUIRED_WINDOWS_CHECKS if checks.get(field) is not True
        ]
        if missing_checks:
            errors.append(f"{prefix} has incomplete checks: {', '.join(missing_checks)}")
        launcher = _mapping(report.get("launcher"))
        current_status = _mapping(launcher.get("currentStatus"))
        if current_status.get("state") != "idle":
            errors.append(f"{prefix} was not exported after a clean stop")

    if len(versions) > 1:
        errors.append("Windows reports use different application versions")
    if len(source_commits) > 1:
        errors.append("Windows reports use different source commits")
    if len(artifact_hashes) > 1:
        errors.append("Windows reports use different executable hashes")
    return errors, {
        "report_count": len(reports),
        "application_versions": sorted(versions),
        "source_commits": sorted(source_commits),
        "artifact_sha256": sorted(artifact_hashes),
        "network_modes": network_modes,
    }


def _validate_vps_and_probe(
    vps_report: Mapping[str, Any],
    relay_probe: Mapping[str, Any],
    *,
    expected_hivemind_version: str,
    max_relay_seconds: float,
    vps_report_sha256: str,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if (
        vps_report.get("schema_version") != 1
        or vps_report.get("kind") != "vps_bootstrap_validation"
    ):
        errors.append("VPS validation has an unsupported schema or kind")
    if vps_report.get("ok") is not True:
        errors.append("VPS service validation did not pass")
    checks = _mapping(vps_report.get("checks"))
    if checks.get("runtime_status_valid") is not True:
        errors.append("VPS runtime status validation did not pass")
    if checks.get("relay_flags_observed") is not True:
        errors.append("VPS relay flags were not observed after validation")
    restart = _mapping(vps_report.get("restart"))
    if restart.get("requested") is not True or restart.get("passed") is not True:
        errors.append("VPS restart continuity was not validated")
    if restart.get("identity_hash_preserved") is not True:
        errors.append("VPS identity hash continuity was not validated")

    status = _mapping(vps_report.get("status"))
    relay_peer_id = str(status.get("peer_id", "")).strip()
    visible_maddrs = _string_list(status.get("visible_maddrs"))
    if not relay_peer_id:
        errors.append("VPS validation has no relay peer ID")
    if status.get("hivemind_version") != expected_hivemind_version:
        errors.append("VPS Hivemind version does not match the expected runtime")

    if relay_probe.get("ok") is not True:
        errors.append("Post-restart relay probe did not pass")
    if relay_probe.get("hivemind_version") != expected_hivemind_version:
        errors.append("Relay probe Hivemind version does not match the expected runtime")
    if relay_probe.get("force_reachability") != "private":
        errors.append("Relay probe did not force private reachability")
    if relay_probe.get("relay_discovery") is not False:
        errors.append("Relay probe did not use static trusted-relay selection")
    try:
        elapsed = float(relay_probe.get("elapsed_seconds"))
    except (TypeError, ValueError):
        elapsed = -1.0
    if not math.isfinite(elapsed) or elapsed < 0 or elapsed > max_relay_seconds:
        errors.append(
            f"Relay reservation took {elapsed:g} seconds; maximum is {max_relay_seconds:g}"
        )

    if not re.fullmatch(r"[0-9a-f]{64}", vps_report_sha256):
        errors.append("VPS validation report SHA-256 is invalid")
    if relay_probe.get("validation_context_sha256") != vps_report_sha256:
        errors.append("Relay probe is not bound to the supplied VPS validation report")

    initial_peers = set(_string_list(relay_probe.get("initial_peers")))
    trusted_relays = set(_string_list(relay_probe.get("trusted_relays")))
    known_relays = set(visible_maddrs) & initial_peers & trusted_relays
    if not known_relays:
        errors.append("Relay probe does not reference the validated VPS multiaddress")
    probe_peer_id = str(relay_probe.get("peer_id", "")).strip()
    circuit_maddrs = _string_list(relay_probe.get("circuit_maddrs"))
    expected_circuits = {
        f"{relay}/p2p-circuit/p2p/{probe_peer_id}"
        for relay in known_relays
        if probe_peer_id
    }
    if not expected_circuits.intersection(circuit_maddrs):
        errors.append("Relay probe has no complete circuit through the validated VPS")

    return errors, {
        "peer_id": relay_peer_id,
        "deployment_commit": status.get("deployment_commit"),
        "hivemind_version": status.get("hivemind_version"),
        "relay_probe_peer_id": probe_peer_id or None,
        "relay_probe_elapsed_seconds": elapsed,
        "validation_context_sha256": vps_report_sha256,
    }


def _validate_inference_report(
    report: Mapping[str, Any],
    *,
    label: str,
    model_name: str,
    expected_mode: str,
    expected_incentives: str = "shadow",
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    if report.get("schema_version") != 1:
        errors.append(f"{label} report has an unsupported schema version")
    if report.get("ok") is not True:
        errors.append(f"{label} inference report did not pass")
    if report.get("model_name") != model_name:
        errors.append(f"{label} report model does not match {model_name}")
    if report.get("expected_mode") != expected_mode:
        errors.append(f"{label} report does not validate {expected_mode} mode")
    if report.get("expected_incentives") != expected_incentives:
        errors.append(
            f"{label} report does not validate {expected_incentives} incentives"
        )

    participants = _string_list(report.get("participants"))
    if len(set(participants)) < 2:
        errors.append(f"{label} report has fewer than two participant labels")
    raw_route = report.get("selected_route")
    route = (
        [item for item in raw_route if isinstance(item, Mapping)]
        if isinstance(raw_route, list)
        else []
    )
    if len({str(item.get("peer_id", "")) for item in route if item.get("peer_id")}) < 2:
        errors.append(f"{label} report route has fewer than two peers")
    cursor = 0
    for item in route:
        try:
            start = int(item["layer_start"])
            end = int(item["layer_end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{label} report contains an invalid route range")
            break
        if start != cursor or end <= start:
            errors.append(f"{label} report route is not exactly adjacent from layer zero")
            break
        if item.get("connection_mode") != expected_mode:
            errors.append(f"{label} report route contains a non-{expected_mode} provider")
        cursor = end

    return errors, {
        "participants": participants,
        "expected_incentives": report.get("expected_incentives"),
        "selected_route": [
            {
                "peer_id": item.get("peer_id"),
                "layer_start": item.get("layer_start"),
                "layer_end": item.get("layer_end"),
            }
            for item in route
        ],
    }


def validate_acceptance_set(
    *,
    windows_reports: Sequence[Mapping[str, Any]],
    vps_report: Mapping[str, Any],
    relay_probe: Mapping[str, Any],
    relay_report: Mapping[str, Any],
    direct_report: Mapping[str, Any],
    model_name: str,
    expected_app_version: str | None = None,
    expected_hivemind_version: str = "1.1.12",
    max_relay_seconds: float = 90.0,
    vps_report_sha256: str,
    architecture_report: Mapping[str, Any] | None = None,
    incentives_off_report: Mapping[str, Any] | None = None,
    incentives_off_report_sha256: str | None = None,
) -> dict[str, Any]:
    if not model_name.strip():
        raise EvidenceError("Model name must not be empty")
    if not math.isfinite(max_relay_seconds) or max_relay_seconds <= 0:
        raise EvidenceError("max_relay_seconds must be positive")

    errors, windows_summary = _validate_windows_reports(
        windows_reports,
        expected_app_version=expected_app_version,
    )
    vps_errors, vps_summary = _validate_vps_and_probe(
        vps_report,
        relay_probe,
        expected_hivemind_version=expected_hivemind_version,
        max_relay_seconds=max_relay_seconds,
        vps_report_sha256=vps_report_sha256,
    )
    relay_errors, relay_summary = _validate_inference_report(
        relay_report,
        label="Relay",
        model_name=model_name,
        expected_mode="relay",
    )
    direct_errors, direct_summary = _validate_inference_report(
        direct_report,
        label="Direct",
        model_name=model_name,
        expected_mode="direct",
    )
    errors.extend(vps_errors)
    errors.extend(relay_errors)
    errors.extend(direct_errors)
    if set(relay_summary["participants"]) != set(direct_summary["participants"]):
        errors.append("Relay and direct reports use different participant labels")

    architecture_summary: dict[str, Any] | None = None
    incentives_off_summary: dict[str, Any] | None = None
    if architecture_report is not None:
        if incentives_off_report is None:
            errors.append(
                "Architecture acceptance requires the incentives-off relay report"
            )
        else:
            off_errors, incentives_off_summary = _validate_inference_report(
                incentives_off_report,
                label="Incentives-off relay",
                model_name=model_name,
                expected_mode="relay",
                expected_incentives="off",
            )
            errors.extend(off_errors)
            if set(incentives_off_summary["participants"]) != set(
                relay_summary["participants"]
            ):
                errors.append(
                    "Incentives-off and shadow relay reports use different participant labels"
                )
            off_validated_at = _utc_timestamp(incentives_off_report.get("validated_at"))
            shadow_validated_at = _utc_timestamp(relay_report.get("validated_at"))
            if off_validated_at is None or shadow_validated_at is None:
                errors.append(
                    "Incentives-off and shadow relay reports require timezone-aware validation timestamps"
                )
            elif off_validated_at >= shadow_validated_at:
                errors.append(
                    "Incentives-off relay evidence was not validated before shadow relay evidence"
                )

        source_commits = windows_summary["source_commits"]
        architecture_summary = validate_architecture_matrix(
            architecture_report,
            expected_source_commit=(source_commits[0] if len(source_commits) == 1 else None),
            expected_hivemind_version=expected_hivemind_version,
        )
        errors.extend(
            f"Architecture: {error}" for error in architecture_summary["errors"]
        )
        expected_off_hash = _mapping(
            _mapping(architecture_report.get("topologies")).get("incentives_off")
        ).get("evidence_sha256")
        if not _valid_sha256(incentives_off_report_sha256):
            errors.append("Incentives-off relay report SHA-256 is missing or invalid")
        elif incentives_off_report_sha256 != expected_off_hash:
            errors.append(
                "Architecture incentives-off evidence hash does not match the supplied relay report"
            )

    return {
        "schema_version": 2 if architecture_report is not None else SCHEMA_VERSION,
        "kind": "cross_sprint_live_acceptance",
        "validated_at": _utc_now(),
        "ok": not errors,
        "final_approval": "pending_manual_review" if not errors else "not_ready",
        "model_name": model_name,
        "windows": windows_summary,
        "vps": vps_summary,
        "relay_inference": relay_summary,
        "direct_inference": direct_summary,
        "incentives_off_inference": incentives_off_summary,
        "incentives_off_report_sha256": (
            incentives_off_report_sha256
            if _valid_sha256(incentives_off_report_sha256)
            else None
        ),
        "architecture": architecture_summary,
        "manual_gates": [
            "Confirm the reports came from two separate physical Windows devices.",
            "Confirm each device launched the reviewed portable artifact hash.",
            "Review generated output and monitoring on both devices.",
            "Explicitly approve sprint closure and any move from shadow to credit mode.",
            *(
                ["Confirm failure domains refer to independent physical hosts or providers."]
                if architecture_report is not None
                else []
            ),
        ],
        "errors": errors,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-report", action="append", type=Path, required=True)
    parser.add_argument("--vps-report", type=Path, required=True)
    parser.add_argument("--relay-probe", type=Path, required=True)
    parser.add_argument("--relay-report", type=Path, required=True)
    parser.add_argument("--direct-report", type=Path, required=True)
    parser.add_argument("--incentives-off-report", type=Path)
    parser.add_argument("--architecture-report", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--expected-app-version")
    parser.add_argument("--expected-hivemind-version", default="1.1.12")
    parser.add_argument("--max-relay-seconds", type=float, default=90.0)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        windows_reports = load_documents(args.windows_report)
        try:
            vps_report_sha256 = hashlib.sha256(
                args.vps_report.expanduser().read_bytes()
            ).hexdigest()
        except OSError as exc:
            raise EvidenceError(
                f"Could not hash VPS report {args.vps_report}: {exc}"
            ) from exc
        vps_report, relay_probe, relay_report, direct_report = load_documents(
            [args.vps_report, args.relay_probe, args.relay_report, args.direct_report]
        )
        architecture_report = None
        if args.architecture_report:
            (architecture_report,) = load_documents([args.architecture_report])
        incentives_off_report = None
        incentives_off_report_sha256 = None
        if args.incentives_off_report:
            try:
                incentives_off_report_sha256 = hashlib.sha256(
                    args.incentives_off_report.expanduser().read_bytes()
                ).hexdigest()
            except OSError as exc:
                raise EvidenceError(
                    "Could not hash incentives-off report "
                    f"{args.incentives_off_report}: {exc}"
                ) from exc
            (incentives_off_report,) = load_documents([args.incentives_off_report])
        report = validate_acceptance_set(
            windows_reports=windows_reports,
            vps_report=vps_report,
            relay_probe=relay_probe,
            relay_report=relay_report,
            direct_report=direct_report,
            model_name=args.model,
            expected_app_version=args.expected_app_version,
            expected_hivemind_version=args.expected_hivemind_version,
            max_relay_seconds=args.max_relay_seconds,
            vps_report_sha256=vps_report_sha256,
            architecture_report=architecture_report,
            incentives_off_report=incentives_off_report,
            incentives_off_report_sha256=incentives_off_report_sha256,
        )
        write_private_json(args.output, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1
    except EvidenceError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
