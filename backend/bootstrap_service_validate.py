"""Validate non-secret runtime evidence from the managed VPS relay service."""

import argparse
import json
from pathlib import Path
from typing import Any


def validate_status(status: dict[str, Any], expected: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if status.get("schema_version") != 1:
        errors.append("Unsupported or missing bootstrap status schema.")
    if not status.get("relay_enabled"):
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", required=True)
    parser.add_argument("--expected-peer-id")
    parser.add_argument("--expected-hivemind-version", default="1.1.12")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-identity-path")
    parser.add_argument("--expected-public-maddr")
    parser.add_argument("--expected-port", type=int)
    parser.add_argument("--python-prefix", default="3.12")
    parser.add_argument("--before-restart-status")
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
    }
    if args.before_restart_status:
        expected["before_restart"] = json.loads(
            Path(args.before_restart_status).read_text(encoding="utf-8")
        )

    errors = validate_status(status, expected)
    result = {
        "ok": not errors,
        "errors": errors,
        "status": status,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
