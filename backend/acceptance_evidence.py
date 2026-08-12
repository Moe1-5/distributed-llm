"""Capture and validate sanitized evidence for live two-device acceptance."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from client.coverage import route_requirement_ranges, select_route

SCHEMA_VERSION = 1
DEFAULT_PROMPT = "The capital of France is"
JsonOpener = Callable[..., Any]


class EvidenceError(RuntimeError):
    """Raised when evidence cannot be captured or does not meet acceptance."""


@dataclass(frozen=True)
class CaptureOptions:
    participant: str
    backend_url: str
    model_name: str
    output: Path
    run_inference: bool = False
    prompt: str = DEFAULT_PROMPT
    max_new_tokens: int = 8
    timeout: float = 30.0
    settlement_wait: float = 10.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


def _validated_backend_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise EvidenceError(
            "Backend URL must be an HTTP(S) origin without credentials, path, query, or fragment"
        )
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _request_json(
    backend_url: str,
    path: str,
    *,
    timeout: float,
    payload: Mapping[str, Any] | None = None,
    opener: JsonOpener = urllib.request.urlopen,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{backend_url}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    try:
        with opener(request, timeout=timeout) as response:
            document = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise EvidenceError(f"{path} returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise EvidenceError(f"{path} is unreachable: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"{path} returned invalid JSON") from exc
    if not isinstance(document, dict):
        raise EvidenceError(f"{path} returned a non-object JSON document")
    return document


def _pick(source: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    return {field: source.get(field) for field in fields}


def _node_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(
        source,
        (
            "peer_id",
            "node_id",
            "model_name",
            "layer_start",
            "layer_end",
            "device",
            "running",
            "layers_loaded",
            "rpc_running",
            "rpc_uid",
            "connection_mode",
            "direct_reachability",
            "transport_verified",
            "receipt_protocol_version",
            "receipt_rpc_uid",
            "application_public_key",
            "model_revision",
        ),
    )


def _model_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(
        source,
        (
            "id",
            "num_layers",
            "runnable",
            "route_ready",
            "route_reasons",
            "covered_layers",
            "missing_layers",
            "compatible_nodes",
            "route_trace",
        ),
    )


def _generator_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _pick(
        source,
        ("ready", "model_name", "route_ready", "reasons", "node_trace"),
    )
    performance = source.get("performance")
    if isinstance(performance, Mapping):
        result["performance"] = _pick(
            performance,
            ("startup_duration_ms", "load_duration_ms", "route_validation_ms"),
        )
        last_generation = performance.get("last_generation")
        if isinstance(last_generation, Mapping):
            result["performance"]["last_generation"] = _generation_performance(
                last_generation
            )
    return result


def _generation_performance(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _pick(
        source,
        (
            "time_to_first_token_ms",
            "total_duration_ms",
            "generated_tokens",
            "tokens_per_second",
            "route_validation_ms_total",
            "stopped",
        ),
    )
    hops = source.get("hop_metrics")
    result["hop_metrics"] = (
        [
            _pick(
                hop,
                (
                    "peer_id",
                    "rpc_uid",
                    "layer_start",
                    "layer_end",
                    "calls",
                    "total_latency_ms",
                    "average_latency_ms",
                    "last_latency_ms",
                ),
            )
            for hop in hops
            if isinstance(hop, Mapping)
        ]
        if isinstance(hops, list)
        else []
    )
    return result


def _contribution_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    return _pick(
        source,
        (
            "peer_id",
            "model_name",
            "layer_start",
            "layer_end",
            "layers_served",
            "requests_served",
            "failed_requests",
            "token_positions_served",
            "last_success_at",
            "last_error_at",
        ),
    )


def _incentives_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _pick(
        source,
        (
            "mode",
            "protocol_version",
            "application_public_key",
            "p2p_peer_id",
            "settlement_url_configured",
            "settlement_connectivity",
            "pending_submissions",
            "accepted_submissions",
            "rejected_submissions",
            "verified_credits",
            "ledger_entries",
            "accepted_receipts",
            "useful_positions_served",
            "token_ui_enabled",
            "transfers_enabled",
            "withdrawals_enabled",
        ),
    )
    contributions = source.get("local_contributions")
    result["local_contributions"] = (
        [
            _contribution_evidence(item)
            for item in contributions
            if isinstance(item, Mapping)
        ]
        if isinstance(contributions, list)
        else []
    )
    return result


def _serving_plan_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _pick(
        source,
        (
            "model_id",
            "coverage_revision",
            "total_layers",
            "current_runnable",
            "route_kind",
            "missing_ranges",
            "uncovered_ranges",
        ),
    )
    for field in ("selected_route", "standby_ranges"):
        entries = source.get(field)
        result[field] = (
            [
                _pick(
                    item,
                    ("peer_id", "node_id", "rpc_uid", "layer_start", "layer_end"),
                )
                for item in entries
                if isinstance(item, Mapping)
            ]
            if isinstance(entries, list)
            else []
        )
    return result


def _stats_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _pick(
        source,
        ("sampled_at", "cpu_percent", "ram_percent", "ram_used_gb", "ram_total_gb"),
    )
    process = source.get("process")
    if isinstance(process, Mapping):
        result["process"] = _pick(
            process,
            ("cpu_percent", "memory_percent", "rss_gb", "threads"),
        )
    gpu = source.get("gpu")
    if isinstance(gpu, Mapping):
        result["gpu"] = _pick(
            gpu,
            (
                "name",
                "util_percent",
                "vram_used_gb",
                "vram_reserved_gb",
                "vram_total_gb",
                "vram_percent",
            ),
        )
    else:
        result["gpu"] = None
    return result


def _chat_evidence(source: Mapping[str, Any]) -> dict[str, Any]:
    result = _pick(source, ("response", "node_trace", "tokens_generated", "error"))
    performance = source.get("performance")
    result["performance"] = (
        _generation_performance(performance)
        if isinstance(performance, Mapping)
        else None
    )
    return result


def _write_private_json(path: Path, document: Mapping[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        os.chmod(target, 0o600)
    finally:
        temporary.unlink(missing_ok=True)


def capture_evidence(
    options: CaptureOptions,
    *,
    opener: JsonOpener = urllib.request.urlopen,
) -> dict[str, Any]:
    if not options.participant.strip():
        raise EvidenceError("Participant label must not be empty")
    if not options.model_name.strip():
        raise EvidenceError("Model name must not be empty")
    if options.max_new_tokens <= 0:
        raise EvidenceError("max_new_tokens must be positive")
    if options.timeout <= 0:
        raise EvidenceError("timeout must be positive")
    if options.settlement_wait < 0:
        raise EvidenceError("settlement_wait must not be negative")
    backend_url = _validated_backend_url(options.backend_url)

    status = _request_json(backend_url, "/status", timeout=options.timeout, opener=opener)
    nodes = _request_json(backend_url, "/nodes", timeout=options.timeout, opener=opener)
    local_nodes = _request_json(
        backend_url,
        "/nodes/local",
        timeout=options.timeout,
        opener=opener,
    )
    models = _request_json(backend_url, "/models", timeout=options.timeout, opener=opener)
    generator = _request_json(
        backend_url,
        "/generator/status",
        timeout=options.timeout,
        opener=opener,
    )
    incentives_before = _request_json(
        backend_url,
        "/incentives/accounting",
        timeout=options.timeout,
        opener=opener,
    )
    try:
        stats = _request_json(
            backend_url,
            "/stats",
            timeout=options.timeout,
            opener=opener,
        )
        stats_error = None
    except EvidenceError:
        stats = {}
        stats_error = "unavailable"

    generation = None
    if options.run_inference:
        generation = _request_json(
            backend_url,
            "/chat",
            timeout=max(options.timeout, 300),
            payload={
                "message": options.prompt,
                "max_new_tokens": options.max_new_tokens,
                "do_sample": False,
            },
            opener=opener,
        )

    incentives_after = incentives_before
    deadline = time.monotonic() + max(0.0, options.settlement_wait)
    while options.run_inference and time.monotonic() < deadline:
        incentives_after = _request_json(
            backend_url,
            "/incentives/accounting",
            timeout=options.timeout,
            opener=opener,
        )
        if _integer(incentives_after.get("pending_submissions")) == 0:
            break
        time.sleep(0.25)

    raw_nodes = nodes.get("nodes")
    raw_models = models.get("models")
    selected_model = next(
        (
            item
            for item in (raw_models if isinstance(raw_models, list) else [])
            if isinstance(item, Mapping) and item.get("id") == options.model_name
        ),
        None,
    )
    serving_plan = None
    if isinstance(selected_model, Mapping):
        try:
            total_layers = int(selected_model["num_layers"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceError("Selected model has an invalid layer count") from exc
        encoded_model = urllib.parse.quote(options.model_name, safe="")
        serving_plan = _request_json(
            backend_url,
            f"/models/{encoded_model}/serving-plan?layer_count={total_layers}",
            timeout=options.timeout,
            opener=opener,
        )
    raw_local_nodes = local_nodes.get("nodes")
    document = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": _utc_now(),
        "participant": options.participant.strip(),
        "runtime": {
            "python_version": platform.python_version(),
            "hivemind_version": _version("hivemind"),
            "platform": platform.system(),
            "platform_release": platform.release(),
            "machine": platform.machine(),
        },
        "expectation": {
            "model_name": options.model_name,
            "inference_requested": options.run_inference,
            "max_new_tokens": options.max_new_tokens if options.run_inference else None,
        },
        "backend": {
            "status": _pick(
                status,
                ("status", "node_running", "gpu_available", "generator_ready"),
            ),
            "nodes": [
                _node_evidence(item)
                for item in (raw_nodes if isinstance(raw_nodes, list) else [])
                if isinstance(item, Mapping)
            ],
            "local_nodes": [
                _node_evidence(item)
                for item in (
                    raw_local_nodes if isinstance(raw_local_nodes, list) else []
                )
                if isinstance(item, Mapping)
            ],
            "model": (
                _model_evidence(selected_model)
                if isinstance(selected_model, Mapping)
                else None
            ),
            "serving_plan": (
                _serving_plan_evidence(serving_plan)
                if isinstance(serving_plan, Mapping)
                else None
            ),
            "generator": _generator_evidence(generator),
            "incentives_before": _incentives_evidence(incentives_before),
            "incentives_after": _incentives_evidence(incentives_after),
            "stats": _stats_evidence(stats) if stats else None,
            "stats_error": stats_error,
        },
        "generation": (
            _chat_evidence(generation) if isinstance(generation, Mapping) else None
        ),
    }
    _write_private_json(options.output, document)
    return document


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _local_contributions(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    backend = document.get("backend")
    incentives = backend.get("incentives_after") if isinstance(backend, Mapping) else None
    contributions = (
        incentives.get("local_contributions")
        if isinstance(incentives, Mapping)
        else None
    )
    if not isinstance(contributions, list):
        return {}
    return {
        str(item.get("peer_id")): dict(item)
        for item in contributions
        if isinstance(item, Mapping) and item.get("peer_id")
    }


def _validate_standby_nonpayment(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    model_name: str,
    selected_peer_ids: set[str],
    serving_plans: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    verified: list[str] = []
    if before.get("schema_version") != SCHEMA_VERSION:
        errors.append("Standby before snapshot has an unsupported schema version")
    if after.get("schema_version") != SCHEMA_VERSION:
        errors.append("Standby after snapshot has an unsupported schema version")
    try:
        before_time = datetime.fromisoformat(str(before["captured_at"]))
        after_time = datetime.fromisoformat(str(after["captured_at"]))
        if after_time <= before_time:
            errors.append("Standby after snapshot must be newer than the before snapshot")
    except (KeyError, TypeError, ValueError):
        errors.append("Standby snapshots have invalid capture timestamps")
    before_contributions = _local_contributions(before)
    after_contributions = _local_contributions(after)
    shared_peers = sorted(set(before_contributions) & set(after_contributions))
    if not shared_peers:
        return ["Standby snapshots have no common local contribution peer"], verified

    standby_peers = {
        str(item.get("peer_id"))
        for plan in serving_plans
        for item in (
            plan.get("standby_ranges")
            if isinstance(plan.get("standby_ranges"), list)
            else []
        )
        if isinstance(item, Mapping) and item.get("peer_id")
    }
    for peer_id in shared_peers:
        first = before_contributions[peer_id]
        second = after_contributions[peer_id]
        if (
            first.get("model_name") != model_name
            or second.get("model_name") != model_name
        ):
            continue
        first_range = (first.get("layer_start"), first.get("layer_end"))
        second_range = (second.get("layer_start"), second.get("layer_end"))
        if first_range != second_range:
            errors.append(f"Standby peer {peer_id[:12]} changed its served range")
            continue
        if peer_id in selected_peer_ids:
            errors.append(
                f"Peer {peer_id[:12]} was selected and cannot prove standby non-payment"
            )
            continue
        if peer_id not in standby_peers:
            errors.append(f"Peer {peer_id[:12]} was not classified as standby")
            continue
        changed_fields = [
            field
            for field in ("requests_served", "token_positions_served")
            if _integer(second.get(field)) != _integer(first.get(field))
        ]
        if changed_fields:
            errors.append(
                f"Standby peer {peer_id[:12]} accounting changed: "
                + ", ".join(changed_fields)
            )
            continue
        verified.append(peer_id)
    if not verified and not errors:
        errors.append("Standby snapshots contain no contribution for the selected model")
    return errors, verified


def validate_evidence(
    documents: Sequence[Mapping[str, Any]],
    *,
    model_name: str,
    expected_mode: str,
    expected_incentives: str,
    min_route_peers: int = 2,
    standby_before: Mapping[str, Any] | None = None,
    standby_after: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if expected_mode not in {"relay", "direct"}:
        raise EvidenceError("expected_mode must be relay or direct")
    if expected_incentives not in {"any", "off", "shadow", "credit"}:
        raise EvidenceError("expected_incentives is invalid")
    if min_route_peers <= 0:
        raise EvidenceError("min_route_peers must be positive")
    if (standby_before is None) != (standby_after is None):
        raise EvidenceError("Both standby before and after evidence are required")
    errors: list[str] = []
    participants: list[str] = []
    nodes_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    total_layers: int | None = None
    generation_documents: list[Mapping[str, Any]] = []
    local_owners: dict[str, set[str]] = {}
    serving_plans: list[Mapping[str, Any]] = []

    if len(documents) < 2:
        errors.append("At least two participant evidence files are required")
    for index, document in enumerate(documents):
        label = str(document.get("participant", "")).strip()
        if document.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"Evidence {index + 1} has an unsupported schema version")
        if not label:
            errors.append(f"Evidence {index + 1} has no participant label")
        participants.append(label)
        expectation = document.get("expectation")
        if (
            not isinstance(expectation, Mapping)
            or expectation.get("model_name") != model_name
        ):
            errors.append(f"{label or index + 1}: model expectation does not match")
        backend = document.get("backend")
        if not isinstance(backend, Mapping):
            errors.append(f"{label or index + 1}: backend evidence is missing")
            continue
        model = backend.get("model")
        if not isinstance(model, Mapping):
            errors.append(f"{label or index + 1}: selected model evidence is missing")
        else:
            try:
                candidate_layers = int(model["num_layers"])
                if total_layers is None:
                    total_layers = candidate_layers
                elif total_layers != candidate_layers:
                    errors.append("Participants disagree on the model layer count")
            except (KeyError, TypeError, ValueError):
                errors.append(f"{label or index + 1}: model layer count is invalid")
        serving_plan = backend.get("serving_plan")
        if isinstance(serving_plan, Mapping):
            serving_plans.append(serving_plan)
        raw_nodes = backend.get("nodes")
        if isinstance(raw_nodes, list):
            for node in raw_nodes:
                if not isinstance(node, Mapping) or node.get("model_name") != model_name:
                    continue
                try:
                    key = (
                        str(node["peer_id"]),
                        int(node["layer_start"]),
                        int(node["layer_end"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                nodes_by_key[key] = dict(node)
        raw_local_nodes = backend.get("local_nodes")
        if isinstance(raw_local_nodes, list):
            for node in raw_local_nodes:
                if not isinstance(node, Mapping) or node.get("model_name") != model_name:
                    continue
                peer_id = str(node.get("peer_id", "")).strip()
                if peer_id:
                    local_owners.setdefault(peer_id, set()).add(label)
        incentives = backend.get("incentives_after")
        if expected_incentives != "any":
            if (
                not isinstance(incentives, Mapping)
                or incentives.get("mode") != expected_incentives
            ):
                errors.append(
                    f"{label or index + 1}: incentives mode is not {expected_incentives}"
                )
            elif expected_incentives in {"shadow", "credit"}:
                if incentives.get("settlement_connectivity") != "connected":
                    errors.append(f"{label or index + 1}: settlement is not connected")
                if _integer(incentives.get("pending_submissions")) != 0:
                    errors.append(
                        f"{label or index + 1}: settlement submissions are pending"
                    )
        if document.get("generation") is not None:
            generation_documents.append(document)

    if len(set(participants)) != len(participants):
        errors.append("Participant labels must be unique")
    route: list[dict[str, Any]] = []
    if total_layers is None or total_layers <= 0:
        errors.append("A positive common model layer count is required")
    else:
        candidates = [
            node
            for node in nodes_by_key.values()
            if node.get("running") is True
            and node.get("layers_loaded") is True
            and node.get("rpc_running") is True
        ]
        route, _ = select_route(candidates, total_layers)
        if not route:
            missing = route_requirement_ranges(candidates, total_layers)
            rendered = ", ".join(f"{item['start']}-{item['end']}" for item in missing)
            errors.append(f"No complete adjacent route; needs layers {rendered}")
        else:
            planned_spans = [
                (int(node["layer_start"]), int(node["layer_end"])) for node in route
            ]
            for document in generation_documents:
                generation = document.get("generation")
                performance = (
                    generation.get("performance")
                    if isinstance(generation, Mapping)
                    else None
                )
                hops = (
                    performance.get("hop_metrics")
                    if isinstance(performance, Mapping)
                    else None
                )
                if not isinstance(hops, list):
                    continue
                try:
                    actual_keys = [
                        (
                            str(hop["peer_id"]),
                            int(hop["layer_start"]),
                            int(hop["layer_end"]),
                        )
                        for hop in hops
                        if isinstance(hop, Mapping)
                    ]
                except (KeyError, TypeError, ValueError):
                    continue
                actual_spans = [(start, end) for _, start, end in actual_keys]
                if actual_spans == planned_spans and all(
                    key in nodes_by_key for key in actual_keys
                ):
                    route = [nodes_by_key[key] for key in actual_keys]
                    break
            selected_peers = {str(node.get("peer_id", "")) for node in route}
            if len(selected_peers) < min_route_peers:
                errors.append(
                    f"Selected route uses {len(selected_peers)} distinct peers; "
                    f"requires at least {min_route_peers}"
                )
            for node in route:
                peer = str(node.get("peer_id", ""))[:12]
                if node.get("connection_mode") != expected_mode:
                    errors.append(
                        f"Selected peer {peer} is not in {expected_mode} mode"
                    )
                if node.get("transport_verified") is not True:
                    errors.append(f"Selected peer {peer} has unverified transport")
            owner_labels = {
                owner
                for peer_id in selected_peers
                for owner in local_owners.get(peer_id, set())
                if owner
            }
            unowned_peers = sorted(
                peer_id for peer_id in selected_peers if peer_id not in local_owners
            )
            if unowned_peers:
                rendered = ", ".join(peer_id[:12] for peer_id in unowned_peers)
                errors.append(
                    f"Selected route peers lack local ownership evidence: {rendered}"
                )
            if len(owner_labels) < min_route_peers:
                errors.append(
                    f"Selected route is owned by {len(owner_labels)} participant labels; "
                    f"requires at least {min_route_peers}"
                )

    if not generation_documents:
        errors.append("No participant captured a generation result")
    for document in generation_documents:
        label = str(document.get("participant", "unknown"))
        backend = document["backend"]
        generator = backend.get("generator")
        generation = document.get("generation")
        if not isinstance(generator, Mapping) or not (
            generator.get("ready") is True and generator.get("route_ready") is True
        ):
            errors.append(f"{label}: generator was not route-ready")
        if not isinstance(generation, Mapping):
            errors.append(f"{label}: generation payload is missing")
            continue
        if generation.get("error"):
            errors.append(f"{label}: generation returned an error")
        if not _positive_number(generation.get("tokens_generated")):
            errors.append(f"{label}: generation produced no tokens")
        if not isinstance(generation.get("node_trace"), list) or not generation.get("node_trace"):
            errors.append(f"{label}: generation route trace is empty")
        performance = generation.get("performance")
        if not isinstance(performance, Mapping):
            errors.append(f"{label}: generation performance is missing")
            continue
        for field in ("total_duration_ms", "generated_tokens", "tokens_per_second"):
            if not _positive_number(performance.get(field)):
                errors.append(f"{label}: {field} is missing or non-positive")
        if performance.get("time_to_first_token_ms") is None:
            errors.append(f"{label}: time_to_first_token_ms is missing")
        hops = performance.get("hop_metrics")
        if not isinstance(hops, list) or len(hops) < len(route):
            errors.append(f"{label}: per-hop timing evidence is incomplete")
        elif route:
            expected_hops = [
                (
                    str(node.get("peer_id", "")),
                    int(node["layer_start"]),
                    int(node["layer_end"]),
                )
                for node in route
            ]
            try:
                actual_hops = [
                    (
                        str(hop["peer_id"]),
                        int(hop["layer_start"]),
                        int(hop["layer_end"]),
                    )
                    for hop in hops
                    if isinstance(hop, Mapping)
                ]
            except (KeyError, TypeError, ValueError):
                actual_hops = []
            if actual_hops != expected_hops:
                errors.append(f"{label}: timed hops do not match the selected route")
        if expected_incentives in {"shadow", "credit"}:
            before = backend.get("incentives_before")
            incentives = backend.get("incentives_after")
            accepted_before = (
                _integer(before.get("accepted_submissions"))
                if isinstance(before, Mapping)
                else 0
            )
            accepted_after = (
                _integer(incentives.get("accepted_submissions"))
                if isinstance(incentives, Mapping)
                else 0
            )
            if accepted_after <= accepted_before:
                errors.append(f"{label}: no accepted receipt submission was recorded")

    standby_verified: list[str] = []
    if standby_before is not None and standby_after is not None:
        standby_plans = [*serving_plans]
        for document in (standby_before, standby_after):
            backend = document.get("backend")
            plan = backend.get("serving_plan") if isinstance(backend, Mapping) else None
            if isinstance(plan, Mapping):
                standby_plans.append(plan)
        standby_errors, standby_verified = _validate_standby_nonpayment(
            standby_before,
            standby_after,
            model_name=model_name,
            selected_peer_ids={str(node.get("peer_id", "")) for node in route},
            serving_plans=standby_plans,
        )
        errors.extend(standby_errors)

    report = {
        "schema_version": SCHEMA_VERSION,
        "validated_at": _utc_now(),
        "ok": not errors,
        "model_name": model_name,
        "expected_mode": expected_mode,
        "expected_incentives": expected_incentives,
        "participants": participants,
        "selected_route": [
            _pick(node, ("peer_id", "layer_start", "layer_end", "connection_mode"))
            for node in route
        ],
        "standby_nonpayment_verified": standby_verified,
        "errors": errors,
    }
    return report


def _load_documents(paths: Sequence[Path]) -> list[dict[str, Any]]:
    documents = []
    for path in paths:
        try:
            document = json.loads(path.expanduser().read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError(f"Could not read evidence file {path}: {exc}") from exc
        if not isinstance(document, dict):
            raise EvidenceError(f"Evidence file {path} must contain a JSON object")
        documents.append(document)
    return documents


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture", help="capture sanitized local evidence")
    capture.add_argument("--participant", required=True)
    capture.add_argument("--backend-url", default="http://127.0.0.1:8000")
    capture.add_argument("--model", required=True)
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--run-inference", action="store_true")
    capture.add_argument("--prompt", default=DEFAULT_PROMPT)
    capture.add_argument("--max-new-tokens", type=int, default=8)
    capture.add_argument("--timeout", type=float, default=30)
    capture.add_argument("--settlement-wait", type=float, default=10)

    validate = subparsers.add_parser("validate", help="validate combined device evidence")
    validate.add_argument("evidence", type=Path, nargs="+")
    validate.add_argument("--model", required=True)
    validate.add_argument("--expected-mode", choices=("relay", "direct"), default="relay")
    validate.add_argument(
        "--expected-incentives",
        choices=("any", "off", "shadow", "credit"),
        default="any",
    )
    validate.add_argument("--min-route-peers", type=int, default=2)
    validate.add_argument("--standby-before", type=Path)
    validate.add_argument("--standby-after", type=Path)
    validate.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "capture":
            document = capture_evidence(
                CaptureOptions(
                    participant=args.participant,
                    backend_url=args.backend_url,
                    model_name=args.model,
                    output=args.output,
                    run_inference=args.run_inference,
                    prompt=args.prompt,
                    max_new_tokens=args.max_new_tokens,
                    timeout=args.timeout,
                    settlement_wait=args.settlement_wait,
                )
            )
            print(json.dumps(document, indent=2, sort_keys=True))
            return 0
        documents = _load_documents(args.evidence)
        standby_documents = (
            _load_documents([args.standby_before, args.standby_after])
            if args.standby_before is not None and args.standby_after is not None
            else None
        )
        if (args.standby_before is None) != (args.standby_after is None):
            raise EvidenceError("Both --standby-before and --standby-after are required")
        report = validate_evidence(
            documents,
            model_name=args.model,
            expected_mode=args.expected_mode,
            expected_incentives=args.expected_incentives,
            min_route_peers=args.min_route_peers,
            standby_before=standby_documents[0] if standby_documents else None,
            standby_after=standby_documents[1] if standby_documents else None,
        )
        if args.output:
            _write_private_json(args.output, report)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["ok"] else 1
    except EvidenceError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
