"""Pure participant-side infrastructure configuration diagnostics."""

from __future__ import annotations

from typing import Sequence


def _ordered_unique(values: Sequence[str]) -> tuple[list[str], list[str]]:
    seen: set[str] = set()
    unique: list[str] = []
    duplicates: list[str] = []
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        if value in seen:
            duplicates.append(value)
            continue
        seen.add(value)
        unique.append(value)
    return unique, duplicates


def summarize_infrastructure(
    initial_peers: Sequence[str],
    trusted_relays: Sequence[str],
    *,
    network_state: str,
) -> dict:
    """Report ordered configuration separately from observed DHT readiness."""
    dht_peers, duplicate_dht = _ordered_unique(initial_peers)
    relays, duplicate_relays = _ordered_unique(trusted_relays)
    dht_state = "redundant_configured" if len(dht_peers) >= 2 else (
        "single_failure_domain" if dht_peers else "unconfigured"
    )
    relay_state = "redundant_configured" if len(relays) >= 2 else (
        "single_failure_domain" if relays else "unconfigured"
    )
    configuration_state = (
        "redundant_configured"
        if dht_state == relay_state == "redundant_configured"
        else "single_failure_domain"
        if dht_peers and relays
        else "unconfigured"
    )
    if network_state == "ready" and configuration_state == "redundant_configured":
        runtime_state = "ready"
    elif network_state in {"ready", "syncing", "degraded"}:
        runtime_state = "degraded"
    else:
        runtime_state = "disconnected"
    return {
        "schema_version": 1,
        "runtime_state": runtime_state,
        "configuration_state": configuration_state,
        "dht": {
            "state": dht_state,
            "configured_count": len(dht_peers),
            "ordered_peers": dht_peers,
            "duplicate_count": len(duplicate_dht),
        },
        "relay": {
            "state": relay_state,
            "configured_count": len(relays),
            "ordered_relays": relays,
            "duplicate_count": len(duplicate_relays),
        },
        "observability": (
            "Configuration count does not prove independent hosts or live failover; "
            "use the architecture failure matrix for that acceptance."
        ),
    }
