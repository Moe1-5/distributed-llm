"""Validate coordinator or settlement health and independent restart evidence."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def validate_health(
    health: Mapping[str, Any],
    *,
    role: str,
    deployment_commit: str,
    failure_domain: str,
) -> list[str]:
    errors: list[str] = []
    if role not in {"coordinator", "settlement"}:
        raise ValueError(f"Unsupported control-service role: {role}")
    if health.get("status") != "ok":
        errors.append("Control service health status is not ok")
    if health.get("component_role") != role:
        errors.append("Control service role does not match the expected role")
    if health.get("service_protocol_version") != 1:
        errors.append("Control service protocol version is unsupported")
    if health.get("schema_version") != 1:
        errors.append("Control service schema version is unsupported")
    if health.get("deployment_commit") != deployment_commit:
        errors.append("Control service deployment commit does not match")
    if health.get("failure_domain") != failure_domain:
        errors.append("Control service failure domain does not match")
    if role == "coordinator":
        if not isinstance(health.get("topology_revision"), int):
            errors.append("Coordinator topology revision is missing")
    else:
        if health.get("receipt_protocol_version") != 1:
            errors.append("Settlement receipt protocol version is unsupported")
        if not isinstance(health.get("reward_version"), int):
            errors.append("Settlement reward version is missing")
        if health.get("mode") not in {"shadow", "credit"}:
            errors.append("Settlement mode is not shadow or credit")
    return errors


def build_report(
    health: Mapping[str, Any],
    errors: list[str],
    *,
    role: str,
    restart_requested: bool,
    restart_pid_changed: bool,
    journal_observed: bool,
) -> dict[str, Any]:
    restart_passed = restart_requested and restart_pid_changed and not errors
    return {
        "schema_version": 1,
        "kind": "control_service_validation",
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "ok": not errors and journal_observed and (
            not restart_requested or restart_passed
        ),
        "role": role,
        "checks": {
            "health_valid": not errors,
            "journal_observed": journal_observed,
        },
        "restart": {
            "requested": restart_requested,
            "passed": restart_passed,
            "pid_changed": restart_pid_changed,
        },
        "health": dict(health),
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health", type=Path, required=True)
    parser.add_argument("--role", choices=("coordinator", "settlement"), required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--expected-failure-domain", required=True)
    parser.add_argument("--restart-requested", action="store_true")
    parser.add_argument("--restart-pid-changed", action="store_true")
    parser.add_argument("--journal-observed", action="store_true")
    args = parser.parse_args()
    health = json.loads(args.health.read_text(encoding="utf-8"))
    errors = validate_health(
        health,
        role=args.role,
        deployment_commit=args.expected_commit,
        failure_domain=args.expected_failure_domain,
    )
    report = build_report(
        health,
        errors,
        role=args.role,
        restart_requested=args.restart_requested,
        restart_pid_changed=args.restart_pid_changed,
        journal_observed=args.journal_observed,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
