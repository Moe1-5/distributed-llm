"""Pure coverage analysis shared by routing and serving recommendations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, MutableMapping
from typing import Any

Span = tuple[int, int]
ProviderIdentity = tuple[str, str, int, int]


def _node_key(node: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(node.get("peer_id", "")),
        str(node.get("rpc_uid", "")),
        str(node.get("node_id", "")),
    )


def provider_identity(node: Mapping[str, Any]) -> ProviderIdentity:
    return (
        str(node.get("peer_id", "")),
        str(node.get("rpc_uid", "")),
        int(node.get("layer_start", 0)),
        int(node.get("layer_end", 0)),
    )


def valid_nodes(nodes: list[dict], total_layers: int) -> list[dict]:
    result: list[dict] = []
    for node in nodes:
        try:
            start = int(node["layer_start"])
            end = int(node["layer_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= start < end <= total_layers:
            result.append({**node, "layer_start": start, "layer_end": end})
    return result


def group_nodes_by_span(nodes: list[dict], total_layers: int) -> dict[Span, list[dict]]:
    groups: dict[Span, list[dict]] = {}
    for node in valid_nodes(nodes, total_layers):
        span = (node["layer_start"], node["layer_end"])
        groups.setdefault(span, []).append(node)
    return {
        span: sorted(replicas, key=_node_key)
        for span, replicas in sorted(groups.items())
    }


def find_route_spans(nodes: list[dict], total_layers: int) -> list[Span] | None:
    """Find the fewest-hop path of exactly adjacent advertised ranges."""
    if total_layers == 0:
        return []

    groups = group_nodes_by_span(nodes, total_layers)
    edges: dict[int, list[Span]] = {}
    for span in groups:
        edges.setdefault(span[0], []).append(span)

    best: dict[int, list[Span]] = {0: []}
    for start in range(total_layers):
        prefix = best.get(start)
        if prefix is None:
            continue
        for span in sorted(edges.get(start, [])):
            candidate = [*prefix, span]
            current = best.get(span[1])
            if current is None or (len(candidate), tuple(candidate)) < (
                len(current),
                tuple(current),
            ):
                best[span[1]] = candidate
    return best.get(total_layers)


def select_route(
    nodes: list[dict],
    total_layers: int,
    replica_cursors: MutableMapping[Span, int] | None = None,
    *,
    advance_replicas: bool = False,
) -> tuple[list[dict], list[dict]]:
    groups = group_nodes_by_span(nodes, total_layers)
    spans = find_route_spans(nodes, total_layers)
    if spans is None:
        return [], [node for replicas in groups.values() for node in replicas]

    selected: list[dict] = []
    selected_keys: set[tuple[Span, tuple[str, str, str]]] = set()
    for span in spans:
        replicas = groups[span]
        cursor = 0
        if replica_cursors is not None:
            cursor = replica_cursors.get(span, 0) % len(replicas)
        chosen = replicas[cursor]
        selected.append(chosen)
        selected_keys.add((span, _node_key(chosen)))
        if replica_cursors is not None and advance_replicas:
            replica_cursors[span] = (cursor + 1) % len(replicas)

    standby = [
        node
        for span, replicas in groups.items()
        for node in replicas
        if (span, _node_key(node)) not in selected_keys
    ]
    return selected, standby


def _health_by_provider(health_snapshot: Mapping[str, Any] | None) -> dict[ProviderIdentity, dict]:
    if health_snapshot is None:
        return {}
    providers = health_snapshot.get("providers", [])
    if not isinstance(providers, list):
        return {}
    return {
        provider_identity(provider): provider
        for provider in providers
        if isinstance(provider, Mapping)
    }


def _transport_rank(node: Mapping[str, Any], health: Mapping[str, Any]) -> int:
    verified = health.get("transport_verified", node.get("transport_verified")) is True
    mode = str(node.get("connection_mode", "checking"))
    if verified and mode == "direct":
        return 0
    if verified and mode == "relay":
        return 1
    return 2


def _route_summary(route: list[dict], health_by_provider: Mapping[ProviderIdentity, dict]) -> dict:
    provider_health: list[dict] = []
    for node in route:
        health = health_by_provider.get(provider_identity(node), {})
        provider_health.append(
            {
                "state": str(health.get("state", "checking")),
                "latency_ms": health.get("latency_ms"),
                "transport_rank": _transport_rank(node, health),
            }
        )
    degraded_count = sum(item["state"] == "degraded" for item in provider_health)
    relay_hops = sum(item["transport_rank"] == 1 for item in provider_health)
    unknown_hops = sum(item["transport_rank"] == 2 for item in provider_health)
    latency_ms = sum(
        float(item["latency_ms"])
        for item in provider_health
        if isinstance(item["latency_ms"], (int, float))
    )
    measured_hops = sum(
        isinstance(item["latency_ms"], (int, float)) for item in provider_health
    )
    transport = (
        "direct"
        if route and not relay_hops and not unknown_hops
        else "relay"
        if route and not unknown_hops
        else "mixed_or_unverified"
    )
    return {
        "route": [dict(node) for node in route],
        "degraded": degraded_count > 0,
        "degraded_hops": degraded_count,
        "transport": transport,
        "relay_hops": relay_hops,
        "unverified_hops": unknown_hops,
        "latency_ms": latency_ms if measured_hops == len(route) else None,
    }


def plan_health_aware_routes(
    nodes: list[dict],
    total_layers: int,
    health_snapshot: Mapping[str, Any] | None = None,
    *,
    max_alternates: int = 3,
    allow_degraded: bool = True,
) -> dict:
    """Return one stable complete route and deterministic bounded alternates."""
    if max_alternates < 0:
        raise ValueError("max_alternates must be non-negative")
    normalized = valid_nodes(nodes, total_layers)
    groups = group_nodes_by_span(normalized, total_layers)
    health_by_provider = _health_by_provider(health_snapshot)
    default_state = "healthy" if health_snapshot is None else "checking"
    limit = max_alternates + 1
    eligible_groups: dict[Span, list[dict]] = {}
    for span, replicas in groups.items():
        eligible_groups[span] = []
        for node in replicas:
            state = str(
                health_by_provider.get(provider_identity(node), {}).get(
                    "state", default_state
                )
            )
            if state == "healthy" or (allow_degraded and state == "degraded"):
                eligible_groups[span].append(node)

    def ranked(route: list[dict]) -> dict:
        summary = _route_summary(route, health_by_provider)
        summary["rank"] = (
            summary["degraded_hops"],
            summary["unverified_hops"],
            summary["relay_hops"],
            len(route),
            summary["latency_ms"] if summary["latency_ms"] is not None else float("inf"),
            tuple(provider_identity(node) for node in route),
        )
        return summary

    edges: dict[int, list[Span]] = {}
    for span, replicas in eligible_groups.items():
        if replicas:
            edges.setdefault(span[0], []).append(span)
    best: dict[int, list[dict]] = {0: [ranked([])]}
    evaluated_candidates = 0
    for start in range(total_layers):
        prefixes = best.get(start, [])
        if not prefixes:
            continue
        for span in sorted(edges.get(start, [])):
            additions = [
                ranked([*prefix["route"], node])
                for prefix in prefixes
                for node in eligible_groups[span]
            ]
            evaluated_candidates += len(additions)
            merged = [*best.get(span[1], []), *additions]
            merged.sort(key=lambda item: item["rank"])
            deduplicated: list[dict] = []
            seen: set[tuple[ProviderIdentity, ...]] = set()
            for candidate in merged:
                identity = tuple(provider_identity(node) for node in candidate["route"])
                if identity in seen:
                    continue
                seen.add(identity)
                deduplicated.append(candidate)
                if len(deduplicated) >= limit:
                    break
            best[span[1]] = deduplicated

    candidates = best.get(total_layers, []) if total_layers else best[0]
    public_candidates = [
        {key: value for key, value in item.items() if key != "rank"}
        for item in candidates[: max_alternates + 1]
    ]
    active = public_candidates[0] if public_candidates else None
    alternates = public_candidates[1:]
    eligible_nodes = [
        node
        for node in normalized
        if str(
            health_by_provider.get(provider_identity(node), {}).get("state", default_state)
        ) in ({"healthy", "degraded"} if allow_degraded else {"healthy"})
    ]
    unavailable_ranges = (
        [] if active is not None else route_requirement_ranges(eligible_nodes, total_layers)
    )
    health_revision = str((health_snapshot or {}).get("health_revision", "unmonitored"))
    revision_document = {
        "coverage_revision": coverage_revision(normalized, total_layers),
        "health_revision": health_revision,
        "active": [provider_identity(node) for node in active["route"]] if active else [],
        "alternates": [
            [provider_identity(node) for node in alternate["route"]]
            for alternate in alternates
        ],
    }
    route_revision = hashlib.sha256(
        json.dumps(revision_document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:20]
    return {
        "route_revision": route_revision,
        "coverage_revision": revision_document["coverage_revision"],
        "health_revision": health_revision,
        "active": active,
        "alternates": alternates,
        "unavailable_ranges": unavailable_ranges,
        "candidate_count": len(candidates),
        "evaluated_candidates": evaluated_candidates,
    }


def reachable_prefix(nodes: list[dict], total_layers: int) -> int:
    groups = group_nodes_by_span(nodes, total_layers)
    reachable = {0}
    for start in range(total_layers):
        if start not in reachable:
            continue
        reachable.update(span[1] for span in groups if span[0] == start)
    return max(reachable)


def coverage_segments(nodes: list[dict], total_layers: int) -> list[dict]:
    normalized = valid_nodes(nodes, total_layers)
    boundaries = {0, total_layers}
    for node in normalized:
        boundaries.add(node["layer_start"])
        boundaries.add(node["layer_end"])

    ordered = sorted(boundaries)
    segments: list[dict] = []
    for start, end in zip(ordered, ordered[1:]):
        provider_count = sum(
            node["layer_start"] <= start and node["layer_end"] >= end
            for node in normalized
        )
        segments.append(
            {
                "start": start,
                "end": end,
                "provider_count": provider_count,
                "status": (
                    "missing"
                    if provider_count == 0
                    else "covered"
                    if provider_count == 1
                    else "redundant"
                ),
            }
        )
    return segments


def _merge_ranges(ranges: list[Span]) -> list[dict]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [{"start": start, "end": end} for start, end in merged]


def uncovered_ranges(nodes: list[dict], total_layers: int) -> list[dict]:
    return [
        {"start": segment["start"], "end": segment["end"]}
        for segment in coverage_segments(nodes, total_layers)
        if segment["provider_count"] == 0
    ]


def route_requirement_ranges(nodes: list[dict], total_layers: int) -> list[dict]:
    if find_route_spans(nodes, total_layers) is not None:
        return []
    uncovered = uncovered_ranges(nodes, total_layers)
    if uncovered:
        return _merge_ranges([(item["start"], item["end"]) for item in uncovered])
    prefix = reachable_prefix(nodes, total_layers)
    return [{"start": prefix, "end": total_layers}] if prefix < total_layers else []


def coverage_revision(nodes: list[dict], total_layers: int) -> str:
    snapshot = [
        {
            "peer_id": str(node.get("peer_id", "")),
            "rpc_uid": str(node.get("rpc_uid", "")),
            "node_id": str(node.get("node_id", "")),
            "layer_start": node["layer_start"],
            "layer_end": node["layer_end"],
        }
        for node in sorted(
            valid_nodes(nodes, total_layers),
            key=lambda item: (
                int(item["layer_start"]),
                int(item["layer_end"]),
                *_node_key(item),
            ),
        )
    ]
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:20]


def _provider_counts(nodes: list[dict], total_layers: int) -> list[int]:
    counts = [0] * total_layers
    for node in valid_nodes(nodes, total_layers):
        for layer in range(node["layer_start"], node["layer_end"]):
            counts[layer] += 1
    return counts


def evaluate_candidate(
    nodes: list[dict],
    total_layers: int,
    start: int,
    end: int,
) -> dict:
    if not 0 <= start < end <= total_layers:
        raise ValueError(f"Invalid candidate range {start}-{end} for {total_layers} layers")
    counts = _provider_counts(nodes, total_layers)
    candidate = {
        "peer_id": "recommended-provider",
        "rpc_uid": "recommended-provider",
        "node_id": "recommended-provider",
        "layer_start": start,
        "layer_end": end,
        "recommended": True,
    }
    projected_nodes = [*nodes, candidate]
    return {
        "layer_start": start,
        "layer_end": end,
        "newly_covered_layers": sum(counts[layer] == 0 for layer in range(start, end)),
        "adds_missing_coverage": any(counts[layer] == 0 for layer in range(start, end)),
        "adds_redundancy": any(counts[layer] > 0 for layer in range(start, end)),
        "completes_route": find_route_spans(projected_nodes, total_layers) is not None,
        "reachable_prefix": reachable_prefix(projected_nodes, total_layers),
        "provider_count_sum": sum(counts[start:end]),
        "provider_count_max": max(counts[start:end], default=0),
        "projected_nodes": projected_nodes,
    }


def recommend_range(nodes: list[dict], total_layers: int, layer_count: int) -> dict:
    if not 1 <= layer_count <= total_layers:
        raise ValueError("layer_count must be between 1 and the model layer count")

    candidates = [
        evaluate_candidate(nodes, total_layers, start, start + layer_count)
        for start in range(total_layers - layer_count + 1)
    ]
    return min(
        candidates,
        key=lambda item: (
            not item["completes_route"],
            -item["newly_covered_layers"],
            -item["reachable_prefix"],
            item["provider_count_sum"],
            item["provider_count_max"],
            item["layer_start"],
        ),
    )


def _route_node(node: Mapping[str, Any]) -> dict:
    return {
        "peer_id": str(node.get("peer_id", "")),
        "node_id": node.get("node_id"),
        "rpc_uid": str(node.get("rpc_uid", "")),
        "layer_start": int(node["layer_start"]),
        "layer_end": int(node["layer_end"]),
        "recommended": bool(node.get("recommended", False)),
    }


def _route_kind(route: list[dict]) -> str:
    if not route:
        return "unavailable"
    return "single_provider" if len(route) == 1 else "multiple_providers"


def build_serving_plan(nodes: list[dict], total_layers: int, layer_count: int) -> dict:
    normalized = valid_nodes(nodes, total_layers)
    current_route, standby = select_route(normalized, total_layers)
    recommendation = recommend_range(normalized, total_layers, layer_count)
    projected_route, _ = select_route(recommendation["projected_nodes"], total_layers)
    segments: list[dict] = []
    for segment in coverage_segments(normalized, total_layers):
        boundaries = {segment["start"], segment["end"]}
        for boundary in (
            recommendation["layer_start"],
            recommendation["layer_end"],
        ):
            if segment["start"] < boundary < segment["end"]:
                boundaries.add(boundary)
        ordered = sorted(boundaries)
        for start, end in zip(ordered, ordered[1:]):
            segments.append(
                {
                    **segment,
                    "start": start,
                    "end": end,
                    "recommended": (
                        recommendation["layer_start"] <= start
                        and recommendation["layer_end"] >= end
                    ),
                }
            )

    current_prefix = reachable_prefix(normalized, total_layers)
    public_recommendation = {
        key: value
        for key, value in recommendation.items()
        if key not in {"projected_nodes", "provider_count_sum", "provider_count_max"}
    }
    public_recommendation["extends_reachable_prefix"] = (
        recommendation["reachable_prefix"] > current_prefix
    )

    return {
        "coverage_revision": coverage_revision(normalized, total_layers),
        "total_layers": total_layers,
        "requested_layer_count": layer_count,
        "segments": segments,
        "missing_ranges": route_requirement_ranges(normalized, total_layers),
        "uncovered_ranges": uncovered_ranges(normalized, total_layers),
        "projected_missing_ranges": route_requirement_ranges(
            recommendation["projected_nodes"],
            total_layers,
        ),
        "recommendation": public_recommendation,
        "current_runnable": bool(current_route) or total_layers == 0,
        "projected_runnable": bool(projected_route) or total_layers == 0,
        "reachable_prefix": current_prefix,
        "selected_route": [_route_node(node) for node in current_route],
        "projected_route": [_route_node(node) for node in projected_route],
        "route_kind": _route_kind(current_route),
        "projected_route_kind": _route_kind(projected_route),
        "standby_ranges": [_route_node(node) for node in standby],
    }
