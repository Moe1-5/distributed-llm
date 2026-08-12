"""Pure coverage analysis and layer-serving recommendations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


Span = tuple[int, int]


def valid_spans(nodes: Iterable[dict], total_layers: int) -> list[Span]:
    """Return unique, valid half-open layer spans in deterministic order."""
    spans: set[Span] = set()
    for node in nodes:
        try:
            start = int(node["layer_start"])
            end = int(node["layer_end"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= start < end <= total_layers:
            spans.add((start, end))
    return sorted(spans)


def select_route_spans(nodes: Iterable[dict], total_layers: int) -> list[Span]:
    """Select a complete adjacent route, preferring fewer hops then stable spans."""
    if total_layers <= 0:
        return []

    spans = valid_spans(nodes, total_layers)
    best_from: dict[int, tuple[Span, ...]] = {total_layers: ()}
    for start in range(total_layers - 1, -1, -1):
        candidates = [
            ((span,) + best_from[span[1]])
            for span in spans
            if span[0] == start and span[1] in best_from
        ]
        if candidates:
            best_from[start] = min(candidates, key=lambda route: (len(route), route))

    if 0 not in best_from:
        raise ValueError(
            f"No contiguous route from layer 0 to {total_layers}; "
            f"reachable endpoints: {reachable_endpoints(spans, total_layers)}"
        )
    return list(best_from[0])


def reachable_endpoints(nodes_or_spans: Iterable[dict | Span], total_layers: int) -> list[int]:
    """Return endpoints reachable from layer zero through exactly adjacent spans."""
    raw = list(nodes_or_spans)
    if raw and isinstance(raw[0], tuple):
        spans = sorted(set(raw))  # type: ignore[arg-type]
    else:
        spans = valid_spans(raw, total_layers)  # type: ignore[arg-type]

    reachable = {0}
    changed = True
    while changed:
        changed = False
        for start, end in spans:
            if start in reachable and end not in reachable:
                reachable.add(end)
                changed = True
    return sorted(reachable)


def provider_counts(nodes: Iterable[dict], total_layers: int) -> list[int]:
    node_list = list(nodes)
    counts = [0] * total_layers
    for start, end in valid_spans(node_list, total_layers):
        replicas = sum(
            1
            for node in node_list
            if _node_span(node) == (start, end)
        )
        for layer in range(start, end):
            counts[layer] += replicas
    return counts


def coverage_ranges(nodes: Iterable[dict], total_layers: int) -> list[dict]:
    """Compress per-layer provider counts into UI-friendly ranges."""
    counts = provider_counts(list(nodes), total_layers)
    if not counts:
        return []

    ranges: list[dict] = []
    start = 0
    for index in range(1, len(counts) + 1):
        if index == len(counts) or counts[index] != counts[start]:
            count = counts[start]
            ranges.append(
                {
                    "layer_start": start,
                    "layer_end": index,
                    "provider_count": count,
                    "status": "missing" if count == 0 else "covered" if count == 1 else "redundant",
                }
            )
            start = index
    return ranges


def missing_ranges(nodes: Iterable[dict], total_layers: int) -> list[dict]:
    return [
        {"layer_start": item["layer_start"], "layer_end": item["layer_end"]}
        for item in coverage_ranges(nodes, total_layers)
        if item["provider_count"] == 0
    ]


def coverage_revision(model_name: str, nodes: Iterable[dict], total_layers: int) -> str:
    snapshot = []
    for node in nodes:
        span = _node_span(node)
        if span is None or not (0 <= span[0] < span[1] <= total_layers):
            continue
        snapshot.append(
            {
                "peer_id": str(node.get("peer_id", "")),
                "rpc_uid": str(node.get("rpc_uid", "")),
                "layer_start": span[0],
                "layer_end": span[1],
            }
        )
    payload = json.dumps(
        {"model_name": model_name, "total_layers": total_layers, "nodes": sorted(snapshot, key=_snapshot_key)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_serving_plan(
    *,
    model_name: str,
    nodes: Iterable[dict],
    total_layers: int,
    layer_count: int,
) -> dict:
    """Recommend the best contiguous range for the requested contribution size."""
    if not 1 <= layer_count <= total_layers:
        raise ValueError(f"layer_count must be between 1 and {total_layers}")

    current_nodes = list(nodes)
    current_counts = provider_counts(current_nodes, total_layers)
    current_route = _try_route(current_nodes, total_layers)
    reachable = set(reachable_endpoints(current_nodes, total_layers))

    ranked: list[tuple[tuple, int, int, list[Span]]] = []
    for start in range(0, total_layers - layer_count + 1):
        end = start + layer_count
        candidate = {"layer_start": start, "layer_end": end}
        projected_route = _try_route([*current_nodes, candidate], total_layers)
        newly_covered = sum(1 for count in current_counts[start:end] if count == 0)
        frontier_extension = end if not current_route and start in reachable else 0
        provider_load = sum(current_counts[start:end])
        score = (
            bool(projected_route),
            newly_covered,
            frontier_extension,
            -provider_load,
            -start,
        )
        ranked.append((score, start, end, projected_route))

    _, start, end, projected_route = max(ranked, key=lambda item: item[0])
    newly_covered = sum(1 for count in current_counts[start:end] if count == 0)
    adds = "missing_coverage" if newly_covered else "redundancy"
    if projected_route and not current_route:
        reason = f"Fills layers {start}-{end}; model becomes runnable."
    elif newly_covered:
        reason = f"Adds {newly_covered} missing layer(s) across {start}-{end}."
    else:
        reason = f"Layers {start}-{end} are already covered; this adds redundancy."

    selected_spans = set(current_route)
    selected_nodes, standby_nodes = _partition_nodes(current_nodes, selected_spans)
    projected_counts = list(current_counts)
    for layer in range(start, end):
        projected_counts[layer] += 1

    return {
        "model_name": model_name,
        "total_layers": total_layers,
        "requested_layer_count": layer_count,
        "coverage_revision": coverage_revision(model_name, current_nodes, total_layers),
        "coverage_ranges": coverage_ranges(current_nodes, total_layers),
        "missing_ranges": missing_ranges(current_nodes, total_layers),
        "recommended_range": {
            "layer_start": start,
            "layer_end": end,
            "adds": adds,
            "newly_covered_layers": newly_covered,
            "completes_route": bool(projected_route) and not bool(current_route),
            "reason": reason,
        },
        "runnable_before": bool(current_route),
        "runnable_after": bool(projected_route),
        "selected_route": _format_spans(current_route),
        "projected_route": _format_spans(projected_route),
        "standby_ranges": standby_nodes,
        "projected_coverage_ranges": _ranges_from_counts(projected_counts),
        "selected_nodes": selected_nodes,
    }


def _try_route(nodes: Iterable[dict], total_layers: int) -> list[Span]:
    try:
        return select_route_spans(nodes, total_layers)
    except ValueError:
        return []


def _node_span(node: dict) -> Span | None:
    try:
        return int(node["layer_start"]), int(node["layer_end"])
    except (KeyError, TypeError, ValueError):
        return None


def _snapshot_key(item: dict) -> tuple:
    return item["layer_start"], item["layer_end"], item["peer_id"], item["rpc_uid"]


def _format_spans(spans: Iterable[Span]) -> list[dict]:
    return [{"layer_start": start, "layer_end": end} for start, end in spans]


def _partition_nodes(nodes: list[dict], selected_spans: set[Span]) -> tuple[list[dict], list[dict]]:
    selected: list[dict] = []
    standby: list[dict] = []
    used_spans: set[Span] = set()
    for node in sorted(nodes, key=lambda item: (_node_span(item) or (-1, -1), str(item.get("peer_id", "")))):
        span = _node_span(node)
        summary = {
            "peer_id": str(node.get("peer_id", "")),
            "layer_start": span[0] if span else None,
            "layer_end": span[1] if span else None,
        }
        if span in selected_spans and span not in used_spans:
            selected.append(summary)
            used_spans.add(span)
        else:
            standby.append(summary)
    return selected, standby


def _ranges_from_counts(counts: list[int]) -> list[dict]:
    if not counts:
        return []
    ranges: list[dict] = []
    start = 0
    for index in range(1, len(counts) + 1):
        if index == len(counts) or counts[index] != counts[start]:
            count = counts[start]
            ranges.append(
                {
                    "layer_start": start,
                    "layer_end": index,
                    "provider_count": count,
                    "status": "missing" if count == 0 else "covered" if count == 1 else "redundant",
                }
            )
            start = index
    return ranges
