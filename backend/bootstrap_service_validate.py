"""Validate non-secret runtime evidence from the managed VPS relay service."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def validate_status(status: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if status.get("schema_version") not in {1, 2}:
        errors.append("Unsupported or missing bootstrap status schema.")
    expected_role = expected.get("role")
    if expected_role:
        if status.get("schema_version") != 2:
            errors.append("Role-specific infrastructure requires status schema version 2.")
        if status.get("infrastructure_protocol_version") != 1:
            errors.append("Unsupported infrastructure protocol version.")
        if status.get("role") != expected_role:
            errors.append(
                f"Unexpected infrastructure role: expected {expected_role!r}, "
                f"got {status.get('role')!r}."
            )
        expected_relay = expected_role in {"relay", "combined"}
        expected_storage = expected_role in {"dht", "combined"}
        if status.get("relay_enabled") is not expected_relay:
            errors.append("Infrastructure relay responsibility does not match its role.")
        if status.get("dht_storage_enabled") is not expected_storage:
            errors.append("Infrastructure DHT storage responsibility does not match its role.")
        if expected_role == "relay" and not status.get("initial_peers"):
            errors.append("Relay has no configured full DHT peer.")
    elif not status.get("relay_enabled"):
        errors.append("Bootstrap relay service is not enabled.")
    if status.get("force_reachability") != "public":
        errors.append("Bootstrap does not report forced public reachability.")
    if not status.get("peer_id"):
        errors.append("Bootstrap peer ID is missing.")
    if not status.get("visible_maddrs"):
        errors.append("Bootstrap visible multiaddresses are missing.")

    comparisons = {
        "peer_id": "peer ID",
        "hivemind_version": "Hivemind version",
        "deployment_commit": "deployment commit",
        "identity_path": "identity path",
        "port": "listening port",
        "failure_domain": "failure domain",
    }
    for field, label in comparisons.items():
        expected_value = expected.get(field)
        if expected_value is not None and status.get(field) != expected_value:
            errors.append(
                f"Unexpected {label}: expected {expected_value!r}, got {status.get(field)!r}."
            )

    expected_maddr = expected.get("public_maddr")
    if expected_maddr and expected_maddr not in status.get("visible_maddrs", []):
        errors.append(f"Expected public multiaddress is not visible: {expected_maddr}.")

    expected_python_prefix = expected.get("python_prefix")
    if expected_python_prefix and not str(status.get("python_version", "")).startswith(
        expected_python_prefix
    ):
        errors.append(
            "Unexpected Python version: "
            f"expected prefix {expected_python_prefix!r}, got {status.get('python_version')!r}."
        )

    before = expected.get("before_restart")
    if before:
        if before.get("peer_id") != status.get("peer_id"):
            errors.append("Peer ID changed across the service restart.")
        if before.get("identity_path") != status.get("identity_path"):
            errors.append("Identity path changed across the service restart.")
        if before.get("pid") == status.get("pid"):
            errors.append("Service PID did not change across the requested restart test.")

    return errors


def build_validation_report(
    status: dict[str, Any],
    errors: list[str],
    *,
    restart_requested: bool,
    identity_hash_preserved: bool,
    relay_flags_observed: bool,
    role: str | None = None,
) -> dict[str, Any]:
    status_valid = not errors
    restart_passed = (
        restart_requested
        and status_valid
        and identity_hash_preserved
        and relay_flags_observed
    )
    return {
        "schema_version": 2 if role else 1,
        "kind": "infrastructure_service_validation" if role else "vps_bootstrap_validation",
        "role": role,
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "ok": (
            status_valid
            and relay_flags_observed
            and (not restart_requested or restart_passed)
        ),
        "checks": {
            "runtime_status_valid": status_valid,
            "relay_flags_observed": relay_flags_observed,
            "effective_flags_observed": relay_flags_observed,
        },
        "restart": {
            "requested": restart_requested,
            "passed": restart_passed,
            "identity_hash_preserved": identity_hash_preserved,
        },
        "errors": errors,
        "status": status,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", required=True)
    parser.add_argument("--expected-role", choices=("dht", "relay", "combined"))
    parser.add_argument("--expected-peer-id")
    parser.add_argument("--expected-hivemind-version", default="1.1.12")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-identity-path")
    parser.add_argument("--expected-public-maddr")
    parser.add_argument("--expected-port", type=int)
    parser.add_argument("--expected-failure-domain")
    parser.add_argument("--python-prefix", default="3.12")
    parser.add_argument("--before-restart-status")
    parser.add_argument("--identity-hash-preserved", action="store_true")
    parser.add_argument("--relay-flags-observed", action="store_true")
    parser.add_argument("--effective-flags-observed", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    status = json.loads(Path(args.status).read_text(encoding="utf-8"))
    expected: dict[str, Any] = {
        "peer_id": args.expected_peer_id,
        "hivemind_version": args.expected_hivemind_version,
        "deployment_commit": args.expected_commit,
        "identity_path": args.expected_identity_path,
        "public_maddr": args.expected_public_maddr,
        "port": args.expected_port,
        "python_prefix": args.python_prefix,
        "role": args.expected_role,
        "failure_domain": args.expected_failure_domain,
    }
    if args.before_restart_status:
        expected["before_restart"] = json.loads(
            Path(args.before_restart_status).read_text(encoding="utf-8")
        )

    errors = validate_status(status, expected)
    result = build_validation_report(
        status,
        errors,
        restart_requested=bool(args.before_restart_status),
        identity_hash_preserved=args.identity_hash_preserved,
        relay_flags_observed=(
            args.relay_flags_observed or args.effective_flags_observed
        ),
        role=args.expected_role,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
