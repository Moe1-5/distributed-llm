"""Validate the Sprint 32 redundant-infrastructure and failure matrix."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from acceptance_evidence import EvidenceError, load_documents, write_private_json

SCHEMA_VERSION = 1
REDUNDANT_ROLES = {"dht": 2, "relay": 2}
SINGLETON_ROLES = {"coordinator": 1, "settlement": 1}
REQUIRED_SCENARIOS = {
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
}
REQUIRED_TOPOLOGIES = {
    "direct",
    "relay",
    "complementary_split",
    "redundant",
    "session",
    "package",
    "incentives_off",
    "shadow_accounting",
}
REQUIRED_PROTOCOLS = {
    "hivemind_version": "1.1.12",
    "infrastructure_protocol_version": 1,
    "expert_uid_protocol": "peer-addressed-v1",
    "placement_protocol_version": 1,
    "session_protocol_version": 1,
    "receipt_protocol_version": 1,
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _valid_hash(value: Any, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None


def _validate_pass_map(
    raw: Any,
    *,
    required: set[str],
    label: str,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    values = _mapping(raw)
    summary: dict[str, Any] = {}
    for name in sorted(required):
        item = _mapping(values.get(name))
        ok = item.get("ok") is True
        evidence_sha256 = item.get("evidence_sha256")
        if not ok:
            errors.append(f"{label} {name} did not pass")
        if not _valid_hash(evidence_sha256, 64):
            errors.append(f"{label} {name} has no valid evidence SHA-256")
        summary[name] = {
            "ok": ok,
            "evidence_sha256": evidence_sha256 if _valid_hash(evidence_sha256, 64) else None,
        }
    return errors, summary


def validate_architecture_matrix(
    document: Mapping[str, Any],
    *,
    expected_source_commit: str | None = None,
    expected_hivemind_version: str = "1.1.12",
) -> dict[str, Any]:
    errors: list[str] = []
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append("Architecture matrix has an unsupported schema version")
    if document.get("kind") != "distributed_architecture_failure_matrix":
        errors.append("Architecture matrix has an unsupported kind")

    source_commit = str(document.get("participant_source_commit", "")).strip()
    if not _valid_hash(source_commit, 40):
        errors.append("Architecture matrix has no valid participant source commit")
    elif expected_source_commit and source_commit != expected_source_commit:
        errors.append("Architecture matrix participant commit does not match the Windows artifact")

    protocols = _mapping(document.get("protocols"))
    expected_protocols = {
        **REQUIRED_PROTOCOLS,
        "hivemind_version": expected_hivemind_version,
    }
    for field, expected in expected_protocols.items():
        if protocols.get(field) != expected:
            errors.append(
                f"Architecture protocol {field} is {protocols.get(field)!r}; expected {expected!r}"
            )

    raw_components = document.get("components")
    components = (
        [dict(item) for item in raw_components if isinstance(item, Mapping)]
        if isinstance(raw_components, list)
        else []
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    instance_ids: set[str] = set()
    for index, component in enumerate(components, start=1):
        role = str(component.get("role", "")).strip()
        grouped.setdefault(role, []).append(component)
        instance_id = str(component.get("instance_id", "")).strip()
        if not instance_id or instance_id in instance_ids:
            errors.append(f"Architecture component {index} has a missing or duplicate instance ID")
        instance_ids.add(instance_id)
        if not str(component.get("failure_domain", "")).strip():
            errors.append(f"Architecture component {index} has no failure domain")
        deployment_commit = component.get("deployment_commit")
        if not _valid_hash(deployment_commit, 40):
            errors.append(f"Architecture component {index} has no valid deployment commit")
        elif _valid_hash(source_commit, 40) and deployment_commit != source_commit:
            errors.append(
                f"Architecture component {index} deployment commit does not match "
                "the reviewed participant source commit"
            )
        if component.get("service_protocol_version") != 1:
            errors.append(f"Architecture component {index} has an unsupported service protocol")
        if role in REDUNDANT_ROLES and not str(component.get("peer_id", "")).strip():
            errors.append(f"Architecture {role} component {index} has no peer ID")

    for role, count in {**REDUNDANT_ROLES, **SINGLETON_ROLES}.items():
        role_components = grouped.get(role, [])
        if len(role_components) < count:
            errors.append(f"Architecture matrix requires at least {count} {role} component(s)")
        if role in REDUNDANT_ROLES:
            domains = {str(item.get("failure_domain", "")).strip() for item in role_components}
            peers = {str(item.get("peer_id", "")).strip() for item in role_components}
            if len(domains - {""}) < count:
                errors.append(f"Architecture {role} components do not span independent failure domains")
            if len(peers - {""}) < count:
                errors.append(f"Architecture {role} components do not use distinct peer identities")

    scenario_errors, scenarios = _validate_pass_map(
        document.get("scenarios"), required=REQUIRED_SCENARIOS, label="Failure scenario"
    )
    topology_errors, topologies = _validate_pass_map(
        document.get("topologies"), required=REQUIRED_TOPOLOGIES, label="Topology"
    )
    errors.extend(scenario_errors)
    errors.extend(topology_errors)

    complementary = _mapping(_mapping(document.get("topologies")).get("complementary_split"))
    redundant = _mapping(_mapping(document.get("topologies")).get("redundant"))
    if complementary.get("complete_alternate") is not False:
        errors.append("Complementary split must be recorded separately as non-redundant")
    if redundant.get("complete_alternate") is not True:
        errors.append("Redundant topology has no complete alternate route")
    try:
        redundant_provider_count = int(redundant.get("provider_count", 0))
    except (TypeError, ValueError):
        redundant_provider_count = 0
    if redundant_provider_count < 3:
        errors.append("Redundant topology requires at least three providers")

    phases = _mapping(document.get("rollout_phases"))
    if phases.get("incentives_off_passed") is not True:
        errors.append("Incentives-off acceptance did not pass")
    if phases.get("shadow_passed") is not True:
        errors.append("Shadow accounting acceptance did not pass")
    if phases.get("shadow_started_after_off") is not True:
        errors.append("Shadow acceptance was not ordered after incentives-off acceptance")

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "validated_distributed_architecture_failure_matrix",
        "ok": not errors,
        "participant_source_commit": source_commit or None,
        "protocols": dict(protocols),
        "components": components,
        "scenarios": scenarios,
        "topologies": topologies,
        "rollout_phases": dict(phases),
        "errors": errors,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", type=Path, required=True)
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--expected-hivemind-version", default="1.1.12")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        (document,) = load_documents([args.matrix])
        report = validate_architecture_matrix(
            document,
            expected_source_commit=args.expected_source_commit,
            expected_hivemind_version=args.expected_hivemind_version,
        )
        write_private_json(args.output, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1
    except EvidenceError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
