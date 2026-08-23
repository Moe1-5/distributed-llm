"""
server.py — FastAPI backend for DistribLLM.

New in this version:
    GET /models  — returns the validated list of supported models
                   from constants.py. Frontend uses this to build
                   the model dropdown instead of free text input.
"""

import asyncio
import inspect
import json
import os
import sys
import threading
import time
import weakref
from dataclasses import dataclass, field
from contextlib import aclosing, asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlparse
from uuid import uuid4

import hivemind
import torch
from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from huggingface_hub import HfApi
from huggingface_hub.errors import (
    GatedRepoError,
    HFValidationError,
    HfHubHTTPError,
    RepositoryNotFoundError,
)
from hivemind.utils.logging import get_logger
from pydantic import BaseModel, field_validator

from api.env_loader import load_project_env

# Load the repository environment before importing modules whose constants are
# evaluated at import time (DHT lease horizons, recovery thresholds, and the
# control-plane refresh cadence).
load_project_env()

from api.lifecycle_jobs import LifecycleJobStore
from api.runtime_state import RuntimeStateStore
from network.supervisor import NetworkSupervisor
from node.gpu_monitor import GPUMonitor
from node.node import Node
from node.rpc_server import DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
from client.sequential import RemoteSequential, shutdown_remote_expert_p2p
from client.coverage import build_serving_plan, evaluate_candidate
from client.generation import DistributedGenerator, GeneratorOperationBusyError
from incentives.runtime import get_useful_work_runtime
from incentives.access import AccessError, get_api_access_manager
from placement.client import (
    PlacementClientError,
    create_placement_runtime_from_env,
)
from api.local_models import (
    LocalModelDeletionError,
    LocalModelValidationError,
    get_local_model_import,
    get_local_model_path,
    import_local_model,
    inspect_local_model,
    list_local_models,
    remove_local_model,
)
from api.hf_oauth import (
    HuggingFaceOAuthError,
    cancel_huggingface_download_job,
    disconnect_huggingface,
    get_huggingface_connection,
    get_huggingface_download_job,
    poll_huggingface_device_flow,
    start_huggingface_model_download,
    start_huggingface_device_flow,
)
from api.settings import get_hf_token, save_hf_token, delete_hf_token, token_is_set
from constants import (
    SUPPORTED_MODELS,
    DHT_PREFIX,
    get_initial_peers,
    get_p2p_network_config,
    get_role_identity_path,
)

logger = get_logger(__name__)
DEFAULT_TRACE_DIR = Path(__file__).resolve().parents[1] / "traces"
TRACE_DIR = Path(os.environ.get("DISTRIBLLM_TRACE_DIR", str(DEFAULT_TRACE_DIR)))


# ---------------------------------------------------------------------------
# Startup validation
# ---------------------------------------------------------------------------

def _validate_environment() -> None:
    if sys.version_info < (3, 12):
        raise RuntimeError(
            f"Python 3.12+ required, got {sys.version_info.major}.{sys.version_info.minor}"
        )
    for pkg in ("torch", "hivemind", "transformers"):
        try:
            mod = __import__(pkg)
            if not getattr(mod, "__version__", None):
                raise RuntimeError(f"{pkg} version string empty")
        except ImportError as e:
            raise RuntimeError(f"{pkg} not installed: {e}") from e

    import torch as t
    import hivemind as h
    logger.info(
        f"Environment OK | torch={t.__version__} | "
        f"hivemind={h.__version__} | cuda={t.cuda.is_available()}"
    )


def _format_route_trace(route: list[dict]) -> list[str]:
    return [
        f"{item['peer_id'][:8]}… (layers {item['layer_start']}→{item['layer_end']})"
        for item in route
    ]


def _is_cuda_out_of_memory(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "cuda" in message
        and (
            "out of memory" in message
            or "cudaerrormemoryallocation" in message
            or "cublas_status_alloc_failed" in message
        )
    )


def _cuda_memory_error_response(model_name: str, *, layer_start: int | None = None, layer_end: int | None = None) -> dict:
    layer_hint = ""
    if layer_start is not None and layer_end is not None:
        layer_hint = f" Current request was layers {layer_start}-{layer_end}."
    return {
        "status": "error",
        "error": "cuda_out_of_memory",
        "message": (
            f"CUDA ran out of memory while loading {model_name}.{layer_hint} "
            "Reduce the served layer range, stop/delete other local nodes or the generator, "
            "switch this node to CPU, or try a smaller model."
        ),
    }


def _is_huggingface_auth_expired(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "oauth token has expired" in message
        or "exp claim timestamp check failed" in message
        or ("401 unauthorized" in message and "huggingface.co" in message)
    )


def _huggingface_reconnect_response(model_name: str) -> dict:
    return {
        "status": "error",
        "error": "huggingface_reconnect_required",
        "message": (
            f"Hugging Face authorization expired while accessing {model_name}. "
            "Reconnect Hugging Face, then try again."
        ),
    }


def _cleanup_failed_node(candidate: object | None) -> bool:
    if candidate is None or not hasattr(candidate, "stop"):
        return True
    try:
        return candidate.stop() is not False
    except Exception as exc:
        logger.warning("Failed to clean up partially started node: %s", exc)
        return False


def _cleanup_failed_generator(candidate: object | None) -> bool:
    cleanup_complete = True
    if candidate is not None and hasattr(candidate, "unload"):
        try:
            cleanup_complete = candidate.unload() is not False
        except Exception as exc:
            logger.warning("Failed to clean up partially loaded generator: %s", exc)
            cleanup_complete = False
    torch.cuda.empty_cache()
    return cleanup_complete


def _safe_filename_part(value: str) -> str:
    safe = "".join(char if char.isalnum() else "-" for char in value.lower())
    safe = "-".join(part for part in safe.split("-") if part)
    return safe[:80] or "trace"


def _write_generation_trace(trace: dict) -> dict:
    created_at = datetime.now(timezone.utc)
    trace_id = uuid4().hex[:12]
    model_part = _safe_filename_part(str(trace.get("model_name", "unknown-model")))
    timestamp_part = created_at.strftime("%Y%m%dT%H%M%SZ")
    trace_dir = TRACE_DIR
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"{timestamp_part}-{model_part}-{trace_id}.json"
    document = {
        "schema_version": 1,
        "trace_id": trace_id,
        "created_at": created_at.isoformat(),
        "trace": trace,
    }
    trace_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "trace_id": trace_id,
        "trace_file": str(trace_path),
        "trace_created_at": document["created_at"],
    }


def _trace_generation_config_key(trace: dict) -> str:
    return json.dumps(
        trace.get("generation_config", {}),
        sort_keys=True,
        separators=(",", ":"),
    )


def _trace_route_shape(trace: dict) -> dict:
    steps = trace.get("steps", [])
    hidden_shapes = [
        {
            "step": step.get("step"),
            "before": step.get("hidden_shape_before_route"),
            "after": step.get("hidden_shape_after_route"),
        }
        for step in steps
    ]
    return {
        "node_trace": trace.get("node_trace", []),
        "hop_count": len(trace.get("node_trace", [])),
        "hidden_shapes": hidden_shapes,
    }


def _trace_replacement_flags(trace: dict) -> dict:
    prompt_steps = trace.get("prompt_tokens", [])
    generation_steps = trace.get("steps", [])
    top_candidate_hits = [
        {
            "step": step.get("step"),
            "token_id": candidate.get("token_id"),
            "token_text": candidate.get("token_text"),
        }
        for step in generation_steps
        for candidate in step.get("top_candidates", [])
        if candidate.get("token_text_contains_replacement_char")
    ]
    selected_hits = [
        {
            "step": step.get("step"),
            "token_id": step.get("token_id"),
            "token_text": step.get("token_text"),
        }
        for step in generation_steps
        if step.get("token_text_contains_replacement_char")
        or step.get("decoded_output_contains_replacement_char")
    ]
    prompt_hits = [
        {
            "token_id": token.get("token_id"),
            "token_text": token.get("token_text"),
        }
        for token in prompt_steps
        if token.get("token_text_contains_replacement_char")
    ]
    return {
        "response_contains_replacement_char": bool(
            trace.get("response_contains_replacement_char")
        ),
        "prompt_token_hits": prompt_hits,
        "selected_token_hits": selected_hits,
        "top_candidate_hits": top_candidate_hits,
    }


def _trace_summary(trace_document: dict, trace_path: Path) -> dict:
    trace = trace_document.get("trace", trace_document)
    steps = trace.get("steps", [])
    top_candidate_ids = [
        [candidate.get("token_id") for candidate in step.get("top_candidates", [])]
        for step in steps
    ]
    outside_top_candidates = [
        {
            "step": step.get("step"),
            "token_id": step.get("token_id"),
            "token_text": step.get("token_text"),
        }
        for step in steps
        if step.get("selected_in_top_candidates") is False
    ]
    return {
        "trace_id": trace_document.get("trace_id"),
        "trace_file": str(trace_path),
        "created_at": trace_document.get("created_at"),
        "schema_version": trace_document.get("schema_version"),
        "model_name": trace.get("model_name"),
        "prompt": trace.get("prompt"),
        "generation_config": trace.get("generation_config", {}),
        "generation_config_key": _trace_generation_config_key(trace),
        "prompt_token_ids": trace.get("prompt_token_ids", []),
        "selected_token_ids": [step.get("token_id") for step in steps],
        "selected_token_texts": [step.get("token_text") for step in steps],
        "top_candidate_ids": top_candidate_ids,
        "response": trace.get("response", ""),
        "step_count": len(steps),
        "route_shape": _trace_route_shape(trace),
        "replacement_flags": _trace_replacement_flags(trace),
        "selected_outside_top_candidates": outside_top_candidates,
    }


def _compare_trace_group(group: list[dict]) -> list[dict]:
    if len(group) < 2:
        return []

    baseline = group[0]
    discrepancies: list[dict] = []
    comparisons = [
        ("prompt_token_mismatch", "prompt_token_ids"),
        ("selected_token_mismatch", "selected_token_ids"),
        ("top_candidate_mismatch", "top_candidate_ids"),
        ("decoded_output_mismatch", "response"),
        ("route_shape_mismatch", "route_shape"),
    ]

    for candidate in group[1:]:
        differing_fields = [
            category
            for category, field in comparisons
            if candidate.get(field) != baseline.get(field)
        ]
        if differing_fields:
            discrepancies.append(
                {
                    "trace_id": candidate.get("trace_id"),
                    "baseline_trace_id": baseline.get("trace_id"),
                    "categories": differing_fields,
                }
            )
    return discrepancies


def _analyze_generation_traces(
    trace_dir: Optional[Path] = None,
    model_name: Optional[str] = None,
) -> dict:
    directory = trace_dir or TRACE_DIR
    summaries: list[dict] = []
    errors: list[dict] = []

    for trace_path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            document = json.loads(trace_path.read_text(encoding="utf-8"))
            summary = _trace_summary(document, trace_path)
        except Exception as e:
            errors.append({"trace_file": str(trace_path), "error": str(e)})
            continue
        if model_name is not None and summary.get("model_name") != model_name:
            continue
        summaries.append(summary)

    grouped: dict[tuple[object, object, object], list[dict]] = {}
    for summary in summaries:
        key = (
            summary.get("model_name"),
            summary.get("prompt"),
            summary.get("generation_config_key"),
        )
        grouped.setdefault(key, []).append(summary)

    cross_run_discrepancies = [
        discrepancy
        for group in grouped.values()
        for discrepancy in _compare_trace_group(group)
    ]
    replacement_traces = [
        summary["trace_id"]
        for summary in summaries
        if summary["replacement_flags"]["response_contains_replacement_char"]
        or summary["replacement_flags"]["prompt_token_hits"]
        or summary["replacement_flags"]["selected_token_hits"]
        or summary["replacement_flags"]["top_candidate_hits"]
    ]
    outside_top_candidate_traces = [
        {
            "trace_id": summary["trace_id"],
            "steps": summary["selected_outside_top_candidates"],
        }
        for summary in summaries
        if summary["selected_outside_top_candidates"]
    ]

    return {
        "trace_dir": str(directory),
        "model_name": model_name,
        "trace_count": len(summaries),
        "error_count": len(errors),
        "errors": errors,
        "groups": [
            {
                "model_name": key[0],
                "prompt": key[1],
                "generation_config": group[0].get("generation_config", {}),
                "trace_ids": [summary.get("trace_id") for summary in group],
                "trace_count": len(group),
            }
            for key, group in grouped.items()
        ],
        "summaries": summaries,
        "discrepancy_counts": {
            "cross_run": len(cross_run_discrepancies),
            "replacement_char": len(replacement_traces),
            "selected_outside_top_candidates": len(outside_top_candidate_traces),
        },
        "discrepancies": {
            "cross_run": cross_run_discrepancies,
            "replacement_char_trace_ids": replacement_traces,
            "selected_outside_top_candidates": outside_top_candidate_traces,
        },
    }


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

gpu_monitor: Optional[GPUMonitor]           = None
node:        Optional[Node]                 = None
local_nodes: dict[str, Node]                = {}
generator:   Optional[DistributedGenerator] = None
client_dht:  Optional[hivemind.DHT]         = None
client_dht_prefix: str = DHT_PREFIX
_lifecycle_jobs = LifecycleJobStore()
_runtime_state = RuntimeStateStore()
_generator_start_lock = threading.Lock()
_generator_lifecycle_lock = threading.RLock()
_backend_closing = False
_generator_cleanup_in_progress = False
_generator_identity_quarantine: Optional[str] = None


@dataclass
class _GeneratorStartTransaction:
    operation_id: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    candidate_dht: Optional[object] = None
    sequential: Optional[object] = None
    candidate_generator: Optional[object] = None
    dht_future: Optional[asyncio.Future] = None
    route_validation_future: Optional[asyncio.Future] = None
    tensor_canary_future: Optional[asyncio.Future] = None
    load_future: Optional[asyncio.Future] = None
    role_registered: bool = False
    committed: bool = False


@dataclass
class _NodeStartTransaction:
    operation_id: str
    cancel_event: threading.Event = field(default_factory=threading.Event)
    done_event: threading.Event = field(default_factory=threading.Event)
    candidate: Optional[object] = None
    start_future: Optional[asyncio.Future] = None
    role_registered: bool = False
    committed: bool = False


@dataclass
class _NodeLifecycleOperation:
    """One exact API-owned lifecycle action for one registered local node."""

    node: object
    action: str
    done_event: threading.Event = field(default_factory=threading.Event)
    future: Optional[asyncio.Future] = None


@dataclass
class _GeneratorCleanupHandles:
    candidate_generator: Optional[object]
    sequential: Optional[object]
    dht: Optional[object]
    reason: str


@dataclass
class _LocalNodeShutdownAttempt:
    node: object
    thread: threading.Thread
    outcome: dict[str, object]


@dataclass
class _GeneratorDHTShutdownAttempt:
    """One retained shutdown call for one exact generator DHT."""

    dht: Optional[object]
    name: str
    done: threading.Event = field(default_factory=threading.Event)
    thread: Optional[threading.Thread] = None
    remote_expert_p2p: str = "not_started"
    errors: list[str] = field(default_factory=list)
    quarantines_identity: bool = False


@dataclass(frozen=True)
class _GeneratorDHTShutdownTombstone:
    """Weak terminal record that makes repeated close calls idempotent."""

    dht_ref: weakref.ReferenceType
    result: dict


_active_generator_start: Optional[_GeneratorStartTransaction] = None
_active_node_starts: dict[str, _NodeStartTransaction] = {}
_active_node_operations: dict[str, _NodeLifecycleOperation] = {}
_pending_generator_cleanup: Optional[_GeneratorCleanupHandles] = None
_local_node_shutdown_attempts: dict[str, _LocalNodeShutdownAttempt] = {}
_generator_dht_shutdown_attempts: dict[
    int,
    _GeneratorDHTShutdownAttempt | _GeneratorDHTShutdownTombstone,
] = {}

_GENERATOR_DHT_QUARANTINE_REASON = (
    "The previous generator transport has not stopped cleanly. Restart the "
    "backend before reusing its stable peer identity."
)
_GENERATOR_DHT_TOMBSTONE_ATTRIBUTE = (
    "_distribllm_generator_dht_shutdown_result"
)


def _record_network_event(**kwargs) -> dict:
    """Resolve the current runtime store so tests may replace it safely."""
    return _runtime_state.record_event(**kwargs)


network_supervisor = NetworkSupervisor(
    initial_peers=get_initial_peers(),
    trusted_relays=list(get_p2p_network_config().trusted_relays),
    dht_prefix=DHT_PREFIX,
    identity_path=get_role_identity_path("control-plane"),
    event_sink=_record_network_event,
)
placement_runtime = create_placement_runtime_from_env()


def _handle_placement_lease_lost(
    node_id: str,
    error: PlacementClientError,
) -> None:
    """Stop advertising work after the coordinator rejects exact ownership."""
    local_node = _find_local_node(node_id)
    if local_node is None:
        _runtime_state.record_event(
            kind="placement",
            phase="lease_lost",
            status="error",
            message=f"Placement ownership was lost for unregistered node {node_id}: {error}",
        )
        return
    operation, conflict = _begin_node_lifecycle_operation(local_node, "lease_lost")
    if conflict is not None or operation is None:
        _runtime_state.record_event(
            kind="placement",
            phase="lease_lost",
            status="error",
            message=(
                f"Placement ownership was lost for node {node_id}; an existing "
                "lifecycle owner must finish before cleanup."
            ),
            details={"coordinator_error": error.code, "lifecycle": conflict},
        )
        return
    try:
        dependency = _generator_dependency(local_node)
        generator_owner = dependency.get("generator_owner")
        if dependency.get("required") and generator_owner is not None:
            generator_owner.request_stop()
            _runtime_state.transition_generator(
                "suspended",
                model_name=generator_owner.model_name,
                components_loaded=True,
                route_ready=False,
                reasons=[
                    "A selected local provider lost authoritative placement ownership."
                ],
                health=dependency.get("health"),
            )
        stopped = local_node.turn_off()
        local_node.placement_lease = None
        _register_network_worker(
            local_node,
            "stopped" if stopped is not False else "cleanup_pending",
        )
        _invalidate_serving_plan_cache(local_node.model_name)
        _runtime_state.record_event(
            kind="placement",
            phase="lease_lost",
            status="error",
            message=(
                f"Node {node_id} stopped serving after authoritative placement "
                f"ownership was rejected: {error}"
            ),
            details={"coordinator_error": error.code},
        )
    finally:
        _finish_node_lifecycle_operation(operation)


placement_runtime.set_lease_lost_callback(_handle_placement_lease_lost)
_serving_plan_cache: dict[tuple[str, int], tuple[float, dict]] = {}
_serving_plan_cache_lock = threading.RLock()
_serving_plan_refresh_tasks: dict[tuple[str, int], asyncio.Task] = {}
_nodes_cache: Optional[tuple[float, dict]] = None
_nodes_refresh_task: Optional[asyncio.Task] = None
SERVING_PLAN_CACHE_SECONDS = max(
    1.0,
    float(os.environ.get("DISTRIBLLM_SERVING_PLAN_CACHE_SECONDS", "5")),
)


def _sync_primary_node() -> None:
    global node
    node = next(iter(local_nodes.values()), None)


def _local_node_list() -> list[Node]:
    if local_nodes:
        return list(local_nodes.values())
    return [node] if node is not None else []


def _register_local_node(local_node: Node) -> None:
    local_nodes[local_node.node_id] = local_node
    _sync_primary_node()


def _unregister_local_node(local_node: Node) -> None:
    node_id = getattr(local_node, "node_id", None)
    if node_id is not None:
        if placement_runtime.enabled:
            try:
                placement_runtime.release_node(node_id, "node_unregistered")
            except PlacementClientError as exc:
                logger.warning(
                    "Could not release placement while unregistering %s; lease will expire: %s",
                    node_id,
                    exc,
                )
        local_nodes.pop(node_id, None)
        if _supervisor_active():
            network_supervisor.unregister_role("worker", role_id=node_id)
            network_supervisor.request_refresh()
    global node
    if node is local_node:
        if local_nodes:
            _sync_primary_node()
        else:
            node = None


def _find_local_node(node_id: Optional[str] = None) -> Optional[Node]:
    if node_id:
        found = local_nodes.get(node_id)
        if found is not None:
            return found
        if node is not None and node.node_id == node_id:
            return node
        return None
    nodes = _local_node_list()
    if not nodes:
        return None
    if len(nodes) > 1:
        raise ValueError("node_id is required when multiple local nodes exist")
    return nodes[0]


def _active_local_dht() -> Optional[hivemind.DHT]:
    for local_node in _local_node_list():
        local_dht = getattr(local_node, "dht", None)
        if local_dht is not None:
            return local_dht
    return None


def _active_dht_prefix() -> str:
    for local_node in _local_node_list():
        return local_node.dht_prefix
    return client_dht_prefix or DHT_PREFIX


def _network_snapshot() -> dict:
    snapshot = network_supervisor.snapshot()
    roles = snapshot.setdefault("roles", {"workers": [], "generator": None})
    workers = {
        str(record.get("node_id")): record
        for record in roles.get("workers", [])
        if record.get("node_id") is not None
    }
    for local_node in _local_node_list():
        info_getter = getattr(local_node, "get_info", None)
        if not callable(info_getter):
            logger.warning(
                "Skipping local worker role overlay without get_info(): %r",
                local_node,
            )
            continue
        try:
            info = info_getter()
        except Exception as exc:
            logger.warning(
                "Failed to read authoritative local worker role state: %s",
                exc,
            )
            continue
        raw_node_id = info.get("node_id") or getattr(local_node, "node_id", None)
        if raw_node_id is None:
            logger.warning(
                "Skipping local worker role overlay without a node_id: %r",
                local_node,
            )
            continue
        node_id = str(raw_node_id)
        pending_getter = getattr(
            local_node,
            "has_pending_serving_cleanup",
            None,
        )
        cleanup_pending = bool(
            pending_getter() if callable(pending_getter) else False
        )
        announcement = info.get("announcement")
        if info.get("running"):
            worker_state = (
                "ready"
                if isinstance(announcement, dict)
                and announcement.get("publication_state") == "healthy"
                else "degraded"
            )
        else:
            worker_state = "cleanup_pending" if cleanup_pending else "stopped"
        existing = workers.get(node_id, {})
        workers[node_id] = {
            **existing,
            "node_id": node_id,
            "peer_id": info.get("peer_id"),
            "state": worker_state,
        }
    roles["workers"] = list(workers.values())
    resources = snapshot.get("resources")
    if isinstance(resources, dict):
        resources["registered_workers"] = len(workers)
    generator_role = roles.get("generator")
    if isinstance(generator_role, dict):
        generator_role["state"] = _runtime_state.generator_snapshot()["state"]
    return snapshot


def _supervisor_active(snapshot: Optional[dict] = None) -> bool:
    current = snapshot or _network_snapshot()
    return bool(
        current.get("state") != "disconnected"
        or current.get("resources", {}).get("control_dht")
        or current.get("resources", {}).get("discovery_task")
    )


def _network_response_fields(snapshot: Optional[dict] = None) -> dict:
    current = snapshot or _network_snapshot()
    state = str(current.get("state", "disconnected"))
    has_snapshot = current.get("snapshot_captured_at") is not None
    return {
        "snapshot_stale": state != "ready",
        "refreshing": state == "syncing",
        "snapshot_source": (
            "validated_dht"
            if state == "ready"
            else "dht_cache"
            if has_snapshot
            else "local_only"
        ),
        "snapshot_age_seconds": current.get("snapshot_age_seconds") or 0.0,
        "network_state": state,
        "network_revision": current.get("revision"),
        "network_topology_revision": current.get("topology_revision"),
        "network_failure": current.get("failure"),
    }


def _placement_participant_id(snapshot: Optional[dict] = None) -> str:
    current = snapshot or _network_snapshot()
    participant_id = str(current.get("control_peer_id") or "").strip()
    if not participant_id:
        raise PlacementClientError(
            "placement_identity_unavailable",
            "The persistent control-plane peer identity is not ready yet.",
        )
    return participant_id


def _placement_http_error(exc: PlacementClientError) -> HTTPException:
    status_code = (
        exc.status_code
        if exc.status_code is not None and 400 <= exc.status_code < 500
        else 503
    )
    return HTTPException(
        status_code=status_code,
        detail={
            "error": exc.code,
            "message": str(exc),
            **exc.details,
            "placement": placement_runtime.status(),
        },
    )


def _coordinator_serving_plan(model_id: str, placement: dict) -> dict:
    """Translate authoritative leases into the renderer's serving-plan contract."""
    occupancy = placement["occupancy_plan"]
    online = placement["online_plan"]
    recommendation = placement.get("recommendation")
    segments = []
    for segment in occupancy["segments"]:
        recommended = bool(
            recommendation is not None
            and int(recommendation["layer_start"]) <= int(segment["start"])
            and int(recommendation["layer_end"]) >= int(segment["end"])
        )
        segments.append({**segment, "recommended": recommended})
    return {
        "model_id": model_id,
        "coverage_revision": f"placement:{placement['topology_revision']}",
        "total_layers": placement["total_layers"],
        "requested_layer_count": placement["requested_layer_count"],
        "segments": segments,
        "missing_ranges": online["missing_ranges"],
        "uncovered_ranges": online["uncovered_ranges"],
        "projected_missing_ranges": occupancy["projected_missing_ranges"],
        "recommendation": recommendation,
        "current_runnable": online["current_runnable"],
        "projected_runnable": occupancy["projected_runnable"],
        "reachable_prefix": online["reachable_prefix"],
        "selected_route": online["selected_route"],
        "projected_route": occupancy["projected_route"],
        "route_kind": online["route_kind"],
        "projected_route_kind": occupancy["projected_route_kind"],
        "standby_ranges": online["standby_ranges"],
        "snapshot_stale": False,
        "refreshing": False,
        "snapshot_source": "placement_coordinator",
        "snapshot_age_seconds": 0.0,
        "placement": {
            "enabled": True,
            "authoritative": True,
            "capacity_available": placement["capacity_available"],
            "topology_revision": placement["topology_revision"],
            "model_revision": placement["model_revision"],
            "captured_at": placement["captured_at"],
            "reservations": placement["reservations"],
        },
        **{
            key: value
            for key, value in _network_response_fields().items()
            if key.startswith("network_")
        },
    }


def _role_initial_peers(requested_peers: list[str]) -> list[str]:
    """Keep every co-located role on the supervisor's configured swarm."""
    if not _supervisor_active():
        return list(requested_peers or get_initial_peers())
    supervisor_peers = list(
        getattr(network_supervisor, "initial_peers", get_initial_peers())
    )
    if requested_peers and set(requested_peers) != set(supervisor_peers):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "network_peers_mismatch",
                "message": (
                    "Worker and generator roles must use the backend supervisor's "
                    "bootstrap peer set. Save the shared network settings and restart "
                    "the backend before starting this role."
                ),
            },
        )
    return supervisor_peers


def _register_network_worker(local_node: Node, state: str) -> None:
    if not _supervisor_active():
        return
    info = local_node.get_info()
    announcement = info.get("announcement")
    role_state = state
    if (
        state == "ready"
        and isinstance(announcement, dict)
        and announcement.get("publication_state") != "healthy"
    ):
        role_state = "degraded"
    network_supervisor.register_role(
        "worker",
        role_id=local_node.node_id,
        peer_id=info.get("peer_id"),
        state=role_state,
    )
    network_supervisor.request_refresh()


def _local_node_key(info: dict) -> str:
    node_id = info.get("node_id")
    if node_id:
        return f"node:{node_id}"
    return (
        f"peer:{info.get('peer_id')}:{info.get('model_name')}:"
        f"{info.get('layer_start')}:{info.get('layer_end')}"
    )


def _merge_supervisor_and_local_nodes(
    discovered_nodes: list[dict],
    *,
    model_name: Optional[str] = None,
) -> list[dict]:
    """Overlay authoritative in-process worker state onto cached discovery."""
    merged: dict[str, dict] = {
        _local_node_key(info): info
        for info in discovered_nodes
        if model_name is None or info.get("model_name") == model_name
    }
    for info in _local_node_infos():
        if model_name is None or info.get("model_name") == model_name:
            merged[_local_node_key(info)] = info
    return list(merged.values())


def _local_node_infos() -> list[dict]:
    return [local_node.get_info() for local_node in _local_node_list()]


def _generator_topology_nodes(model_name: str) -> list[dict]:
    """Merge supervisor discovery with authoritative co-located worker state."""
    return _merge_supervisor_and_local_nodes(
        network_supervisor.nodes_for_model(model_name),
        model_name=model_name,
    )


def _has_overlapping_local_node(req) -> Optional[Node]:
    for local_node in _local_node_list():
        if (
            local_node.model_name != req.model_name
            or local_node.dht_prefix != req.dht_prefix
        ):
            continue
        if (
            local_node.layer_start == req.layer_start
            and local_node.layer_end == req.layer_end
        ):
            continue
        if req.layer_start < local_node.layer_end and req.layer_end > local_node.layer_start:
            return local_node
    return None


def _next_rpc_uid_suffix(req) -> Optional[int]:
    used_suffixes = [
        int(getattr(local_node, "rpc_uid_suffix", 0) or 0)
        for local_node in _local_node_list()
        if (
            local_node.model_name == req.model_name
            and local_node.layer_start == req.layer_start
            and local_node.layer_end == req.layer_end
            and local_node.dht_prefix == req.dht_prefix
        )
    ]
    if not used_suffixes:
        return None
    return max(used_suffixes) + 1


def _matching_local_replicas(req) -> list[Node]:
    return [
        local_node
        for local_node in _local_node_list()
        if (
            local_node.model_name == req.model_name
            and local_node.layer_start == req.layer_start
            and local_node.layer_end == req.layer_end
            and local_node.dht_prefix == req.dht_prefix
        )
    ]


def _invalidate_serving_plan_cache(model_name: str) -> None:
    global _nodes_cache
    with _serving_plan_cache_lock:
        for key in [key for key in _serving_plan_cache if key[0] == model_name]:
            _serving_plan_cache.pop(key, None)
        _nodes_cache = None


def _route_contains_local_node(route: list[dict], local_node: Node) -> bool:
    peer_id = local_node.get_peer_id()
    return any(
        str(item.get("peer_id")) == str(peer_id)
        and int(item.get("layer_start", -1)) == local_node.layer_start
        and int(item.get("layer_end", -1)) == local_node.layer_end
        for item in route
    )


def _generator_dependency(local_node: Node) -> dict:
    with _generator_lifecycle_lock:
        active_generator = generator
        if (
            active_generator is None
            or not active_generator.is_loaded()
            or active_generator.model_name != local_node.model_name
        ):
            return {
                "required": False,
                "alternate_available": False,
                "generator_owner": active_generator,
            }
        health_getter = getattr(
            active_generator.sequential,
            "get_health_readiness",
            None,
        )
        if not callable(health_getter):
            return {
                "required": True,
                "alternate_available": False,
                "generator_owner": active_generator,
            }
        health = health_getter()
        selected_route = health.get("selected_route", [])
        if not _route_contains_local_node(selected_route, local_node):
            return {
                "required": False,
                "alternate_available": False,
                "health": health,
                "generator_owner": active_generator,
            }
        alternate_available = any(
            not _route_contains_local_node(candidate.get("route", []), local_node)
            for candidate in health.get("alternate_routes", [])
        )
        return {
            "required": not alternate_available,
            "alternate_available": alternate_available,
            "health": health,
            "generator_owner": active_generator,
        }


async def _unload_generator_runtime(
    reason: str,
    timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> dict:
    global generator, client_dht, _generator_cleanup_in_progress
    global _pending_generator_cleanup
    with _generator_lifecycle_lock:
        active_start = _active_generator_start
        if active_start is not None and not active_start.committed:
            active_start.cancel_event.set()
            return {
                "status": "startup_cancelling",
                "reason": reason,
            }
        if _generator_cleanup_in_progress:
            return {
                "status": "cleanup_in_progress",
                "reason": reason,
            }
        handles = _pending_generator_cleanup
        if handles is None and generator is None and client_dht is None:
            _runtime_state.transition_generator(
                "stopped",
                model_name=None,
                components_loaded=False,
                route_ready=False,
                reasons=[reason],
            )
            return {"status": "not_running"}
        if handles is None:
            candidate = generator
            handles = _GeneratorCleanupHandles(
                candidate_generator=candidate,
                sequential=None,
                dht=client_dht,
                reason=reason,
            )
            _pending_generator_cleanup = handles
            generator = None
            client_dht = None
        else:
            candidate = handles.candidate_generator
            handles.reason = reason
        _generator_cleanup_in_progress = True

    try:
        network_supervisor.unregister_role("generator")
    except Exception as exc:
        logger.warning("Could not unregister generator network role: %s", exc)
    if candidate is None:
        _runtime_state.transition_generator(
            "stopping",
            model_name=None,
            components_loaded=False,
            route_ready=False,
            reasons=[reason],
        )
    else:
        _runtime_state.transition_generator(
            "stopping",
            model_name=getattr(candidate, "model_name", None),
            route_ready=False,
            reasons=[reason],
        )

    cancellation_count = 0
    unload_error: Optional[BaseException] = None
    network = None
    cleanup_complete = True
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        if candidate is not None:
            request_stop = getattr(candidate, "request_stop", None)
            if callable(request_stop):
                request_stop()
            loop = asyncio.get_running_loop()
            unload_call = _bind_optional_timeout(
                candidate.unload,
                max(0.0, deadline - time.monotonic()),
            )
            unload_future = asyncio.ensure_future(
                loop.run_in_executor(None, unload_call)
            )
            cancellation_count += await _await_owned_future(unload_future)
            if unload_future.cancelled():
                cleanup_complete = False
            else:
                try:
                    cleanup_complete = unload_future.result() is not False
                except BaseException as exc:
                    unload_error = exc
                    cleanup_complete = False
            if cleanup_complete:
                handles.candidate_generator = None
        elif handles.sequential is not None:
            stop_health_monitor = getattr(
                handles.sequential,
                "stop_health_monitor",
                None,
            )
            if callable(stop_health_monitor):
                loop = asyncio.get_running_loop()
                monitor_future = asyncio.ensure_future(
                    loop.run_in_executor(
                        None,
                        lambda: _call_with_optional_timeout(
                            stop_health_monitor,
                            max(0.0, deadline - time.monotonic()),
                        ),
                    )
                )
                cancellation_count += await _await_owned_future(monitor_future)
                if monitor_future.cancelled():
                    cleanup_complete = False
                else:
                    try:
                        cleanup_complete = monitor_future.result() is not False
                    except BaseException as exc:
                        unload_error = exc
                        cleanup_complete = False
            if cleanup_complete:
                handles.sequential = None

        if cleanup_complete and handles.dht is not None:
            dht = handles.dht
            loop = asyncio.get_running_loop()
            dht_future = asyncio.ensure_future(
                loop.run_in_executor(
                    None,
                    lambda: _close_generator_dht(
                        dht,
                        timeout=max(0.0, deadline - time.monotonic()),
                    ),
                )
            )
            cancellation_count += await _await_owned_future(dht_future)
            if dht_future.cancelled():
                cleanup_complete = False
            else:
                try:
                    network = dht_future.result()
                    cleanup_complete = network.get("status") == "stopped"
                except BaseException as exc:
                    unload_error = unload_error or exc
                    cleanup_complete = False
            if cleanup_complete:
                handles.dht = None
    finally:
        with _generator_lifecycle_lock:
            cleanup_finished = bool(
                cleanup_complete
                and handles.candidate_generator is None
                and handles.sequential is None
                and handles.dht is None
            )
            if cleanup_finished and _pending_generator_cleanup is handles:
                _pending_generator_cleanup = None
            try:
                if unload_error is not None:
                    _runtime_state.transition_generator(
                        "failed",
                        model_name=getattr(candidate, "model_name", None),
                        components_loaded=False,
                        route_ready=False,
                        reasons=[str(unload_error)],
                    )
                elif not cleanup_finished:
                    _runtime_state.transition_generator(
                        "stopping",
                        model_name=getattr(candidate, "model_name", None),
                        components_loaded=bool(
                            handles.candidate_generator is not None
                            and getattr(
                                handles.candidate_generator,
                                "is_loaded",
                                lambda: False,
                            )()
                        ),
                        route_ready=False,
                        reasons=[
                            "Generator cleanup exceeded its deadline; the exact "
                            "runtime handles were retained for a retry."
                        ],
                    )
                elif generator is None and client_dht is None:
                    _runtime_state.transition_generator(
                        "stopped",
                        model_name=None,
                        components_loaded=False,
                        route_ready=False,
                        reasons=[reason],
                    )
            finally:
                # Admission and the terminal cleanup state are committed while
                # holding the same owner lock. A replacement cannot publish its
                # startup state between these two changes.
                _generator_cleanup_in_progress = False

    if unload_error is not None:
        raise unload_error
    if not cleanup_finished:
        if cancellation_count:
            raise asyncio.CancelledError
        return {
            "status": "cleanup_pending",
            "network": network,
            "reason": reason,
        }
    if cancellation_count:
        raise asyncio.CancelledError
    return {"status": "unloaded", "network": network, "reason": reason}


def _generator_initial_peers(
    configured_peers: list[str],
    model_name: str,
    dht_prefix: str,
) -> list[str]:
    peers = list(configured_peers)
    for local_node in _local_node_list():
        if (
            local_node.model_name != model_name
            or local_node.dht_prefix != dht_prefix
            or not local_node.is_running()
        ):
            continue
        peers.extend(local_node.get_visible_maddrs())
    return list(dict.fromkeys(peer for peer in peers if peer))


def _generator_dht_kwargs(initial_peers: list[str]) -> dict:
    """Build a dialing-only client that can reach workers through circuit addresses."""
    identity_path = get_role_identity_path("generator")
    identity_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity_path.parent.chmod(0o700)
    if identity_path.exists():
        if not identity_path.is_file():
            raise RuntimeError("Generator P2P identity target is not a file")
        identity_path.chmod(0o600)
    return {
        "initial_peers": initial_peers,
        "start": True,
        "use_ipfs": False,
        "use_relay": True,
        "client_mode": True,
        "identity_path": str(identity_path),
    }


def _shutdown_local_nodes(
    timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> list[dict]:
    results: list[dict] = []
    deadline = time.monotonic() + max(0.0, timeout)
    for local_node in list(_local_node_list()):
        node_id = str(getattr(local_node, "node_id", None) or id(local_node))
        with _generator_lifecycle_lock:
            attempt = _local_node_shutdown_attempts.get(node_id)
            if attempt is not None and attempt.node is not local_node:
                results.append(
                    {"node_id": node_id, "status": "ownership_conflict"}
                )
                continue
            if attempt is None:
                outcome: dict[str, object] = {
                    "complete": False,
                    "error": None,
                }

                attempt_holder: dict[str, _LocalNodeShutdownAttempt] = {}

                def _stop_node(
                    owned_node=local_node,
                    owned_outcome=outcome,
                    owned_node_id=node_id,
                ) -> None:
                    try:
                        try:
                            stopped = owned_node.stop(
                                timeout=max(0.0, deadline - time.monotonic())
                            )
                        except TypeError:
                            stopped = owned_node.stop()
                        owned_outcome["complete"] = stopped is not False
                    except BaseException as exc:
                        owned_outcome["error"] = exc
                    finally:
                        owned_attempt = attempt_holder.get("attempt")
                        should_unregister = False
                        if (
                            owned_attempt is not None
                            and owned_outcome.get("complete")
                            and owned_outcome.get("error") is None
                        ):
                            with _generator_lifecycle_lock:
                                if (
                                    _local_node_shutdown_attempts.get(owned_node_id)
                                    is owned_attempt
                                ):
                                    _local_node_shutdown_attempts.pop(
                                        owned_node_id,
                                        None,
                                    )
                                    should_unregister = True
                        if should_unregister:
                            _unregister_local_node(owned_node)

                thread = threading.Thread(
                    target=_stop_node,
                    daemon=True,
                    name=f"local-node-stop-{node_id}",
                )
                attempt = _LocalNodeShutdownAttempt(local_node, thread, outcome)
                attempt_holder["attempt"] = attempt
                _local_node_shutdown_attempts[node_id] = attempt
                thread.start()

        attempt.thread.join(max(0.0, deadline - time.monotonic()))
        finished = not attempt.thread.is_alive()
        cleanup_complete = bool(
            finished
            and attempt.outcome.get("complete")
            and attempt.outcome.get("error") is None
        )
        if cleanup_complete:
            with _generator_lifecycle_lock:
                if _local_node_shutdown_attempts.get(node_id) is attempt:
                    _local_node_shutdown_attempts.pop(node_id, None)
            _unregister_local_node(local_node)
        elif finished:
            with _generator_lifecycle_lock:
                if _local_node_shutdown_attempts.get(node_id) is attempt:
                    _local_node_shutdown_attempts.pop(node_id, None)
        results.append(
            {
                "node_id": node_id,
                "status": "stopped" if cleanup_complete else "cleanup_pending",
            }
        )
    return results


def _generator_dht_shutdown_attempt(
    dht: object,
    *,
    name: str,
) -> _GeneratorDHTShutdownAttempt | _GeneratorDHTShutdownTombstone:
    """Start or recover the one retained shutdown attempt for ``dht``."""
    attempt_key = id(dht)
    with _generator_lifecycle_lock:
        existing = _generator_dht_shutdown_attempts.get(attempt_key)
        if existing is not None:
            existing_dht = (
                existing.dht_ref()
                if isinstance(existing, _GeneratorDHTShutdownTombstone)
                else existing.dht
            )
            if existing_dht is None:
                _generator_dht_shutdown_attempts.pop(attempt_key, None)
            elif existing_dht is not dht:
                raise RuntimeError(
                    "Generator DHT shutdown ownership collided with another object"
                )
            else:
                return existing

        attempt = _GeneratorDHTShutdownAttempt(dht=dht, name=name)
        _generator_dht_shutdown_attempts[attempt_key] = attempt

        def _shutdown() -> None:
            try:
                try:
                    remote_stopped = shutdown_remote_expert_p2p(dht)
                except BaseException as exc:
                    remote_stopped = False
                    with _generator_lifecycle_lock:
                        attempt.errors.append(
                            "Remote expert P2P shutdown raised "
                            f"{type(exc).__name__}: {exc}"
                        )
                    logger.warning(
                        "%s remote expert P2P cleanup raised: %s",
                        name,
                        exc,
                        exc_info=True,
                    )
                with _generator_lifecycle_lock:
                    attempt.remote_expert_p2p = (
                        "stopped" if remote_stopped else "error"
                    )
                    if not remote_stopped and not attempt.errors:
                        attempt.errors.append(
                            "Remote expert P2P shutdown did not complete successfully"
                        )

                try:
                    dht.shutdown()
                except BaseException as exc:
                    with _generator_lifecycle_lock:
                        attempt.errors.append(
                            f"DHT shutdown raised {type(exc).__name__}: {exc}"
                        )
                    logger.warning(
                        "%s raised during shutdown: %s",
                        name,
                        exc,
                        exc_info=True,
                    )
                else:
                    is_alive = getattr(dht, "is_alive", None)
                    join = getattr(dht, "join", None)
                    if callable(is_alive):
                        try:
                            while is_alive():
                                if not callable(join):
                                    raise RuntimeError(
                                        "DHT shutdown returned while its process was "
                                        "still alive and no join method is available"
                                    )
                                join(0.1)
                        except BaseException as exc:
                            with _generator_lifecycle_lock:
                                attempt.errors.append(
                                    "DHT process liveness verification raised "
                                    f"{type(exc).__name__}: {exc}"
                                )
                            logger.warning(
                                "%s could not verify DHT process exit: %s",
                                name,
                                exc,
                                exc_info=True,
                            )
            finally:
                attempt.done.set()

        thread = threading.Thread(target=_shutdown, daemon=True, name=name)
        attempt.thread = thread
        try:
            thread.start()
        except BaseException as exc:
            attempt.errors.append(
                f"Shutdown thread failed to start: {type(exc).__name__}: {exc}"
            )
            attempt.done.set()
        return attempt


def _finalize_successful_generator_dht_shutdown(
    attempt: _GeneratorDHTShutdownAttempt,
    result: dict,
) -> None:
    """Replace a successful strong owner with an exact weak tombstone."""
    dht = attempt.dht
    if dht is None:
        return
    attempt_key = id(dht)

    def _discard_tombstone(dht_ref: weakref.ReferenceType) -> None:
        with _generator_lifecycle_lock:
            current = _generator_dht_shutdown_attempts.get(attempt_key)
            if (
                isinstance(current, _GeneratorDHTShutdownTombstone)
                and current.dht_ref is dht_ref
            ):
                _generator_dht_shutdown_attempts.pop(attempt_key, None)

    try:
        dht_ref = weakref.ref(dht, _discard_tombstone)
    except TypeError:
        try:
            setattr(
                dht,
                _GENERATOR_DHT_TOMBSTONE_ATTRIBUTE,
                dict(result),
            )
        except (AttributeError, TypeError):
            # An unusual non-weak-referenceable, slot-only object must retain
            # its exact attempt to keep shutdown idempotent.
            return
        with _generator_lifecycle_lock:
            if _generator_dht_shutdown_attempts.get(attempt_key) is attempt:
                _generator_dht_shutdown_attempts.pop(attempt_key, None)
                attempt.dht = None
        return

    tombstone = _GeneratorDHTShutdownTombstone(
        dht_ref=dht_ref,
        result=dict(result),
    )
    with _generator_lifecycle_lock:
        if _generator_dht_shutdown_attempts.get(attempt_key) is attempt:
            _generator_dht_shutdown_attempts[attempt_key] = tombstone
            attempt.dht = None


def _join_generator_dht_shutdown(
    dht: object,
    *,
    timeout: float,
    name: str,
) -> tuple[Optional[_GeneratorDHTShutdownAttempt], dict]:
    attached_result = getattr(
        dht,
        _GENERATOR_DHT_TOMBSTONE_ATTRIBUTE,
        None,
    )
    if (
        isinstance(attached_result, dict)
        and attached_result.get("status") == "stopped"
    ):
        return None, dict(attached_result)

    entry = _generator_dht_shutdown_attempt(dht, name=name)
    if isinstance(entry, _GeneratorDHTShutdownTombstone):
        return None, dict(entry.result)
    attempt = entry
    thread = attempt.thread
    if thread is not None:
        thread.join(max(0.0, timeout))

    with _generator_lifecycle_lock:
        pending = bool(thread is not None and thread.is_alive())
        errors = list(attempt.errors)
        remote_expert_p2p = attempt.remote_expert_p2p
    if pending:
        status = "pending"
    elif errors:
        status = "error"
    else:
        status = "stopped"
    result = {
        "status": status,
        "remote_expert_p2p": remote_expert_p2p,
    }
    if errors:
        result["error"] = "; ".join(errors)
    return attempt, result


def _shutdown_dht_instance(
    dht: object,
    *,
    timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    name: str = "client-dht-shutdown",
) -> dict:
    attempt, result = _join_generator_dht_shutdown(
        dht,
        timeout=timeout,
        name=name,
    )
    if attempt is not None and result["status"] == "stopped":
        _finalize_successful_generator_dht_shutdown(attempt, result)
    return result


def _close_generator_dht(
    dht: object,
    *,
    timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
    name: str = "client-dht-shutdown",
) -> dict:
    global _generator_identity_quarantine
    attempt, result = _join_generator_dht_shutdown(
        dht,
        timeout=timeout,
        name=name,
    )
    if attempt is None:
        return result
    with _generator_lifecycle_lock:
        previously_quarantined = attempt.quarantines_identity
        attempt.quarantines_identity = result["status"] != "stopped"
        any_quarantined = any(
            isinstance(candidate, _GeneratorDHTShutdownAttempt)
            and candidate.quarantines_identity
            for candidate in _generator_dht_shutdown_attempts.values()
        )
        if attempt.quarantines_identity:
            _generator_identity_quarantine = _GENERATOR_DHT_QUARANTINE_REASON
        elif (
            previously_quarantined
            and not any_quarantined
            and _generator_identity_quarantine
            == _GENERATOR_DHT_QUARANTINE_REASON
        ):
            _generator_identity_quarantine = None
    if result["status"] == "stopped":
        _finalize_successful_generator_dht_shutdown(attempt, result)
    return result


def _shutdown_client_dht(
    timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> Optional[dict]:
    global client_dht, _pending_generator_cleanup
    with _generator_lifecycle_lock:
        if client_dht is None:
            return None
        if _pending_generator_cleanup is not None:
            raise RuntimeError(
                "Cannot detach another generator DHT while exact cleanup is pending"
            )
        dht = client_dht
        client_dht = None
    network_supervisor.unregister_role("generator")
    result = _close_generator_dht(dht, timeout=timeout)
    if result["status"] != "stopped":
        with _generator_lifecycle_lock:
            pending = _pending_generator_cleanup
            if pending is None:
                _pending_generator_cleanup = _GeneratorCleanupHandles(
                    candidate_generator=None,
                    sequential=None,
                    dht=dht,
                    reason=(
                        "Detached generator DHT cleanup did not complete within "
                        "its deadline."
                    ),
                )
            elif pending.dht is not dht:
                logger.error(
                    "Could not retain detached generator DHT cleanup because "
                    "another exact cleanup owner is already pending"
                )
    return result


class _GeneratorStartupCancelled(RuntimeError):
    pass


class _NodeStartupCancelled(RuntimeError):
    pass


def _raise_if_generator_start_cancelled(
    transaction: _GeneratorStartTransaction,
    external_cancel: Optional[threading.Event],
) -> None:
    if external_cancel is not None and external_cancel.is_set():
        transaction.cancel_event.set()
    with _generator_lifecycle_lock:
        backend_closing = _backend_closing
    if transaction.cancel_event.is_set():
        raise _GeneratorStartupCancelled("Generator startup was cancelled.")
    if backend_closing or not network_supervisor.accepting_roles:
        transaction.cancel_event.set()
        raise _GeneratorStartupCancelled(
            "Generator startup was cancelled because backend shutdown began."
        )


async def _await_owned_future(future: asyncio.Future) -> int:
    """Wait for owned executor work even if the caller is cancelled again."""
    cancellation_count = 0
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancellation_count += 1
        except BaseException:
            break
    if future.done() and not future.cancelled():
        try:
            future.result()
        except BaseException:
            pass
    return cancellation_count


async def _run_owned_cleanup(target) -> int:
    loop = asyncio.get_running_loop()
    future = asyncio.ensure_future(loop.run_in_executor(None, target))
    return await _await_owned_future(future)


def _begin_node_lifecycle_operation(
    local_node: Node,
    action: str,
) -> tuple[Optional[_NodeLifecycleOperation], Optional[dict]]:
    """Claim one registered node until its method and API state update finish."""
    node_id = str(local_node.node_id)
    with _generator_lifecycle_lock:
        if (
            local_nodes.get(node_id) is not local_node
            and not (node is local_node and getattr(node, "node_id", None) == node_id)
        ):
            return None, {
                "status": "not_found",
                "message": "The selected node was removed before the action began.",
            }
        if _backend_closing or not getattr(
            network_supervisor,
            "accepting_roles",
            True,
        ):
            return None, {
                "status": "error",
                "error": "backend_shutting_down",
                "message": "The backend network lifecycle is shutting down.",
            }
        if action in {"turn_off", "delete"} and _active_generator_start is not None:
            return None, {
                "status": "error",
                "error": "generator_start_in_progress",
                "message": (
                    "A generator startup is validating its selected provider route. "
                    f"Wait for it to finish before {action}."
                ),
            }
        existing = _active_node_operations.get(node_id)
        shutdown = _local_node_shutdown_attempts.get(node_id)
        if existing is not None or shutdown is not None:
            current_action = (
                existing.action if existing is not None else "backend_shutdown"
            )
            return None, {
                "status": "error",
                "error": "node_lifecycle_operation_in_progress",
                "message": (
                    f"Node {node_id} is already running lifecycle action "
                    f"{current_action}. Wait for it to finish before {action}."
                ),
            }
        operation = _NodeLifecycleOperation(local_node, action)
        _active_node_operations[node_id] = operation
        return operation, None


async def _run_node_lifecycle_operation(
    operation: _NodeLifecycleOperation,
    target,
):
    """Run one exact blocking node action and finish it despite caller cancellation."""
    loop = asyncio.get_running_loop()
    future = asyncio.ensure_future(loop.run_in_executor(None, target))
    operation.future = future
    cancellation_count = 0
    try:
        await asyncio.shield(future)
    except asyncio.CancelledError:
        cancellation_count = 1 + await _await_owned_future(future)
    result = future.result()
    return result, cancellation_count


def _finish_node_lifecycle_operation(
    operation: _NodeLifecycleOperation,
) -> None:
    node_id = str(getattr(operation.node, "node_id", ""))
    with _generator_lifecycle_lock:
        if _active_node_operations.get(node_id) is operation:
            _active_node_operations.pop(node_id, None)
    operation.done_event.set()


async def _run_generator_start_executor_work(
    transaction: _GeneratorStartTransaction,
    future_attribute: str,
    target,
    *args,
):
    """Retain and shield one exact executor future owned by startup."""
    if getattr(transaction, future_attribute) is not None:
        raise RuntimeError(
            f"Generator startup executor work {future_attribute!r} already exists"
        )
    loop = asyncio.get_running_loop()
    future = asyncio.ensure_future(
        loop.run_in_executor(None, target, *args)
    )
    setattr(transaction, future_attribute, future)
    return await asyncio.shield(future)


def _call_with_optional_timeout(target, timeout: float):
    """Call a lifecycle method with a shared deadline when it supports one."""
    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "timeout" in parameters:
        return target(timeout=max(0.0, timeout))
    return target()


def _bind_optional_timeout(target, timeout: float):
    """Keep legacy no-timeout bound methods visible to executor test/control hooks."""
    try:
        parameters = inspect.signature(target).parameters
    except (TypeError, ValueError):
        parameters = {}
    if "timeout" not in parameters:
        return target
    return lambda: target(timeout=max(0.0, timeout))


async def _rollback_generator_start(
    transaction: _GeneratorStartTransaction,
) -> bool:
    """Release only resources still owned by one uncommitted startup."""
    global _pending_generator_cleanup
    cancellation_count = 0
    cleanup_complete = True
    for owned_future in (
        transaction.dht_future,
        transaction.route_validation_future,
        transaction.tensor_canary_future,
        transaction.load_future,
    ):
        if owned_future is not None:
            cancellation_count += await _await_owned_future(owned_future)

    # Cancellation can arrive while DHT construction is still in the executor,
    # before the startup coroutine records its result. Recover that exact result
    # after joining so rollback can close the instance it created.
    dht_future = transaction.dht_future
    if transaction.candidate_dht is None and dht_future is not None:
        if dht_future.done() and not dht_future.cancelled():
            try:
                transaction.candidate_dht = dht_future.result()
            except BaseException:
                # Construction failed without yielding a resource to close.
                pass

    sequential = transaction.sequential
    candidate_generator = transaction.candidate_generator
    if sequential is not None:
        stop_health_monitor = getattr(sequential, "stop_health_monitor", None)
        if callable(stop_health_monitor):
            loop = asyncio.get_running_loop()
            monitor_future = asyncio.ensure_future(
                loop.run_in_executor(
                    None,
                    lambda: _call_with_optional_timeout(
                        stop_health_monitor,
                        DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
                    ),
                )
            )
            cancellation_count += await _await_owned_future(monitor_future)
            if monitor_future.cancelled():
                cleanup_complete = False
            else:
                try:
                    cleanup_complete = monitor_future.result() is not False
                except BaseException as exc:
                    logger.warning("Failed to stop candidate health monitor: %s", exc)
                    cleanup_complete = False
        if cleanup_complete:
            transaction.sequential = None
            sequential = None

    if cleanup_complete and candidate_generator is not None:
        loop = asyncio.get_running_loop()
        unload_future = asyncio.ensure_future(
            loop.run_in_executor(
                None,
                lambda: _call_with_optional_timeout(
                    candidate_generator.unload,
                    DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
                ),
            )
        )
        cancellation_count += await _await_owned_future(unload_future)
        if unload_future.cancelled():
            cleanup_complete = False
        else:
            try:
                cleanup_complete = unload_future.result() is not False
            except BaseException as exc:
                logger.warning("Failed to roll back candidate generator: %s", exc)
                cleanup_complete = False
        if cleanup_complete:
            transaction.candidate_generator = None
            candidate_generator = None

    candidate_dht = transaction.candidate_dht
    if cleanup_complete and candidate_dht is not None:
        loop = asyncio.get_running_loop()
        dht_future = asyncio.ensure_future(
            loop.run_in_executor(
                None,
                lambda: _close_generator_dht(
                    candidate_dht,
                    name="candidate-generator-dht-shutdown",
                ),
            )
        )
        cancellation_count += await _await_owned_future(dht_future)
        if dht_future.cancelled():
            cleanup_complete = False
        else:
            try:
                cleanup_complete = dht_future.result().get("status") == "stopped"
            except BaseException as exc:
                logger.warning("Failed to close candidate generator DHT: %s", exc)
                cleanup_complete = False
        if cleanup_complete:
            transaction.candidate_dht = None
            candidate_dht = None

    if transaction.role_registered:
        try:
            network_supervisor.unregister_role("generator")
        except Exception as exc:
            logger.warning("Could not unregister candidate generator role: %s", exc)
        transaction.role_registered = False

    if not cleanup_complete:
        handles = _GeneratorCleanupHandles(
            candidate_generator=candidate_generator,
            sequential=None if candidate_generator is not None else sequential,
            dht=candidate_dht,
            reason="Generator startup rollback did not complete within its deadline.",
        )
        with _generator_lifecycle_lock:
            if _pending_generator_cleanup is not None:
                raise RuntimeError(
                    "Multiple generator cleanup owners would overlap"
                )
            _pending_generator_cleanup = handles
        transaction.candidate_generator = None
        transaction.sequential = None
        transaction.candidate_dht = None

    if cancellation_count:
        raise asyncio.CancelledError
    return cleanup_complete


def _raise_if_node_start_cancelled(
    transaction: _NodeStartTransaction,
    external_cancel: Optional[threading.Event],
) -> None:
    if external_cancel is not None and external_cancel.is_set():
        transaction.cancel_event.set()
    with _generator_lifecycle_lock:
        backend_closing = _backend_closing
    if transaction.cancel_event.is_set():
        raise _NodeStartupCancelled("Node startup was cancelled.")
    if backend_closing or not network_supervisor.accepting_roles:
        transaction.cancel_event.set()
        raise _NodeStartupCancelled(
            "Node startup was cancelled because backend shutdown began."
        )


async def _rollback_node_start(transaction: _NodeStartTransaction) -> bool:
    cancellation_count = 0
    cleanup_complete = True
    start_future = transaction.start_future
    if start_future is not None:
        cancellation_count += await _await_owned_future(start_future)
    candidate = transaction.candidate
    if candidate is not None:
        loop = asyncio.get_running_loop()
        cleanup_future = asyncio.ensure_future(
            loop.run_in_executor(None, lambda: _cleanup_failed_node(candidate))
        )
        cancellation_count += await _await_owned_future(cleanup_future)
        if cleanup_future.cancelled():
            cleanup_complete = False
        else:
            try:
                cleanup_complete = bool(cleanup_future.result())
            except BaseException as exc:
                logger.warning("Failed to clean up candidate node: %s", exc)
                cleanup_complete = False
        if cleanup_complete:
            transaction.candidate = None
        else:
            _register_local_node(candidate)
            try:
                _runtime_state.record_event(
                    kind="node",
                    phase="cleanup_pending",
                    status="error",
                    message=(
                        f"Node {getattr(candidate, 'node_id', 'unknown')} retained "
                        "runtime handles after bounded startup rollback."
                    ),
                    operation_id=transaction.operation_id,
                )
            except Exception as exc:
                logger.warning("Could not record pending node cleanup: %s", exc)
    if transaction.role_registered:
        node_id = getattr(candidate, "node_id", None)
        if node_id is not None:
            network_supervisor.unregister_role("worker", role_id=node_id)
        transaction.role_registered = False
    if cancellation_count:
        raise asyncio.CancelledError
    return cleanup_complete


async def _cancel_discovery_refresh_tasks() -> None:
    """Cancel legacy request tasks before network resources are torn down."""
    global _nodes_refresh_task
    with _serving_plan_cache_lock:
        tasks = [
            task
            for task in [
                _nodes_refresh_task,
                *_serving_plan_refresh_tasks.values(),
            ]
            if task is not None and not task.done()
        ]
        _nodes_refresh_task = None
        _serving_plan_refresh_tasks.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gpu_monitor, _backend_closing
    _validate_environment()
    with _generator_lifecycle_lock:
        if (
            _active_generator_start is not None
            or _active_node_starts
            or _active_node_operations
            or _local_node_shutdown_attempts
            or _generator_cleanup_in_progress
            or _pending_generator_cleanup is not None
            or generator is not None
            or client_dht is not None
            or _local_node_list()
        ):
            raise RuntimeError(
                "Cannot start the backend while runtime cleanup is still active"
            )
        _backend_closing = False
    reopen_admission = getattr(_lifecycle_jobs, "reopen_admission", None)
    if callable(reopen_admission):
        reopen_admission()
    network_supervisor.start()
    try:
        gpu_monitor = GPUMonitor(interval=2.0)
        gpu_monitor.start()
        logger.info("GPU monitor started.")
        yield
    finally:
        logger.info("Shutting down...")
        shutdown_deadline = time.monotonic() + DEFAULT_SHUTDOWN_TIMEOUT_SECONDS
        shutdown_failures: list[str] = []

        def record_shutdown_failure(stage: str, exc: BaseException) -> dict:
            message = f"{stage} failed during backend shutdown: {exc}"
            shutdown_failures.append(message)
            logger.error(message, exc_info=True)
            return {"status": "error", "error": str(exc)}

        with _generator_lifecycle_lock:
            _backend_closing = True
            active_generator_start = _active_generator_start
            if active_generator_start is not None:
                active_generator_start.cancel_event.set()
            active_node_starts = list(_active_node_starts.values())
            for active_node_start in active_node_starts:
                active_node_start.cancel_event.set()
            active_node_operations = list(_active_node_operations.values())

        try:
            network_supervisor.begin_shutdown()
        except BaseException as exc:
            record_shutdown_failure("Network admission closure", exc)

        try:
            await _cancel_discovery_refresh_tasks()
        except BaseException as exc:
            record_shutdown_failure("Discovery refresh cancellation", exc)

        lifecycle_jobs_stopped = False
        try:
            cancel_and_wait = getattr(_lifecycle_jobs, "cancel_all_and_wait", None)
            lifecycle_jobs_stopped = (
                cancel_and_wait(max(0.0, shutdown_deadline - time.monotonic()))
                if callable(cancel_and_wait)
                else (_lifecycle_jobs.cancel_all() or True)
            )
        except BaseException as exc:
            record_shutdown_failure("Lifecycle job cleanup", exc)

        generator_start_stopped = bool(
            active_generator_start is None
            or active_generator_start.done_event.wait(
                max(0.0, shutdown_deadline - time.monotonic())
            )
        )
        node_starts_stopped = True
        for active_node_start in active_node_starts:
            if not active_node_start.done_event.wait(
                max(0.0, shutdown_deadline - time.monotonic())
            ):
                node_starts_stopped = False

        node_operations_stopped = True
        for active_node_operation in active_node_operations:
            if not active_node_operation.done_event.wait(
                max(0.0, shutdown_deadline - time.monotonic())
            ):
                node_operations_stopped = False

        try:
            generator_shutdown_result = await _unload_generator_runtime(
                "Backend shutdown began.",
                timeout=max(0.0, shutdown_deadline - time.monotonic()),
            )
        except BaseException as exc:
            generator_shutdown_result = record_shutdown_failure(
                "Generator cleanup",
                exc,
            )

        try:
            node_shutdown_results = _shutdown_local_nodes(
                max(0.0, shutdown_deadline - time.monotonic())
            )
        except BaseException as exc:
            node_shutdown_results = [record_shutdown_failure("Local node cleanup", exc)]

        try:
            placement_shutdown_result = placement_runtime.shutdown(
                max(0.0, shutdown_deadline - time.monotonic())
            )
            if not placement_shutdown_result:
                record_shutdown_failure(
                    "Placement heartbeat cleanup",
                    RuntimeError("Placement heartbeat did not stop before the deadline"),
                )
        except BaseException as exc:
            placement_shutdown_result = record_shutdown_failure(
                "Placement heartbeat cleanup",
                exc,
            )

        try:
            supervisor_shutdown_result = network_supervisor.stop(
                max(0.0, shutdown_deadline - time.monotonic())
            )
        except BaseException as exc:
            supervisor_shutdown_result = record_shutdown_failure(
                "Network supervisor cleanup",
                exc,
            )

        if gpu_monitor is not None:
            try:
                gpu_monitor.stop()
            except BaseException as exc:
                record_shutdown_failure("GPU monitor cleanup", exc)

        with _generator_lifecycle_lock:
            current_generator_start = _active_generator_start
            cleanup_in_progress = _generator_cleanup_in_progress
            cleanup_handles = _pending_generator_cleanup
            live_generator = generator
            live_client_dht = client_dht
            identity_quarantine = _generator_identity_quarantine

        tracked_generator = None
        if cleanup_handles is not None:
            tracked_generator = cleanup_handles.candidate_generator
        if tracked_generator is None:
            tracked_generator = live_generator
        if tracked_generator is None and current_generator_start is not None:
            tracked_generator = current_generator_start.candidate_generator

        previous_generator = _runtime_state.generator_snapshot()
        tracked_model_name = getattr(
            tracked_generator,
            "model_name",
            previous_generator.get("model_name"),
        )
        components_loaded = bool(previous_generator.get("components_loaded"))
        if tracked_generator is not None:
            is_loaded = getattr(tracked_generator, "is_loaded", None)
            try:
                components_loaded = (
                    bool(is_loaded()) if callable(is_loaded) else components_loaded
                )
            except BaseException as exc:
                record_shutdown_failure("Generator loaded-state inspection", exc)
                components_loaded = True

        generator_cleanup_unresolved = bool(
            current_generator_start is not None
            or cleanup_in_progress
            or cleanup_handles is not None
            or live_generator is not None
            or live_client_dht is not None
            or identity_quarantine is not None
        )
        generator_cleanup_failed = (
            isinstance(generator_shutdown_result, dict)
            and generator_shutdown_result.get("status") == "error"
        )
        if generator_cleanup_unresolved:
            unresolved_reasons = [
                "Backend shutdown still owns an exact generator startup or cleanup "
                "handle; cleanup must finish before the runtime can be called stopped."
            ]
            unresolved_reasons.extend(shutdown_failures)
            _runtime_state.transition_generator(
                "stopping",
                model_name=tracked_model_name,
                components_loaded=components_loaded,
                route_ready=False,
                reasons=unresolved_reasons,
            )
        elif generator_cleanup_failed:
            _runtime_state.transition_generator(
                "failed",
                model_name=tracked_model_name,
                components_loaded=False,
                route_ready=False,
                reasons=shutdown_failures
                or ["Generator cleanup failed during backend shutdown."],
            )
        else:
            _runtime_state.transition_generator(
                "stopped",
                model_name=None,
                components_loaded=False,
                route_ready=False,
                reasons=["Backend shutdown completed."],
            )
        logger.info(
            "Shutdown cleanup status | lifecycle_jobs=%s generator_start=%s "
            "node_starts=%s node_operations=%s generator=%s local_nodes=%s "
            "placement=%s supervisor=%s failures=%s",
            lifecycle_jobs_stopped,
            generator_start_stopped,
            node_starts_stopped,
            node_operations_stopped,
            generator_shutdown_result,
            node_shutdown_results,
            placement_shutdown_result,
            supervisor_shutdown_result,
            shutdown_failures,
        )
        if generator_cleanup_unresolved or shutdown_failures:
            logger.warning(
                "Shutdown finished with unresolved or failed cleanup; retained "
                "state remains visible in runtime diagnostics."
            )
        else:
            logger.info("Shutdown complete.")


app = FastAPI(
    title="DistribLLM API",
    version="0.1.0",
    lifespan=lifespan,
)


def _configured_browser_origins() -> tuple[str, ...]:
    raw = os.environ.get(
        "DISTRIBLLM_ALLOWED_BROWSER_ORIGINS",
        "distribllm://app,http://localhost:5173,http://127.0.0.1:5173",
    )
    origins = tuple(
        dict.fromkeys(value.strip() for value in raw.split(",") if value.strip())
    )
    if not origins:
        raise ValueError("DISTRIBLLM_ALLOWED_BROWSER_ORIGINS must not be empty")
    for origin in origins:
        if origin == "distribllm://app":
            continue
        parsed = urlparse(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "DISTRIBLLM_ALLOWED_BROWSER_ORIGINS must contain only the exact "
                "packaged distribllm://app origin or exact loopback HTTP(S) origins"
            )
    return origins


ALLOWED_BROWSER_ORIGINS = _configured_browser_origins()


def _browser_origin_is_allowed(origin: str | None) -> bool:
    return origin is None or origin in ALLOWED_BROWSER_ORIGINS


@app.middleware("http")
async def reject_untrusted_browser_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if not _browser_origin_is_allowed(origin):
        return JSONResponse(
            status_code=403,
            content={
                "detail": {
                    "error": "untrusted_browser_origin",
                    "message": "The browser origin is not allowed to manage this backend.",
                }
            },
        )
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_BROWSER_ORIGINS),
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class NodeStartRequest(BaseModel):
    model_name:    str
    layer_start:   int
    layer_end:     int
    dht_prefix:    str = DHT_PREFIX
    initial_peers: list[str] = []
    device:        str = "cuda" if torch.cuda.is_available() else "cpu"
    coverage_revision: Optional[str] = None
    placement_mode: Literal["recommended", "custom"] = "custom"
    layer_capacity: Optional[int] = None
    placement_revision: Optional[int] = None
    placement_idempotency_key: Optional[str] = None
    confirm_redundancy: bool = False
    confirm_local_replica: bool = False

    @field_validator("model_name")
    @classmethod
    def model_must_be_supported(cls, v: str) -> str:
        v = v.strip()
        if v not in SUPPORTED_MODELS:
            raise ValueError(
                f"Unsupported model '{v}'. "
                f"Supported: {list(SUPPORTED_MODELS.keys())}"
            )
        return v

    @field_validator("layer_end")
    @classmethod
    def layer_range_valid(cls, v: int, info) -> int:
        start = info.data.get("layer_start", 0)
        if v <= start:
            raise ValueError(f"layer_end ({v}) must be > layer_start ({start})")
        model = info.data.get("model_name", "")
        if model in SUPPORTED_MODELS:
            max_layers = SUPPORTED_MODELS[model]["num_layers"]
            if v > max_layers:
                raise ValueError(f"layer_end ({v}) exceeds model depth ({max_layers})")
        return v

    @field_validator("device")
    @classmethod
    def device_valid(cls, v: str) -> str:
        if v not in ("cuda", "cpu"):
            raise ValueError(f"device must be 'cuda' or 'cpu', got '{v}'")
        if v == "cuda" and not torch.cuda.is_available():
            return "cpu"
        return v


class GeneratorStartRequest(BaseModel):
    model_name:    str
    dht_prefix:    str = DHT_PREFIX
    initial_peers: list[str] = []

    @field_validator("model_name")
    @classmethod
    def model_must_be_supported(cls, v: str) -> str:
        v = v.strip()
        if v not in SUPPORTED_MODELS:
            raise ValueError(
                f"Unsupported model '{v}'. Supported: {list(SUPPORTED_MODELS.keys())}"
            )
        return v


class ChatRequest(BaseModel):
    message:        str
    max_new_tokens: Optional[int]   = None
    temperature:    Optional[float] = None
    top_p:          Optional[float] = None
    top_k:          Optional[int]   = None
    repetition_penalty: Optional[float] = None
    do_sample:      Optional[bool]  = None

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty")
        return v

    @field_validator("max_new_tokens")
    @classmethod
    def max_tokens_valid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (1 <= v <= 2048):
            raise ValueError("max_new_tokens must be between 1 and 2048")
        return v

    @field_validator("temperature")
    @classmethod
    def temperature_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 < v <= 2.0):
            raise ValueError("temperature must be between 0 and 2")
        return v

    @field_validator("top_p")
    @classmethod
    def top_p_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.0 < v <= 1.0):
            raise ValueError("top_p must be between 0 and 1")
        return v

    @field_validator("top_k")
    @classmethod
    def top_k_valid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (0 <= v <= 50000):
            raise ValueError("top_k must be between 0 and 50000")
        return v

    @field_validator("repetition_penalty")
    @classmethod
    def repetition_penalty_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not (0.1 <= v <= 5.0):
            raise ValueError("repetition_penalty must be between 0.1 and 5")
        return v


class ApiKeyCreateRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_valid(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 80:
            raise ValueError("name must contain 1 to 80 characters")
        return value


class OpenAIChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def role_valid(cls, value: str) -> str:
        if value not in {"system", "user", "assistant"}:
            raise ValueError("role must be system, user, or assistant")
        return value

    @field_validator("content")
    @classmethod
    def content_valid(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


class OpenAIChatCompletionRequest(BaseModel):
    model: str
    messages: list[OpenAIChatMessage]
    max_tokens: int = 64
    temperature: float = 0.7
    top_p: float = 0.9
    stream: bool = False

    @field_validator("model")
    @classmethod
    def api_model_supported(cls, value: str) -> str:
        if value not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported model '{value}'")
        return value

    @field_validator("messages")
    @classmethod
    def messages_present(cls, value: list[OpenAIChatMessage]) -> list[OpenAIChatMessage]:
        if not value:
            raise ValueError("messages must not be empty")
        return value

    @field_validator("max_tokens")
    @classmethod
    def max_tokens_valid(cls, value: int) -> int:
        if not 1 <= value <= 2048:
            raise ValueError("max_tokens must be between 1 and 2048")
        return value

    @field_validator("temperature")
    @classmethod
    def temperature_valid(cls, value: float) -> float:
        if not 0.0 < value <= 2.0:
            raise ValueError("temperature must be greater than 0 and at most 2")
        return value

    @field_validator("top_p")
    @classmethod
    def top_p_valid(cls, value: float) -> float:
        if not 0.0 < value <= 1.0:
            raise ValueError("top_p must be greater than 0 and at most 1")
        return value

class NextTokenParityRequest(BaseModel):
    prompt: str
    atol: float = 1e-4
    rtol: float = 1e-4

    @field_validator("prompt")
    @classmethod
    def prompt_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("prompt must not be empty")
        return v

    @field_validator("atol", "rtol")
    @classmethod
    def tolerance_valid(cls, v: float) -> float:
        if v < 0:
            raise ValueError("tolerances must be non-negative")
        return v


class GenerationTraceRequest(BaseModel):
    prompt: str
    max_new_tokens: Optional[int] = 32
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    repetition_penalty: Optional[float] = None
    do_sample: Optional[bool] = None

    @field_validator("prompt")
    @classmethod
    def prompt_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("prompt must not be empty")
        return v

    @field_validator("max_new_tokens")
    @classmethod
    def max_tokens_valid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None:
            if not (1 <= v <= 256):
                raise ValueError("max_new_tokens must be between 1 and 256")
        return v

    @field_validator("temperature")
    @classmethod
    def temperature_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            if not (0.0 < v <= 2.0):
                raise ValueError("temperature must be between 0 and 2")
        return v

    @field_validator("top_p")
    @classmethod
    def top_p_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            if not (0.0 < v <= 1.0):
                raise ValueError("top_p must be between 0 and 1")
        return v

    @field_validator("top_k")
    @classmethod
    def top_k_valid(cls, v: Optional[int]) -> Optional[int]:
        if v is not None:
            if not (0 <= v <= 50000):
                raise ValueError("top_k must be between 0 and 50000")
        return v

    @field_validator("repetition_penalty")
    @classmethod
    def repetition_penalty_valid(cls, v: Optional[float]) -> Optional[float]:
        if v is not None:
            if not (0.1 <= v <= 5.0):
                raise ValueError("repetition_penalty must be between 0.1 and 5")
        return v


class GeneratedParityRequest(GenerationTraceRequest):
    max_new_tokens: Optional[int] = 16
    do_sample: Optional[bool] = False


class TokenRequest(BaseModel):
    token: str

    @field_validator("token")
    @classmethod
    def token_valid(cls, v: str) -> str:
        v = v.strip()
        if not v or not v.startswith("hf_"):
            raise ValueError("Token must start with 'hf_'")
        return v


class TokenValidationRequest(BaseModel):
    model_name: str
    token: Optional[str] = None

    @field_validator("model_name")
    @classmethod
    def model_must_be_supported(cls, v: str) -> str:
        v = v.strip()
        if v not in SUPPORTED_MODELS:
            raise ValueError(
                f"Unsupported model '{v}'. Supported: {list(SUPPORTED_MODELS.keys())}"
            )
        return v

    @field_validator("token")
    @classmethod
    def token_format_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not v.startswith("hf_"):
            raise ValueError("Token must start with 'hf_'")
        return v


class LocalModelRequest(BaseModel):
    model_name: str
    path: str

    @field_validator("model_name")
    @classmethod
    def model_must_be_supported(cls, v: str) -> str:
        v = v.strip()
        if v not in SUPPORTED_MODELS:
            raise ValueError(
                f"Unsupported model '{v}'. Supported: {list(SUPPORTED_MODELS.keys())}"
            )
        return v

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("path must not be empty")
        return v


class HuggingFaceDevicePollRequest(BaseModel):
    flow_id: str

    @field_validator("flow_id")
    @classmethod
    def flow_id_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("flow_id must not be empty")
        return v


class HuggingFaceDownloadRequest(BaseModel):
    model_name: str
    revision: Optional[str] = None

    @field_validator("model_name")
    @classmethod
    def model_must_be_supported(cls, v: str) -> str:
        v = v.strip()
        if v not in SUPPORTED_MODELS:
            raise ValueError(
                f"Unsupported model '{v}'. Supported: {list(SUPPORTED_MODELS.keys())}"
            )
        return v

    @field_validator("revision")
    @classmethod
    def revision_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        return v or None


# ---------------------------------------------------------------------------
# Status & stats
# ---------------------------------------------------------------------------

@app.get("/status")
async def get_status() -> dict:
    local_infos = _local_node_infos()
    generator_status = await get_generator_status()
    return {
        "status":          "online",
        "node_running":    any(local_node.is_running() for local_node in _local_node_list()),
        "node_info":       local_infos[0] if local_infos else None,
        "node_infos":      local_infos,
        "local_node_ids":  [info["node_id"] for info in local_infos if info.get("node_id")],
        "gpu_available":   torch.cuda.is_available(),
        "generator_ready": generator_status["ready"],
        "generator_state": generator_status["state"],
        "generator_components_loaded": generator_status["components_loaded"],
        "route_ready": generator_status["route_ready"],
        "generator_reasons": generator_status["reasons"],
        "token_set":       token_is_set(),
        "local_models":    list_local_models(),
        "network":         _network_snapshot(),
    }


@app.get("/stats")
async def get_stats() -> dict:
    if gpu_monitor is None:
        raise HTTPException(status_code=503, detail="GPU monitor not ready.")
    return gpu_monitor.get_stats()


@app.get("/nodes")
async def get_nodes() -> dict:
    global _nodes_refresh_task
    if _supervisor_active():
        return _get_nodes_sync()
    now = time.monotonic()
    with _serving_plan_cache_lock:
        cached = _nodes_cache
        refresh_task = _nodes_refresh_task
    if cached is not None and now - cached[0] <= SERVING_PLAN_CACHE_SECONDS:
        return {**cached[1], "snapshot_stale": False, "refreshing": False}
    if refresh_task is None or refresh_task.done():
        refresh_task = asyncio.create_task(_refresh_nodes_cache())
        with _serving_plan_cache_lock:
            _nodes_refresh_task = refresh_task
    if cached is not None:
        return {**cached[1], "snapshot_stale": True, "refreshing": True}
    return {
        "nodes": _local_node_infos(),
        "warning": "Network discovery is refreshing; showing local nodes first.",
        "snapshot_stale": True,
        "refreshing": True,
    }


async def _refresh_nodes_cache() -> None:
    global _nodes_cache, _nodes_refresh_task
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_get_nodes_sync), timeout=30.0)
        with _serving_plan_cache_lock:
            _nodes_cache = (time.monotonic(), result)
    except Exception as exc:
        logger.warning("Background node discovery failed: %s", exc)
        _runtime_state.record_event(
            kind="monitoring",
            phase="node_discovery",
            status="error",
            message=f"Node discovery refresh failed: {exc}",
        )
    finally:
        with _serving_plan_cache_lock:
            if _nodes_refresh_task is asyncio.current_task():
                _nodes_refresh_task = None


def _get_nodes_sync() -> dict:
    supervisor_snapshot = _network_snapshot()
    if _supervisor_active(supervisor_snapshot):
        discovered_nodes = _merge_supervisor_and_local_nodes(
            list(supervisor_snapshot.get("nodes", []))
        )
        result = {
            "nodes": discovered_nodes,
            **_network_response_fields(supervisor_snapshot),
        }
        if supervisor_snapshot.get("state") != "ready":
            result["warning"] = (
                "Network discovery is not current; the last-good and local "
                "provider records are shown."
            )
        return result

    dht = _active_local_dht() or client_dht
    if dht is None:
        local_infos = _local_node_infos()
        if local_infos:
            return {
                "nodes": local_infos,
                "warning": "No active DHT connection; showing local loaded node only.",
            }
        return {"nodes": [], "warning": "No DHT connection yet."}
    try:
        seq = RemoteSequential(
            dht=dht,
            dht_prefix=_active_dht_prefix(),
            num_layers=0,
            model_name=None,
        )
        status = seq.get_network_status()
        discovered_nodes = status["nodes"]
        discovered_keys = {_local_node_key(info) for info in discovered_nodes}
        local_only_nodes = [
            info for info in _local_node_infos() if _local_node_key(info) not in discovered_keys
        ]
        return {"nodes": [*discovered_nodes, *local_only_nodes]}
    except Exception as e:
        logger.error(f"Node discovery failed: {e}", exc_info=True)
        return {"nodes": [], "error": str(e)}


@app.get("/nodes/local")
async def get_local_nodes() -> dict:
    """Return only nodes owned by this backend process for lifecycle controls."""
    return {"nodes": _local_node_infos()}


@app.get("/models")
async def get_models() -> dict:
    return await asyncio.to_thread(_get_models_sync)


def _model_availability(
    *,
    model_id: str,
    model_info: dict,
    local_imports: dict[str, dict],
    route_status: dict,
    local_infos: list[dict],
    discovery_state: str,
) -> dict:
    """Build one explicit, renderer-safe model availability contract.

    Availability is deliberately not inferred from the legacy ``available``
    boolean.  Local model access, local serving, discovery, and validated route
    readiness are independent facts and remain visible even when the summary
    state changes.
    """
    local_imported = model_id in local_imports
    local_access = not bool(model_info["gated"]) or local_imported
    local_serving = any(
        info.get("model_name") == model_id
        and bool(info.get("running"))
        and bool(info.get("layers_loaded"))
        and bool(info.get("rpc_running"))
        for info in local_infos
    )
    provider_count = int(route_status.get("compatible_nodes", 0))
    covered_layers = int(route_status.get("covered_layers", 0))
    route_ready = bool(route_status.get("route_ready"))
    remotely_discoverable = provider_count > 0 or covered_layers > 0

    if not local_access:
        state = "gated"
        reason = "Approved model files must be imported or downloaded on this device."
        action = "Open model access and import the approved local files."
    elif route_ready:
        state = "remotely_runnable"
        reason = "A complete compatible provider route has been validated."
        action = "Start the inference client."
    elif discovery_state in {"syncing", "not_started"}:
        state = "route_validating"
        reason = "Provider discovery has not produced an authoritative route yet."
        action = "Wait for discovery to finish, then refresh model availability."
    elif remotely_discoverable:
        state = "remotely_discoverable"
        reason = "Compatible providers are visible, but their layers do not form a complete route."
        action = "Add providers for the missing layers or wait for coverage to change."
    elif local_serving or local_imported:
        state = "local_available"
        reason = (
            "This device is serving part of the model, but no complete route is ready."
            if local_serving
            else "Approved model files are available locally, but no provider route is ready."
        )
        action = "Serve missing layers or wait for remote providers."
    else:
        state = "unavailable"
        reason = "No compatible provider route is currently visible."
        action = "Start a provider or check DHT connectivity."

    return {
        "schema_version": 1,
        "state": state,
        "local_access": local_access,
        "local_imported": local_imported,
        "local_serving": local_serving,
        "remote_discovery": discovery_state,
        "remotely_discoverable": remotely_discoverable,
        "provider_count": provider_count,
        "covered_layers": covered_layers,
        "route_ready": route_ready,
        "can_generate_remotely": local_access and route_ready,
        "reason": reason,
        "action": action,
    }


def _local_availability_infos() -> list[dict]:
    """Read local role facts without making the model catalog lifecycle-critical."""
    infos: list[dict] = []
    for local_node in _local_node_list():
        getter = getattr(local_node, "get_info", None)
        if not callable(getter):
            continue
        try:
            infos.append(getter())
        except Exception as exc:
            logger.warning(
                "Could not include local node %s in model availability: %s",
                getattr(local_node, "node_id", "unknown"),
                exc,
            )
    return infos


@app.get("/models/catalog")
async def get_model_catalog() -> dict:
    """Return local model choices without waiting for any DHT route scan."""
    token_available = token_is_set()
    local_imports = {
        record["model_name"]: record
        for record in list_local_models()
        if record.get("model_name")
    }
    local_infos = _local_availability_infos()
    models = []
    for model_id, info in SUPPORTED_MODELS.items():
        total_layers = int(info["num_layers"])
        route_status = {
            "route_ready": False,
            "compatible_nodes": 0,
            "covered_layers": 0,
        }
        models.append(
            {
                "id": model_id,
                "num_layers": total_layers,
                "hidden_size": info["hidden_size"],
                "gated": info["gated"],
                "tuning": info.get("tuning", "base"),
                "description": info["description"],
                "vram_gb": info["vram_gb"],
                "available": not info["gated"] or model_id in local_imports,
                "local_imported": model_id in local_imports,
                "local_import": local_imports.get(model_id),
                "runnable": False,
                "route_ready": False,
                "route_reasons": ["Route status loads separately."],
                "covered_layers": 0,
                "missing_layers": list(range(total_layers)),
                "total_layers": total_layers,
                "compatible_nodes": 0,
                "route_trace": [],
                "availability": _model_availability(
                    model_id=model_id,
                    model_info=info,
                    local_imports=local_imports,
                    route_status=route_status,
                    local_infos=local_infos,
                    discovery_state="not_started",
                ),
            }
        )
    return {
        "models": models,
        "token_available": token_available,
        "default_peers": get_initial_peers(),
    }


def _get_models_sync() -> dict:
    """
    Return the validated list of supported models.
    Frontend uses this to build the model dropdown.
    Also returns whether each model needs a token, so the
    frontend can show the HuggingFace redirect prompt.
    """
    token_available = token_is_set()
    local_imports = {
        record["model_name"]: record
        for record in list_local_models()
        if record.get("model_name")
    }
    supervisor_snapshot = _network_snapshot()
    supervisor_nodes = (
        _merge_supervisor_and_local_nodes(
            list(supervisor_snapshot.get("nodes", []))
        )
        if _supervisor_active(supervisor_snapshot)
        else None
    )
    dht = None if supervisor_nodes is not None else (_active_local_dht() or client_dht)
    active_prefix = _active_dht_prefix()
    local_infos = _local_availability_infos()
    discovery_state = str(supervisor_snapshot.get("state", "not_started"))
    if not _supervisor_active(supervisor_snapshot):
        discovery_state = "ready" if dht is not None else "not_started"
    models = []
    for model_id, info in SUPPORTED_MODELS.items():
        route_status = _get_model_route_status(
            model_id=model_id,
            model_info=info,
            dht=dht,
            dht_prefix=active_prefix,
            nodes=(
                [node for node in supervisor_nodes if node.get("model_name") == model_id]
                if supervisor_nodes is not None
                else None
            ),
        )
        models.append({
            "id":           model_id,
            "num_layers":   info["num_layers"],
            "hidden_size":  info["hidden_size"],
            "gated":        info["gated"],
            "tuning":       info.get("tuning", "base"),
            "description":  info["description"],
            "vram_gb":      info["vram_gb"],
            # Can this model be used right now?
            "available":    not info["gated"] or model_id in local_imports,
            "local_imported": model_id in local_imports,
            "local_import": local_imports.get(model_id),
            "runnable":     route_status["runnable"],
            "route_ready":  route_status["route_ready"],
            "route_reasons": route_status["reasons"],
            "covered_layers": route_status["covered_layers"],
            "missing_layers": route_status["missing_layers"],
            "total_layers": route_status["total_layers"],
            "compatible_nodes": route_status["compatible_nodes"],
            "route_trace": route_status["route_trace"],
            "availability": _model_availability(
                model_id=model_id,
                model_info=info,
                local_imports=local_imports,
                route_status=route_status,
                local_infos=local_infos,
                discovery_state=discovery_state,
            ),
        })
    result = {
        "models":          models,
        "token_available": token_available,
        "default_peers":   get_initial_peers(),
    }
    if supervisor_nodes is not None:
        result["network"] = supervisor_snapshot
    return result


def _active_serving_nodes(
    model_id: str,
    dht_prefix: Optional[str] = None,
    *,
    supervisor_snapshot: Optional[dict] = None,
) -> list[dict]:
    model_info = SUPPORTED_MODELS[model_id]
    total_layers = int(model_info["num_layers"])
    active_prefix = dht_prefix or _active_dht_prefix()
    topology_snapshot = (
        supervisor_snapshot
        if supervisor_snapshot is not None
        else _network_snapshot()
    )
    use_supervisor = _supervisor_active(topology_snapshot)
    dht = None if use_supervisor else (_active_local_dht() or client_dht)
    discovered: list[dict] = []
    sequential = RemoteSequential(
        dht=dht if dht is not None else object(),
        dht_prefix=active_prefix,
        num_layers=total_layers,
        model_name=model_id,
        **(
            {"placement_model_revision": placement_runtime.config.model_revision}
            if placement_runtime.enabled
            else {}
        ),
    )
    if use_supervisor:
        discovered = _merge_supervisor_and_local_nodes(
            list(topology_snapshot.get("nodes", [])),
            model_name=model_id,
        )
    elif dht is not None:
        try:
            discovered = sequential.get_network_status()["nodes"]
        except Exception as exc:
            logger.warning("Coverage discovery failed for %s: %s", model_id, exc)

    candidates = (
        discovered
        if use_supervisor
        else _merge_supervisor_and_local_nodes(
            discovered,
            model_name=model_id,
        )
    )
    valid: list[dict] = []
    for candidate in candidates:
        try:
            node_info = sequential._validate_node_metadata(
                candidate,
                str(candidate.get("peer_id", "unknown")),
            )
        except ValueError as exc:
            logger.warning("Ignoring invalid coverage metadata: %s", exc)
            continue
        if sequential._is_serving_node(node_info):
            valid.append(node_info)
    return valid


def _build_model_serving_plan(
    model_id: str,
    layer_count: int,
    dht_prefix: Optional[str] = None,
    serving_nodes: Optional[list[dict]] = None,
) -> dict:
    if model_id not in SUPPORTED_MODELS:
        raise HTTPException(status_code=404, detail="Unsupported model")
    total_layers = int(SUPPORTED_MODELS[model_id]["num_layers"])
    if not 1 <= layer_count <= total_layers:
        raise HTTPException(
            status_code=422,
            detail=f"layer_count must be between 1 and {total_layers}",
        )
    nodes = (
        serving_nodes
        if serving_nodes is not None
        else _active_serving_nodes(model_id, dht_prefix)
    )
    return {
        "model_id": model_id,
        **build_serving_plan(nodes, total_layers, layer_count),
    }


@app.get("/models/{model_id:path}/serving-plan")
async def get_model_serving_plan(
    model_id: str,
    layer_count: int = Query(..., ge=1),
) -> dict:
    if placement_runtime.enabled:
        try:
            placement = await asyncio.to_thread(
                placement_runtime.plan,
                model_id,
                layer_count,
            )
        except PlacementClientError as exc:
            raise _placement_http_error(exc) from exc
        return _coordinator_serving_plan(model_id, placement)
    key = (model_id, layer_count)
    now = time.monotonic()
    supervisor_snapshot = _network_snapshot()
    if _supervisor_active(supervisor_snapshot):
        serving_nodes = _active_serving_nodes(
            model_id,
            supervisor_snapshot=supervisor_snapshot,
        )
        plan = _build_model_serving_plan(
            model_id,
            layer_count,
            serving_nodes=serving_nodes,
        )
        with _serving_plan_cache_lock:
            _serving_plan_cache[key] = (now, plan)
        return {
            **plan,
            **_network_response_fields(supervisor_snapshot),
        }
    with _serving_plan_cache_lock:
        cached = _serving_plan_cache.get(key)
        refresh_task = _serving_plan_refresh_tasks.get(key)
    if cached is not None and now - cached[0] <= SERVING_PLAN_CACHE_SECONDS:
        return {
            **cached[1],
            "snapshot_stale": False,
            "refreshing": False,
            "snapshot_source": "dht_cache",
            "snapshot_age_seconds": max(0.0, now - cached[0]),
        }

    if refresh_task is None or refresh_task.done():
        refresh_task = asyncio.create_task(_refresh_serving_plan(key))
        with _serving_plan_cache_lock:
            _serving_plan_refresh_tasks[key] = refresh_task

    if cached is not None:
        return {
            **cached[1],
            "snapshot_stale": True,
            "refreshing": True,
            "snapshot_source": "dht_cache",
            "snapshot_age_seconds": max(0.0, now - cached[0]),
        }

    local_infos = [
        info
        for info in _local_node_infos()
        if info.get("model_name") == model_id and info.get("running")
    ]
    immediate = _build_model_serving_plan(
        model_id,
        layer_count,
        serving_nodes=local_infos,
    )
    return {
        **immediate,
        "snapshot_stale": True,
        "refreshing": True,
        "snapshot_source": "local_only",
        "snapshot_age_seconds": 0.0,
    }


@app.get("/placement/status")
async def get_placement_status() -> dict:
    """Passive diagnostics; this endpoint never mutates coordinator state."""
    return placement_runtime.status()


async def _refresh_serving_plan(key: tuple[str, int]) -> None:
    model_id, layer_count = key
    try:
        plan = await asyncio.wait_for(
            asyncio.to_thread(_build_model_serving_plan, model_id, layer_count),
            timeout=30.0,
        )
        with _serving_plan_cache_lock:
            previous = _serving_plan_cache.get(key)
            _serving_plan_cache[key] = (time.monotonic(), plan)
        if previous is None or previous[1].get("coverage_revision") != plan.get("coverage_revision"):
            _runtime_state.record_event(
                kind="coverage",
                phase="refresh",
                status="success",
                message=f"Fresh serving plan is available for {model_id}.",
                details={
                    "model_id": model_id,
                    "layer_count": layer_count,
                    "coverage_revision": plan.get("coverage_revision"),
                    "current_runnable": plan.get("current_runnable"),
                    "selected_route": [
                        {
                            "peer_id": item.get("peer_id"),
                            "layer_start": item.get("layer_start"),
                            "layer_end": item.get("layer_end"),
                        }
                        for item in plan.get("selected_route", [])
                    ],
                    "recommendation": plan.get("recommendation"),
                },
            )
    except Exception as exc:
        logger.warning(
            "Background serving-plan refresh failed for %s: %s",
            model_id,
            exc,
        )
        _runtime_state.record_event(
            kind="coverage",
            phase="refresh",
            status="error",
            message=f"Serving-plan refresh failed for {model_id}: {exc}",
        )
    finally:
        with _serving_plan_cache_lock:
            current = _serving_plan_refresh_tasks.get(key)
            if current is asyncio.current_task():
                _serving_plan_refresh_tasks.pop(key, None)


def _get_model_route_status(
    model_id: str,
    model_info: dict,
    dht: Optional[hivemind.DHT],
    dht_prefix: str,
    nodes: Optional[list[dict]] = None,
) -> dict:
    total_layers = int(model_info["num_layers"])
    empty_status = {
        "runnable": False,
        "route_ready": False,
        "reasons": [],
        "covered_layers": 0,
        "missing_layers": list(range(total_layers)),
        "total_layers": total_layers,
        "compatible_nodes": 0,
        "route_trace": [],
    }

    if dht is None and nodes is None:
        return {
            **empty_status,
            "reasons": ["No DHT connection yet."],
        }

    seq = RemoteSequential(
        dht=dht if dht is not None else object(),
        dht_prefix=dht_prefix,
        num_layers=total_layers,
        model_name=model_id,
        **(
            {"placement_model_revision": placement_runtime.config.model_revision}
            if placement_runtime.enabled
            else {}
        ),
    )
    try:
        if nodes is None:
            network_status = seq.get_network_status()
            route_nodes = network_status["nodes"]
        else:
            route_nodes = [
                seq._validate_node_metadata(
                    node,
                    str(node.get("peer_id", "unknown")),
                )
                for node in nodes
            ]
            serving_nodes = [node for node in route_nodes if seq._is_serving_node(node)]
            network_status = {
                "nodes": route_nodes,
                **seq._check_coverage(serving_nodes),
            }
            network_status["covered_layers"] = len(network_status["covered"])
            network_status["missing_layers"] = network_status["missing"]
            route_nodes = serving_nodes
        route = seq.validate_route(route_nodes)
        return {
            "runnable": True,
            "route_ready": True,
            "reasons": [],
            "covered_layers": network_status["covered_layers"],
            "missing_layers": network_status["missing_layers"],
            "total_layers": total_layers,
            "compatible_nodes": len(route),
            "route_trace": _format_route_trace(route),
        }
    except Exception as e:
        try:
            if nodes is None:
                network_status = seq.get_network_status()
                status_nodes = network_status["nodes"]
            else:
                status_nodes = [
                    node for node in nodes if node.get("model_name") == model_id
                ]
                serving_nodes = [
                    node for node in status_nodes if seq._is_serving_node(node)
                ]
                coverage = seq._check_coverage(serving_nodes)
                network_status = {
                    "covered_layers": len(coverage["covered"]),
                    "missing_layers": coverage["missing"],
                }
            return {
                **empty_status,
                "reasons": [str(e)],
                "covered_layers": network_status["covered_layers"],
                "missing_layers": network_status["missing_layers"],
                "compatible_nodes": len(status_nodes),
            }
        except Exception as status_error:
            return {
                **empty_status,
                "reasons": [str(status_error)],
            }


# ---------------------------------------------------------------------------
# Useful-work incentive accounting
# ---------------------------------------------------------------------------

@app.get("/incentives/accounting")
async def get_incentive_accounting() -> dict:
    """Return public receipt and ledger status without exposing private key material."""
    runtime = get_useful_work_runtime()
    local_nodes_list = _local_node_list()
    display_peer_id = None
    for local_node in local_nodes_list:
        peer_getter = getattr(local_node, "get_peer_id", None)
        peer_id = peer_getter() if callable(peer_getter) else None
        if peer_id:
            display_peer_id = peer_id
            break
    if runtime.config.settlement_url and runtime.enabled:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, runtime.refresh_account)
    local_contributions = [
        local_node.get_accounting_snapshot() for local_node in local_nodes_list
    ]
    return {
        **runtime.snapshot(display_peer_id=display_peer_id),
        "token_ui_enabled": False,
        "transfers_enabled": False,
        "withdrawals_enabled": False,
        "local_contribution": local_contributions[0] if local_contributions else None,
        "local_contributions": local_contributions,
    }


def _raise_access_error(exc: AccessError) -> None:
    status_code = 400
    if exc.code == "invalid_api_key":
        status_code = 401
    elif exc.code == "insufficient_credits":
        status_code = 402
    elif exc.code in {"developer_api_disabled", "verified_credits_required"}:
        status_code = 403
    elif exc.code in {"duplicate_request", "capability_replayed"}:
        status_code = 409
    raise HTTPException(
        status_code=status_code,
        detail={"error": exc.code, "message": exc.message},
    ) from exc


async def _verified_credit_snapshot() -> dict:
    runtime = get_useful_work_runtime()
    if runtime.config.settlement_url and runtime.enabled:
        await asyncio.to_thread(runtime.refresh_account)
    return runtime.snapshot()


def _require_local_management_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if _browser_origin_is_allowed(origin):
        return
    raise HTTPException(
        status_code=403,
        detail={
            "error": "local_management_only",
            "message": "Developer key management is restricted to the local desktop app.",
        },
    )


@app.get("/developer/access")
async def get_developer_access(request: Request) -> dict:
    _require_local_management_origin(request)
    manager = get_api_access_manager()
    credits = await _verified_credit_snapshot()
    keys = manager.store.list_keys(manager.identity.public_key)
    usage = manager.store.owner_usage_summary(manager.identity.public_key)
    verified_credits = int(credits.get("verified_credits", 0))
    return {
        "mode": manager.config.mode,
        "application_public_key": manager.identity.public_key,
        "verified_credits": verified_credits,
        "spent_credits": usage["spent_units"],
        "reserved_credits": usage["reserved_units"],
        "available_credits": max(
            0,
            verified_credits - usage["spent_units"] - usage["reserved_units"],
        ),
        "eligible_for_api_key": verified_credits > 0,
        "free_electron_chat": True,
        "developer_api_enabled": manager.config.mode != "off",
        "keys": keys,
        "pricing": {
            "version": 1,
            "price_scale": manager.config.price_scale,
            "model_compute_weights": {
                model_id: max(1, int(model["hidden_size"]) // 768)
                for model_id, model in SUPPORTED_MODELS.items()
            },
        },
    }


@app.post("/developer/api-keys")
async def create_developer_api_key(req: ApiKeyCreateRequest, request: Request) -> dict:
    _require_local_management_origin(request)
    manager = get_api_access_manager()
    if manager.config.mode == "off":
        _raise_access_error(
            AccessError("developer_api_disabled", "Developer API access is disabled.")
        )
    credits = await _verified_credit_snapshot()
    try:
        return manager.store.create_key(
            manager.identity.public_key,
            req.name,
            verified_credits=int(credits.get("verified_credits", 0)),
        )
    except AccessError as exc:
        _raise_access_error(exc)


@app.delete("/developer/api-keys/{key_id}")
async def revoke_developer_api_key(key_id: str, request: Request) -> dict:
    _require_local_management_origin(request)
    manager = get_api_access_manager()
    revoked = manager.store.revoke_key(manager.identity.public_key, key_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="API key not found")
    return {"status": "revoked", "key_id": key_id}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings")
async def get_settings() -> dict:
    token = get_hf_token()
    return {
        "token_set":     token is not None,
        "token_preview": f"{token[:8]}..." if token else None,
        "local_models":  list_local_models(),
        "huggingface":   get_huggingface_connection(),
    }


@app.get("/settings/local-models")
async def get_local_models() -> dict:
    return {"models": list_local_models()}


@app.post("/settings/local-models/inspect")
async def inspect_local_model_directory(req: LocalModelRequest) -> dict:
    try:
        return inspect_local_model(req.model_name, req.path)
    except LocalModelValidationError as e:
        return {
            "valid": False,
            "model_name": req.model_name,
            "error": e.code,
            "message": e.message,
        }


@app.post("/settings/local-models")
async def add_local_model(req: LocalModelRequest) -> dict:
    try:
        return {
            "status": "imported",
            "model": import_local_model(req.model_name, req.path),
        }
    except LocalModelValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": e.code, "message": e.message},
        )


@app.get("/settings/local-models/{model_name:path}")
async def get_local_model(model_name: str) -> dict:
    record = get_local_model_import(model_name, include_path=True)
    if record is None:
        raise HTTPException(status_code=404, detail="Local model import not found.")
    return {"model": record}


@app.delete("/settings/local-models/{model_name:path}")
async def delete_local_model(model_name: str, delete_files: bool = False) -> dict:
    try:
        result = remove_local_model(model_name, delete_files=delete_files)
    except LocalModelDeletionError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": e.code, "message": e.message},
        )
    return {
        "status": "removed" if result["removed"] else "not_found",
        **result,
    }


@app.post("/settings/token")
async def set_token(req: TokenRequest) -> dict:
    try:
        save_hf_token(req.token)
        return {"status": "saved", "token_preview": f"{req.token[:8]}..."}
    except AssertionError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/settings/token")
async def remove_token() -> dict:
    delete_hf_token()
    return {"status": "deleted"}


def _raise_hf_oauth_error(exc: HuggingFaceOAuthError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "message": exc.message},
    )


@app.get("/settings/huggingface/connection")
async def get_hf_connection() -> dict:
    return get_huggingface_connection()


@app.post("/settings/huggingface/oauth/device")
async def start_hf_device_oauth() -> dict:
    try:
        return start_huggingface_device_flow()
    except HuggingFaceOAuthError as exc:
        _raise_hf_oauth_error(exc)


@app.post("/settings/huggingface/oauth/device/poll")
async def poll_hf_device_oauth(req: HuggingFaceDevicePollRequest) -> dict:
    try:
        return poll_huggingface_device_flow(req.flow_id)
    except HuggingFaceOAuthError as exc:
        _raise_hf_oauth_error(exc)


@app.delete("/settings/huggingface/connection")
async def disconnect_hf_connection() -> dict:
    return disconnect_huggingface()


@app.post("/settings/huggingface/download")
async def download_hf_model(req: HuggingFaceDownloadRequest) -> dict:
    try:
        return start_huggingface_model_download(req.model_name, req.revision)
    except HuggingFaceOAuthError as exc:
        _raise_hf_oauth_error(exc)


@app.get("/settings/huggingface/downloads/{job_id}")
async def get_hf_download(job_id: str) -> dict:
    try:
        return get_huggingface_download_job(job_id)
    except HuggingFaceOAuthError as exc:
        _raise_hf_oauth_error(exc)


@app.delete("/settings/huggingface/downloads/{job_id}")
async def cancel_hf_download(job_id: str) -> dict:
    try:
        return cancel_huggingface_download_job(job_id)
    except HuggingFaceOAuthError as exc:
        _raise_hf_oauth_error(exc)


def _validate_hf_model_access(model_name: str, token: Optional[str] = None) -> dict:
    model_info = SUPPORTED_MODELS[model_name]
    if not model_info["gated"]:
        return {
            "valid": True,
            "model_name": model_name,
            "gated": False,
            "token_required": False,
            "access_granted": True,
            "message": "Model is open; token validation is not required.",
        }

    hf_token = token or get_hf_token()
    if not hf_token:
        return {
            "valid": False,
            "model_name": model_name,
            "gated": True,
            "token_required": True,
            "access_granted": False,
            "error": "gated_model_no_token",
            "message": f"{model_name} requires a HuggingFace token.",
        }

    api = HfApi()
    try:
        api.whoami(token=hf_token, cache=False)
        api.model_info(model_name, token=hf_token, timeout=10)
    except GatedRepoError:
        return {
            "valid": False,
            "model_name": model_name,
            "gated": True,
            "token_required": True,
            "access_granted": False,
            "error": "gated_model_access_denied",
            "message": (
                f"Your HuggingFace token is valid, but {model_name} is gated for this account. "
                "Accept the model license or use a token from an approved account."
            ),
        }
    except RepositoryNotFoundError:
        return {
            "valid": False,
            "model_name": model_name,
            "gated": True,
            "token_required": True,
            "access_granted": False,
            "error": "hf_model_not_found_or_private",
            "message": (
                f"HuggingFace could not find {model_name} for this token. "
                "Check model access and token permissions."
            ),
        }
    except HFValidationError as e:
        return {
            "valid": False,
            "model_name": model_name,
            "gated": True,
            "token_required": True,
            "access_granted": False,
            "error": "hf_token_invalid",
            "message": str(e),
        }
    except HfHubHTTPError as e:
        status_code = getattr(getattr(e, "response", None), "status_code", None)
        if status_code in (401, 403):
            return {
                "valid": False,
                "model_name": model_name,
                "gated": True,
                "token_required": True,
                "access_granted": False,
                "error": "hf_token_invalid_or_unauthorized",
                "message": (
                    "HuggingFace rejected this token for the selected model. "
                    "Check the token value and accepted model license."
                ),
            }
        return {
            "valid": False,
            "model_name": model_name,
            "gated": True,
            "token_required": True,
            "access_granted": False,
            "error": "hf_validation_failed",
            "message": (
                "Could not validate HuggingFace access before model loading. "
                f"Hub status: {status_code or 'unknown'}."
            ),
        }
    except Exception as e:
        return {
            "valid": False,
            "model_name": model_name,
            "gated": True,
            "token_required": True,
            "access_granted": False,
            "error": "hf_validation_failed",
            "message": f"Could not validate HuggingFace access before model loading: {e}",
        }

    return {
        "valid": True,
        "model_name": model_name,
        "gated": True,
        "token_required": True,
        "access_granted": True,
        "message": "HuggingFace token can access this model.",
    }


async def _validate_hf_model_access_async(
    model_name: str,
    token: Optional[str] = None,
) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _validate_hf_model_access, model_name, token)


@app.post("/settings/token/validate")
async def validate_token(req: TokenValidationRequest) -> dict:
    return await _validate_hf_model_access_async(req.model_name, req.token)


# ---------------------------------------------------------------------------
# Node management
# ---------------------------------------------------------------------------

@app.post("/node/start")
async def start_node(req: NodeStartRequest) -> dict:
    return await _start_node_runtime(req)


async def _start_node_runtime(
    req: NodeStartRequest,
    *,
    external_cancel: Optional[threading.Event] = None,
) -> dict:
    global _active_node_starts
    supervisor_snapshot = _network_snapshot()
    with _generator_lifecycle_lock:
        backend_closing = _backend_closing
    if backend_closing or not network_supervisor.accepting_roles:
        return {
            "status": "error",
            "error": "backend_shutting_down",
            "message": "The backend network lifecycle is shutting down.",
        }
    if (
        _supervisor_active(supervisor_snapshot)
        and req.dht_prefix != network_supervisor.dht_prefix
    ):
        return {
            "status": "error",
            "error": "network_prefix_mismatch",
            "message": (
                f"This backend supervises DHT prefix {network_supervisor.dht_prefix!r}; "
                f"the request used {req.dht_prefix!r}."
            ),
        }
    existing_prefixes = {local_node.dht_prefix for local_node in _local_node_list()}
    if existing_prefixes and req.dht_prefix not in existing_prefixes:
        return {
            "status": "error",
            "error": "multi_prefix_local_nodes_not_supported",
            "message": (
                "Multiple local nodes in one backend must use the same DHT prefix "
                "so they participate in one route."
            ),
        }

    replicas = _matching_local_replicas(req)
    if (
        replicas
        and not req.confirm_local_replica
        and (not placement_runtime.enabled or req.placement_mode == "custom")
    ):
        replica_label = "replica" if len(replicas) == 1 else "replicas"
        raise HTTPException(
            status_code=409,
            detail={
                "error": "local_replica_confirmation_required",
                "message": (
                    f"{len(replicas)} loaded local {replica_label} already use "
                    f"{req.model_name} layers {req.layer_start}-{req.layer_end}. "
                    "Turned-off replicas still retain their model layers in memory. "
                    "Confirm this additional exact replica to continue."
                ),
            },
        )

    serving_nodes = _active_serving_nodes(
        req.model_name,
        req.dht_prefix,
        supervisor_snapshot=supervisor_snapshot,
    )
    plan_network_fields = (
        _network_response_fields(supervisor_snapshot)
        if _supervisor_active(supervisor_snapshot)
        else {
            "snapshot_stale": False,
            "refreshing": False,
            "snapshot_source": "validated_dht",
            "snapshot_age_seconds": 0.0,
        }
    )
    current_plan = {
        **_build_model_serving_plan(
            req.model_name,
            req.layer_end - req.layer_start,
            req.dht_prefix,
            serving_nodes,
        ),
        **plan_network_fields,
    }
    if (
        not placement_runtime.enabled
        and
        req.coverage_revision is not None
        and req.coverage_revision != current_plan["coverage_revision"]
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "coverage_revision_stale",
                "message": "Layer coverage changed. Review the current serving plan.",
                "plan": current_plan,
            },
        )

    candidate = evaluate_candidate(
        serving_nodes,
        int(SUPPORTED_MODELS[req.model_name]["num_layers"]),
        req.layer_start,
        req.layer_end,
    )
    if (
        not placement_runtime.enabled
        and
        current_plan["missing_ranges"]
        and candidate["newly_covered_layers"] == 0
        and not candidate["completes_route"]
        and not req.confirm_redundancy
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "redundancy_confirmation_required",
                "message": (
                    f"Layers {req.layer_start}-{req.layer_end} add redundancy while "
                    "the model still has route gaps. Confirm to continue."
                ),
                "plan": current_plan,
            },
        )

    overlapping_node = _has_overlapping_local_node(req)
    if (
        overlapping_node is not None
        and (not placement_runtime.enabled or req.placement_mode == "custom")
    ):
        return {
            "status": "error",
            "error": "overlapping_layer_range",
            "message": (
                f"Requested layers {req.layer_start}-{req.layer_end} overlap "
                f"existing local node {overlapping_node.layer_start}-{overlapping_node.layer_end}. "
                "Use a non-overlapping slice."
            ),
        }

    model_info = SUPPORTED_MODELS[req.model_name]
    try:
        local_model_path = get_local_model_path(req.model_name)
    except LocalModelValidationError as e:
        return {
            "status": "error",
            "error": "local_model_import_invalid",
            "message": e.message,
            "validation": {
                "valid": False,
                "model_name": req.model_name,
                "error": e.code,
                "message": e.message,
            },
        }
    if model_info["gated"] and local_model_path is None:
        return {
            "status": "error",
            "error": "local_model_import_required",
            "message": (
                f"{req.model_name} is gated. Download the approved model from Hugging Face "
                "outside DistribLLM, then import the local model directory before starting."
            ),
        }

    operation_id = str(uuid4())
    placement_reservation: Optional[dict] = None
    placement_tracked = False
    if placement_runtime.enabled:
        layer_capacity = int(req.layer_capacity or (req.layer_end - req.layer_start))
        if not 1 <= layer_capacity <= int(model_info["num_layers"]):
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "invalid_layer_capacity",
                    "message": (
                        f"layer_capacity must be between 1 and {model_info['num_layers']}."
                    ),
                },
            )
        try:
            placement_result = await asyncio.to_thread(
                placement_runtime.reserve,
                participant_id=_placement_participant_id(supervisor_snapshot),
                idempotency_key=req.placement_idempotency_key or operation_id,
                model_name=req.model_name,
                layer_capacity=layer_capacity,
                placement_mode=req.placement_mode,
                layer_start=req.layer_start if req.placement_mode == "custom" else None,
                layer_end=req.layer_end if req.placement_mode == "custom" else None,
                expected_topology_revision=req.placement_revision,
            )
        except PlacementClientError as exc:
            raise _placement_http_error(exc) from exc
        placement_reservation = placement_result["reservation"]
        req = req.model_copy(
            update={
                "layer_start": int(placement_reservation["layer_start"]),
                "layer_end": int(placement_reservation["layer_end"]),
                "layer_capacity": layer_capacity,
                "placement_revision": int(placement_result["topology_revision"]),
            }
        )
        allocated_overlap = _has_overlapping_local_node(req)
        if allocated_overlap is not None:
            try:
                await asyncio.to_thread(
                    placement_runtime.release_reservation,
                    placement_reservation,
                    "local_overlap_after_reservation",
                )
            except PlacementClientError as exc:
                logger.warning("Could not release rejected placement reservation: %s", exc)
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "overlapping_layer_range",
                    "message": (
                        f"Coordinator allocated {req.layer_start}-{req.layer_end}, but local "
                        f"node {allocated_overlap.layer_start}-{allocated_overlap.layer_end} "
                        "already owns that range. Turn off the stale local node and retry."
                    ),
                },
            )

    # Use default peers if none provided
    peers = _role_initial_peers(req.initial_peers)
    rpc_uid_suffix = None
    local_node = None
    transaction: Optional[_NodeStartTransaction] = None

    try:
        local_node = Node(
            model_name=req.model_name,
            layer_start=req.layer_start,
            layer_end=req.layer_end,
            dht_prefix=req.dht_prefix,
            initial_peers=peers,
            device=req.device,
            hf_token=None,
            local_model_path=local_model_path,
            publication_verifier=network_supervisor.verify_publication,
        )
        if placement_reservation is not None:
            local_node.placement_model_revision = placement_runtime.config.model_revision
            local_node.placement_lease = await asyncio.to_thread(
                placement_runtime.begin_joining,
                placement_reservation,
                local_node.node_id,
            )
            placement_tracked = True

        with _generator_lifecycle_lock:
            if _backend_closing or not network_supervisor.accepting_roles:
                return {
                    "status": "error",
                    "error": "backend_shutting_down",
                    "message": "The backend network lifecycle is shutting down.",
                }
            for active_start in _active_node_starts.values():
                active_candidate = active_start.candidate
                if active_candidate is None:
                    continue
                if (
                    getattr(active_candidate, "model_name", None) == req.model_name
                    and getattr(active_candidate, "dht_prefix", None) == req.dht_prefix
                    and req.layer_start
                    < int(getattr(active_candidate, "layer_end", req.layer_start))
                    and req.layer_end
                    > int(getattr(active_candidate, "layer_start", req.layer_end))
                ):
                    return {
                        "status": "error",
                        "error": "node_start_in_progress",
                        "message": (
                            "Another local node startup already owns an overlapping "
                            "layer range. Wait for it to finish before retrying."
                        ),
                    }
            committed_overlap = _has_overlapping_local_node(req)
            if committed_overlap is not None:
                return {
                    "status": "error",
                    "error": "overlapping_layer_range",
                    "message": (
                        f"Requested layers {req.layer_start}-{req.layer_end} overlap "
                        f"existing local node {committed_overlap.layer_start}-"
                        f"{committed_overlap.layer_end}. Use a non-overlapping slice."
                    ),
                }
            committed_replicas = _matching_local_replicas(req)
            if committed_replicas and not req.confirm_local_replica:
                return {
                    "status": "error",
                    "error": "local_replica_confirmation_required",
                    "message": (
                        "A matching local replica committed while this request was "
                        "being validated. Confirm the additional replica and retry."
                    ),
                }
            rpc_uid_suffix = _next_rpc_uid_suffix(req)
            local_node.rpc_uid_suffix = rpc_uid_suffix
            transaction = _NodeStartTransaction(
                operation_id=operation_id,
                candidate=local_node,
            )
            _active_node_starts[operation_id] = transaction

        _raise_if_node_start_cancelled(transaction, external_cancel)
        loop = asyncio.get_running_loop()
        start_future = asyncio.ensure_future(
            loop.run_in_executor(None, local_node.start)
        )
        transaction.start_future = start_future
        await asyncio.shield(start_future)
        _raise_if_node_start_cancelled(transaction, external_cancel)
        if not local_node.is_running():
            raise RuntimeError("node.start() completed but is_running() is False")
        if placement_reservation is not None:
            local_node.placement_lease = await asyncio.to_thread(
                placement_runtime.mark_online,
                local_node.node_id,
                local_node.get_info(),
            )

        with _generator_lifecycle_lock:
            if external_cancel is not None and external_cancel.is_set():
                transaction.cancel_event.set()
            if (
                _backend_closing
                or transaction.cancel_event.is_set()
                or not network_supervisor.accepting_roles
            ):
                raise _NodeStartupCancelled(
                    "Node startup was cancelled before runtime commit."
                )
            committed_overlap = _has_overlapping_local_node(req)
            if committed_overlap is not None:
                raise RuntimeError(
                    f"Requested layers {req.layer_start}-{req.layer_end} now overlap "
                    f"local node {committed_overlap.layer_start}-"
                    f"{committed_overlap.layer_end}; startup was rolled back."
                )
            if _matching_local_replicas(req) and not req.confirm_local_replica:
                raise RuntimeError(
                    "A matching local replica committed during startup; confirm the "
                    "additional replica and retry."
                )
            transaction.role_registered = True
            _register_network_worker(local_node, "ready")
            _register_local_node(local_node)
            transaction.committed = True
            transaction.candidate = None
            transaction.role_registered = False

        _invalidate_serving_plan_cache(req.model_name)
        try:
            _runtime_state.record_event(
                kind="node",
                phase="ready",
                status="info",
                message=(
                    f"Node {local_node.node_id} is serving {req.model_name} "
                    f"layers {req.layer_start}-{req.layer_end}."
                ),
                operation_id=operation_id,
                details=local_node.get_info(),
            )
        except Exception as exc:
            logger.warning("Could not record node-ready event: %s", exc)
        return {"status": "started", "info": local_node.get_info()}

    except _NodeStartupCancelled as e:
        cleanup_complete = True
        if transaction is not None and not transaction.committed:
            cleanup_complete = await _rollback_node_start(transaction)
        try:
            _runtime_state.record_event(
                kind="node",
                phase="cancelled" if cleanup_complete else "cleanup_pending",
                status="info" if cleanup_complete else "error",
                message=str(e),
                operation_id=operation_id,
            )
        except Exception as exc:
            logger.warning("Could not record node cancellation: %s", exc)
        if not cleanup_complete:
            return {
                "status": "cleanup_pending",
                "message": (
                    f"{e} The candidate still owns runtime handles; retry Delete."
                ),
            }
        return {"status": "cancelled", "message": str(e)}
    except asyncio.CancelledError:
        if transaction is not None:
            transaction.cancel_event.set()
            if not transaction.committed:
                try:
                    await _rollback_node_start(transaction)
                except asyncio.CancelledError:
                    pass
        raise
    except AssertionError as e:
        cleanup_complete = True
        if transaction is not None and not transaction.committed:
            cleanup_complete = await _rollback_node_start(transaction)
        elif transaction is None:
            cleanup_complete = _cleanup_failed_node(local_node)
        if not cleanup_complete:
            return {
                "status": "cleanup_pending",
                "error": str(e),
                "message": "Node startup failed and cleanup is still pending.",
            }
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"Node start failed: {e}", exc_info=True)
        cleanup_complete = True
        if transaction is not None and not transaction.committed:
            cleanup_complete = await _rollback_node_start(transaction)
        elif transaction is None:
            cleanup_complete = _cleanup_failed_node(local_node)
        if not cleanup_complete:
            return {
                "status": "cleanup_pending",
                "error": str(e),
                "message": "Node startup failed and cleanup is still pending.",
            }
        if _is_cuda_out_of_memory(e):
            return _cuda_memory_error_response(
                req.model_name,
                layer_start=req.layer_start,
                layer_end=req.layer_end,
            )
        if _is_huggingface_auth_expired(e):
            return _huggingface_reconnect_response(req.model_name)
        return {"status": "error", "error": str(e)}
    finally:
        committed = bool(transaction is not None and transaction.committed)
        if placement_reservation is not None and not committed:
            try:
                if placement_tracked and local_node is not None:
                    await asyncio.to_thread(
                        placement_runtime.release_node,
                        local_node.node_id,
                        "startup_rollback",
                    )
                else:
                    await asyncio.to_thread(
                        placement_runtime.release_reservation,
                        placement_reservation,
                        "startup_rollback",
                    )
            except PlacementClientError as exc:
                logger.warning(
                    "Placement release failed during startup rollback; lease will expire: %s",
                    exc,
                )
        if transaction is not None:
            with _generator_lifecycle_lock:
                if _active_node_starts.get(operation_id) is transaction:
                    _active_node_starts.pop(operation_id, None)
            transaction.done_event.set()


@app.post("/node/start-async", status_code=202)
async def start_node_async(req: NodeStartRequest) -> dict:
    resource_key = (
        f"node:{req.dht_prefix}:{req.model_name}:{req.layer_start}:{req.layer_end}"
    )

    def target(progress, cancelled) -> dict:
        progress("validating", "Refreshing coverage and validating the layer request.")
        if cancelled.is_set():
            return {"status": "cancelled"}
        progress("loading", "Establishing transport and loading model layers.")
        try:
            result = asyncio.run(
                _start_node_runtime(req, external_cancel=cancelled)
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            result = {"status": "error", **detail}
        if cancelled.is_set() and result.get("status") == "started":
            return {
                **result,
                "cancel_requested": True,
                "message": (
                    "Cancellation arrived after the node committed. The node remains "
                    "registered; use Turn Off or Delete explicitly if it is unwanted."
                ),
            }
        return result

    return _lifecycle_jobs.submit("node_start", resource_key, target)


@app.post("/node/turn-on")
async def turn_on_node(node_id: Optional[str] = None) -> dict:
    try:
        local_node = _find_local_node(node_id)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    if local_node is None:
        return {"status": "not_found"}
    operation, conflict = _begin_node_lifecycle_operation(local_node, "turn_on")
    if conflict is not None:
        return conflict
    assert operation is not None
    placement_reserved = False
    try:
        if local_node.is_running():
            return {"status": "already_running", "info": local_node.get_info()}
        if placement_runtime.enabled:
            try:
                placement_result = await asyncio.to_thread(
                    placement_runtime.reserve,
                    participant_id=_placement_participant_id(),
                    idempotency_key=f"turn-on-{local_node.node_id}-{uuid4().hex}",
                    model_name=local_node.model_name,
                    layer_capacity=local_node.layer_end - local_node.layer_start,
                    placement_mode="custom",
                    layer_start=local_node.layer_start,
                    layer_end=local_node.layer_end,
                    expected_topology_revision=None,
                )
                reservation = placement_result["reservation"]
                local_node.placement_lease = await asyncio.to_thread(
                    placement_runtime.begin_joining,
                    reservation,
                    local_node.node_id,
                )
                placement_reserved = True
            except PlacementClientError as exc:
                raise _placement_http_error(exc) from exc
        _result, cancellation_count = await _run_node_lifecycle_operation(
            operation,
            local_node.start,
        )
        if placement_reserved:
            local_node.placement_lease = await asyncio.to_thread(
                placement_runtime.mark_online,
                local_node.node_id,
                local_node.get_info(),
            )
        _register_network_worker(local_node, "ready")
        _invalidate_serving_plan_cache(local_node.model_name)
        if cancellation_count:
            raise asyncio.CancelledError
        return {"status": "turned_on", "info": local_node.get_info()}
    except Exception as e:
        logger.error(f"Node turn-on failed: {e}", exc_info=True)
        if placement_reserved:
            try:
                await _run_node_lifecycle_operation(operation, local_node.turn_off)
            except Exception as cleanup_exc:
                logger.warning("Turn-on rollback could not stop the node: %s", cleanup_exc)
            try:
                await asyncio.to_thread(
                    placement_runtime.release_node,
                    local_node.node_id,
                    "turn_on_rollback",
                )
            except PlacementClientError as release_exc:
                logger.warning(
                    "Turn-on placement release failed; lease will expire: %s",
                    release_exc,
                )
            local_node.placement_lease = None
        if isinstance(e, HTTPException):
            raise
        if _is_cuda_out_of_memory(e):
            return _cuda_memory_error_response(
                local_node.model_name,
                layer_start=local_node.layer_start,
                layer_end=local_node.layer_end,
            )
        return {"status": "error", "error": str(e)}
    finally:
        _finish_node_lifecycle_operation(operation)


@app.post("/node/turn-off")
async def turn_off_node(node_id: Optional[str] = None) -> dict:
    try:
        local_node = _find_local_node(node_id)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    if local_node is None:
        return {"status": "not_found"}
    operation, conflict = _begin_node_lifecycle_operation(local_node, "turn_off")
    if conflict is not None:
        return conflict
    assert operation is not None
    try:
        requires_turn_off_getter = getattr(
            local_node,
            "requires_turn_off",
            None,
        )
        if callable(requires_turn_off_getter):
            requires_turn_off = bool(requires_turn_off_getter())
        else:
            pending_cleanup_getter = getattr(
                local_node,
                "has_pending_serving_cleanup",
                None,
            )
            pending_cleanup = bool(
                pending_cleanup_getter()
                if callable(pending_cleanup_getter)
                else False
            )
            requires_turn_off = bool(
                local_node.is_running() or pending_cleanup
            )
        if not requires_turn_off:
            return {"status": "already_off", "info": local_node.get_info()}
        dependency = _generator_dependency(local_node)
        generator_owner = dependency.get("generator_owner")
        if dependency.get("required") and generator_owner is not None:
            with _generator_lifecycle_lock:
                if (
                    generator is generator_owner
                    and not _generator_cleanup_in_progress
                    and _pending_generator_cleanup is None
                ):
                    generator_owner.request_stop()
                    _runtime_state.transition_generator(
                        "suspended",
                        model_name=generator_owner.model_name,
                        components_loaded=True,
                        route_ready=False,
                        reasons=[
                            "A required local serving node was turned off. "
                            "Restore complete RPC-healthy coverage before generating."
                        ],
                        health=dependency.get("health"),
                    )
        stopped, cancellation_count = await _run_node_lifecycle_operation(
            operation,
            local_node.turn_off,
        )
        if stopped is False:
            try:
                _register_network_worker(local_node, "cleanup_pending")
            except Exception as exc:
                logger.warning("Could not synchronize pending node cleanup: %s", exc)
            if cancellation_count:
                raise asyncio.CancelledError
            return {
                "status": "cleanup_pending",
                "message": (
                    "The node stopped accepting work, but one owned network "
                    "handle is still shutting down. Retry Turn Off or Delete."
                ),
                "info": local_node.get_info(),
            }
        _register_network_worker(local_node, "stopped")
        placement_warning = None
        if placement_runtime.enabled:
            try:
                await asyncio.to_thread(
                    placement_runtime.release_node,
                    local_node.node_id,
                    "operator_turn_off",
                )
            except PlacementClientError as exc:
                placement_warning = (
                    "Node is off, but the coordinator could not confirm release; "
                    "the reservation remains unavailable until its lease expires."
                )
                logger.warning("Turn-off placement release failed: %s", exc)
            local_node.placement_lease = None
        _invalidate_serving_plan_cache(local_node.model_name)
        _runtime_state.record_event(
            kind="node",
            phase="turned_off",
            status="info",
            message=f"Node {local_node.node_id} stopped serving.",
            details={"generator_suspended": bool(dependency.get("required"))},
        )
        if cancellation_count:
            raise asyncio.CancelledError
        return {
            "status": "turned_off",
            "info": local_node.get_info(),
            "placement_warning": placement_warning,
        }
    except Exception as e:
        logger.error(f"Node turn-off failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
    finally:
        _finish_node_lifecycle_operation(operation)


@app.delete("/node")
async def delete_node(
    node_id: Optional[str] = None,
    confirm_generator_stop: bool = False,
) -> dict:
    try:
        local_node = _find_local_node(node_id)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    if local_node is None:
        return {"status": "not_found"}
    operation, conflict = _begin_node_lifecycle_operation(local_node, "delete")
    if conflict is not None:
        return conflict
    assert operation is not None
    try:
        dependency = _generator_dependency(local_node)
        if dependency.get("required") and not confirm_generator_stop:
            return {
                "status": "error",
                "error": "generator_dependency_confirmation_required",
                "message": (
                    "This node is required by the active generator route. "
                    "Deleting it will stop and unload the generator."
                ),
                "requires_generator_stop": True,
            }
        generator_result = None
        if dependency.get("required"):
            generator_result = await _unload_generator_runtime(
                "A required local serving node was deleted."
            )
        stopped, cancellation_count = await _run_node_lifecycle_operation(
            operation,
            local_node.stop,
        )
        if stopped is False:
            try:
                _register_network_worker(local_node, "cleanup_pending")
            except Exception as exc:
                logger.warning("Could not synchronize pending node cleanup: %s", exc)
            if cancellation_count:
                raise asyncio.CancelledError
            return {
                "status": "cleanup_pending",
                "message": (
                    "The node stopped accepting work, but cleanup is still in "
                    "progress. Retry Delete before starting a replacement."
                ),
                "info": local_node.get_info(),
                "generator": generator_result,
            }
        _unregister_local_node(local_node)
        _invalidate_serving_plan_cache(local_node.model_name)
        _runtime_state.record_event(
            kind="node",
            phase="deleted",
            status="info",
            message=f"Node {local_node.node_id} was deleted.",
            details={"generator": generator_result},
        )
        if cancellation_count:
            raise asyncio.CancelledError
        return {"status": "deleted", "generator": generator_result}
    except Exception as e:
        logger.error(f"Node delete failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}
    finally:
        _finish_node_lifecycle_operation(operation)


@app.post("/node/stop")
async def stop_node(node_id: Optional[str] = None) -> dict:
    result = await delete_node(node_id)
    if result["status"] == "deleted":
        return {"status": "stopped"}
    if result["status"] == "not_found":
        return {"status": "not_running"}
    return result



# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------

@app.post("/generator/start")
async def start_generator(req: GeneratorStartRequest) -> dict:
    return await _start_generator_runtime(req)


async def _start_generator_runtime(
    req: GeneratorStartRequest,
    *,
    external_cancel: Optional[threading.Event] = None,
) -> dict:
    global generator, client_dht, client_dht_prefix, _active_generator_start
    startup_started_at = time.perf_counter()
    supervisor_snapshot = _network_snapshot()
    with _generator_lifecycle_lock:
        backend_closing = _backend_closing
        cleanup_in_progress = _generator_cleanup_in_progress
        cleanup_pending = _pending_generator_cleanup is not None
        quarantine_reason = _generator_identity_quarantine
    if backend_closing or not network_supervisor.accepting_roles:
        return {
            "status": "error",
            "error": "backend_shutting_down",
            "message": "The backend network lifecycle is shutting down.",
        }
    if (
        _supervisor_active(supervisor_snapshot)
        and req.dht_prefix != network_supervisor.dht_prefix
    ):
        return {
            "status": "error",
            "error": "network_prefix_mismatch",
            "message": (
                f"This backend supervises DHT prefix {network_supervisor.dht_prefix!r}; "
                f"the request used {req.dht_prefix!r}."
            ),
        }
    if cleanup_in_progress or cleanup_pending:
        return {
            "status": "error",
            "error": (
                "generator_cleanup_in_progress"
                if cleanup_in_progress
                else "generator_cleanup_pending"
            ),
            "message": (
                "The previous generator runtime still owns resources. Retry "
                "Unload before starting a replacement."
            ),
        }
    if quarantine_reason is not None:
        return {
            "status": "error",
            "error": "generator_identity_quarantined",
            "message": quarantine_reason,
        }

    model_info = SUPPORTED_MODELS[req.model_name]
    try:
        local_model_path = get_local_model_path(req.model_name)
    except LocalModelValidationError as e:
        return {
            "status": "error",
            "error": "local_model_import_invalid",
            "message": e.message,
            "validation": {
                "valid": False,
                "model_name": req.model_name,
                "error": e.code,
                "message": e.message,
            },
        }
    if model_info["gated"] and local_model_path is None:
        return {
            "status": "error",
            "error": "local_model_import_required",
            "message": (
                f"{req.model_name} is gated. Download the approved model from Hugging Face "
                "outside DistribLLM, then import the local model directory before starting."
            ),
        }

    peers = _generator_initial_peers(
        _role_initial_peers(req.initial_peers),
        req.model_name,
        req.dht_prefix,
    )
    if not _generator_start_lock.acquire(blocking=False):
        return {
            "status": "error",
            "error": "generator_start_in_progress",
            "message": "Another generator startup is already in progress.",
        }

    operation_id = str(uuid4())
    transaction: Optional[_GeneratorStartTransaction] = None
    try:
        with _generator_lifecycle_lock:
            if _backend_closing or not network_supervisor.accepting_roles:
                return {
                    "status": "error",
                    "error": "backend_shutting_down",
                    "message": "The backend network lifecycle is shutting down.",
                }
            if (
                _generator_cleanup_in_progress
                or _pending_generator_cleanup is not None
            ):
                return {
                    "status": "error",
                    "error": (
                        "generator_cleanup_in_progress"
                        if _generator_cleanup_in_progress
                        else "generator_cleanup_pending"
                    ),
                    "message": (
                        "The previous generator runtime still owns resources. "
                        "Retry Unload before starting a replacement."
                    ),
                }
            if _generator_identity_quarantine is not None:
                return {
                    "status": "error",
                    "error": "generator_identity_quarantined",
                    "message": _generator_identity_quarantine,
                }
            if _active_generator_start is not None:
                return {
                    "status": "error",
                    "error": "generator_start_in_progress",
                    "message": "Another generator startup is already in progress.",
                }
            destructive_node_operation = next(
                (
                    operation
                    for operation in _active_node_operations.values()
                    if operation.action in {"turn_off", "delete"}
                ),
                None,
            )
            if destructive_node_operation is not None:
                return {
                    "status": "error",
                    "error": "node_lifecycle_operation_in_progress",
                    "message": (
                        "A local provider is being turned off or deleted. Wait for "
                        "that exact lifecycle operation before starting the generator."
                    ),
                }
            existing_generator = (
                generator
                if generator is not None and generator.is_loaded()
                else None
            )
            if existing_generator is None:
                transaction = _GeneratorStartTransaction(operation_id)
                _active_generator_start = transaction

        if existing_generator is not None:
            status = await get_generator_status()
            return {
                "status": "already_ready" if status["ready"] else "suspended",
                "message": (
                    "Unload the current generator before starting another model."
                    if existing_generator.model_name != req.model_name
                    else "The generator is already loaded."
                ),
                "generator": status,
            }

        assert transaction is not None
        _raise_if_generator_start_cancelled(transaction, external_cancel)
        _runtime_state.transition_generator(
            "starting",
            model_name=req.model_name,
            components_loaded=False,
            route_ready=False,
            reasons=["Starting the generator network client."],
        )
        _cleanup_failed_generator(generator)
        generator = None
        _shutdown_client_dht()

        if _generator_identity_quarantine is not None:
            raise RuntimeError(_generator_identity_quarantine)
        dht_kwargs = _generator_dht_kwargs(peers)

        def construct_candidate_dht():
            return hivemind.DHT(**dht_kwargs)

        candidate_dht = await _run_generator_start_executor_work(
            transaction,
            "dht_future",
            construct_candidate_dht,
        )
        transaction.candidate_dht = candidate_dht
        _raise_if_generator_start_cancelled(transaction, external_cancel)
        if candidate_dht.peer_id is None:
            raise RuntimeError("Generator DHT started but peer_id is None")
        generator_peer_id = str(candidate_dht.peer_id)
        role_peer_ids = {
            str(peer_id)
            for peer_id in [
                supervisor_snapshot.get("control_peer_id"),
                *(local_node.get_peer_id() for local_node in _local_node_list()),
            ]
            if peer_id
        }
        if generator_peer_id in role_peer_ids:
            raise RuntimeError(
                "Generator P2P identity collides with a control-plane or worker peer"
            )
        if _supervisor_active():
            transaction.role_registered = True
            network_supervisor.register_role(
                "generator",
                peer_id=generator_peer_id,
                state="starting",
            )
        _raise_if_generator_start_cancelled(transaction, external_cancel)

        sequential_kwargs = {}
        try:
            sequential_parameters = inspect.signature(RemoteSequential).parameters
        except (TypeError, ValueError):
            sequential_parameters = {}
        if "topology_provider" in sequential_parameters and _supervisor_active():
            sequential_kwargs["topology_provider"] = (
                lambda: _generator_topology_nodes(req.model_name)
            )
        if "placement_model_revision" in sequential_parameters and placement_runtime.enabled:
            sequential_kwargs["placement_model_revision"] = (
                placement_runtime.config.model_revision
            )
        sequential = RemoteSequential(
            dht=candidate_dht,
            dht_prefix=req.dht_prefix,
            num_layers=model_info["num_layers"],
            model_name=req.model_name,
            **sequential_kwargs,
        )
        transaction.sequential = sequential
        _raise_if_generator_start_cancelled(transaction, external_cancel)

        start_health_monitor = getattr(sequential, "start_health_monitor", None)
        if callable(start_health_monitor):
            start_health_monitor()
        _raise_if_generator_start_cancelled(transaction, external_cancel)

        _runtime_state.transition_generator(
            "validating_route",
            model_name=req.model_name,
            components_loaded=False,
            route_ready=False,
            reasons=["Waiting for a complete RPC-healthy provider route."],
        )
        route_timeout = max(
            1.0,
            float(os.environ.get("DISTRIBLLM_GENERATOR_ROUTE_START_TIMEOUT", "45")),
        )
        deadline = time.monotonic() + route_timeout
        readiness: Optional[dict] = None
        while True:
            _raise_if_generator_start_cancelled(transaction, external_cancel)
            health_getter = getattr(sequential, "get_health_readiness", None)
            if callable(health_getter):
                readiness = health_getter()
                if readiness.get("route_ready"):
                    break
                if time.monotonic() >= deadline:
                    reasons = readiness.get("reasons") or [
                        "No complete RPC-healthy provider route became available."
                    ]
                    raise RuntimeError(
                        f"Generator route validation timed out after {route_timeout:g} seconds: "
                        + "; ".join(str(reason) for reason in reasons)
                    )
                await asyncio.sleep(0.25)
                continue
            route_validator = getattr(sequential, "validate_reachable_route", None)
            if callable(route_validator):
                route = await _run_generator_start_executor_work(
                    transaction,
                    "route_validation_future",
                    route_validator,
                )
                readiness = {"route_ready": True, "selected_route": route}
            else:
                readiness = {
                    "route_ready": True,
                    "selected_route": [],
                    "warnings": ["Legacy sequential implementation skipped route preflight."],
                }
            break

        _raise_if_generator_start_cancelled(transaction, external_cancel)
        canary_validator = getattr(sequential, "validate_tensor_route", None)
        if callable(canary_validator):
            canary = await _run_generator_start_executor_work(
                transaction,
                "tensor_canary_future",
                canary_validator,
                int(model_info["hidden_size"]),
            )
        else:
            canary = {
                "ok": True,
                "skipped": True,
                "reason": "Legacy sequential implementation has no tensor canary.",
            }
        _raise_if_generator_start_cancelled(transaction, external_cancel)

        _runtime_state.transition_generator(
            "loading",
            model_name=req.model_name,
            components_loaded=False,
            route_ready=True,
            reasons=[],
            node_trace=_format_route_trace(readiness.get("selected_route", [])),
            health=readiness,
            canary=canary,
        )

        candidate_generator = DistributedGenerator(
            model_name=req.model_name,
            sequential=sequential,
            hf_token=None,
            local_model_path=local_model_path,
            device="cuda"if torch.cuda.is_available() else "cpu",
            dtype= torch.float16 if torch.cuda.is_available() else torch.float32,
        )
        transaction.candidate_generator = candidate_generator
        _raise_if_generator_start_cancelled(transaction, external_cancel)

        loop = asyncio.get_running_loop()
        load_future = asyncio.ensure_future(
            loop.run_in_executor(None, candidate_generator.load)
        )
        transaction.load_future = load_future
        await asyncio.shield(load_future)
        _raise_if_generator_start_cancelled(transaction, external_cancel)
        if not candidate_generator.is_loaded():
            raise RuntimeError("generator.load() completed but is_loaded() is False")

        final_readiness = readiness
        health_getter = getattr(sequential, "get_health_readiness", None)
        if callable(health_getter):
            final_readiness = health_getter()
            if not final_readiness.get("route_ready"):
                raise RuntimeError(
                    "Provider route became unhealthy while generator components were loading: "
                    + "; ".join(
                        str(reason) for reason in final_readiness.get("reasons", [])
                    )
                )
        selected_local_route_nodes = [
            local_node
            for local_node in _local_node_list()
            if _route_contains_local_node(
                final_readiness.get("selected_route", []),
                local_node,
            )
        ]
        _raise_if_generator_start_cancelled(transaction, external_cancel)

        startup_duration_ms = (time.perf_counter() - startup_started_at) * 1000
        set_startup_duration = getattr(
            candidate_generator,
            "set_startup_duration_ms",
            None,
        )
        if callable(set_startup_duration):
            set_startup_duration(startup_duration_ms)
        performance = (
            candidate_generator.get_performance_snapshot()
            if hasattr(candidate_generator, "get_performance_snapshot")
            else {"startup_duration_ms": startup_duration_ms}
        )
        _raise_if_generator_start_cancelled(transaction, external_cancel)

        with _generator_lifecycle_lock:
            if external_cancel is not None and external_cancel.is_set():
                transaction.cancel_event.set()
            if (
                _backend_closing
                or transaction.cancel_event.is_set()
                or not network_supervisor.accepting_roles
            ):
                raise _GeneratorStartupCancelled(
                    "Generator startup was cancelled before runtime commit."
                )
            if any(
                operation.action in {"turn_off", "delete"}
                for operation in _active_node_operations.values()
            ):
                raise _GeneratorStartupCancelled(
                    "A selected provider lifecycle changed before generator commit."
                )
            unavailable_local_nodes = [
                local_node.node_id
                for local_node in selected_local_route_nodes
                if (
                    local_nodes.get(local_node.node_id) is not local_node
                    and not (node is local_node and node.node_id == local_node.node_id)
                )
                or not local_node.is_running()
            ]
            if unavailable_local_nodes:
                raise _GeneratorStartupCancelled(
                    "Selected local provider(s) became unavailable before generator "
                    f"commit: {', '.join(unavailable_local_nodes)}"
                )
            if transaction.role_registered:
                network_supervisor.register_role(
                    "generator",
                    peer_id=generator_peer_id,
                    state="ready",
                )
                if (
                    _backend_closing
                    or transaction.cancel_event.is_set()
                    or not network_supervisor.accepting_roles
                ):
                    raise _GeneratorStartupCancelled(
                        "Generator startup was cancelled during runtime commit."
                    )
            try:
                generator = candidate_generator
                client_dht = candidate_dht
                client_dht_prefix = req.dht_prefix
                _runtime_state.transition_generator(
                    "ready",
                    model_name=req.model_name,
                    components_loaded=True,
                    route_ready=True,
                    reasons=[],
                    node_trace=_format_route_trace(
                        final_readiness.get("selected_route", [])
                    ),
                    health=final_readiness,
                    canary=canary,
                )
            except BaseException:
                if generator is candidate_generator:
                    generator = None
                if client_dht is candidate_dht:
                    client_dht = None
                raise
            transaction.committed = True
            transaction.candidate_generator = None
            transaction.candidate_dht = None
            transaction.sequential = None
            transaction.role_registered = False

        try:
            _runtime_state.record_event(
                kind="generator",
                phase="ready",
                status="info",
                message=(
                    f"Generator for {req.model_name} passed route and tensor validation."
                ),
                operation_id=operation_id,
                details={"startup_duration_ms": startup_duration_ms, "canary": canary},
            )
        except Exception as exc:
            logger.warning("Could not record generator-ready event: %s", exc)
        return {
            "status": "ready",
            "route_ready": True,
            "canary": canary,
            "performance": performance,
        }

    except _GeneratorStartupCancelled as e:
        cleanup_complete = True
        if transaction is not None and not transaction.committed:
            cleanup_complete = await _rollback_generator_start(transaction)
        if not cleanup_complete:
            _runtime_state.transition_generator(
                "stopping",
                model_name=req.model_name,
                route_ready=False,
                reasons=[
                    str(e),
                    "Generator startup cleanup is pending.",
                ],
            )
            return {
                "status": "cleanup_pending",
                "message": str(e),
            }
        _runtime_state.transition_generator(
            "stopped",
            model_name=None,
            components_loaded=False,
            route_ready=False,
            reasons=[str(e)],
        )
        try:
            _runtime_state.record_event(
                kind="generator",
                phase="cancelled",
                status="info",
                message=str(e),
                operation_id=operation_id,
            )
        except Exception as exc:
            logger.warning("Could not record generator cancellation: %s", exc)
        return {"status": "cancelled", "message": str(e)}
    except asyncio.CancelledError:
        cleanup_complete = True
        if transaction is not None:
            transaction.cancel_event.set()
            if not transaction.committed:
                try:
                    cleanup_complete = await _rollback_generator_start(transaction)
                except asyncio.CancelledError:
                    pass
        _runtime_state.transition_generator(
            "stopped" if cleanup_complete else "stopping",
            model_name=None if cleanup_complete else req.model_name,
            components_loaded=False if cleanup_complete else None,
            route_ready=False,
            reasons=[
                "Generator startup task was cancelled."
                + (
                    " Cleanup is still pending."
                    if not cleanup_complete
                    else ""
                )
            ],
        )
        raise
    except AssertionError as e:
        cleanup_complete = True
        if transaction is not None and not transaction.committed:
            cleanup_complete = await _rollback_generator_start(transaction)
        if not cleanup_complete:
            return {
                "status": "cleanup_pending",
                "error": str(e),
                "message": "Generator startup failed and cleanup is still pending.",
            }
        _runtime_state.transition_generator(
            "failed",
            model_name=req.model_name,
            components_loaded=False,
            route_ready=False,
            reasons=[str(e)],
        )
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"Generator start failed: {e}", exc_info=True)
        cleanup_complete = True
        if transaction is not None and not transaction.committed:
            cleanup_complete = await _rollback_generator_start(transaction)
        if not cleanup_complete:
            _runtime_state.transition_generator(
                "stopping",
                model_name=req.model_name,
                route_ready=False,
                reasons=[str(e), "Generator startup cleanup is pending."],
            )
            return {
                "status": "cleanup_pending",
                "error": str(e),
                "message": "Generator startup failed and cleanup is still pending.",
            }
        _runtime_state.transition_generator(
            "failed",
            model_name=req.model_name,
            components_loaded=False,
            route_ready=False,
            reasons=[str(e)],
        )
        _runtime_state.record_event(
            kind="generator",
            phase="startup",
            status="error",
            message=str(e),
            operation_id=operation_id,
        )
        if _is_cuda_out_of_memory(e):
            return _cuda_memory_error_response(req.model_name)
        if _is_huggingface_auth_expired(e):
            return _huggingface_reconnect_response(req.model_name)
        return {"status": "error", "error": str(e)}
    finally:
        if transaction is not None:
            with _generator_lifecycle_lock:
                if _active_generator_start is transaction:
                    _active_generator_start = None
            transaction.done_event.set()
        _generator_start_lock.release()


@app.post("/generator/start-async", status_code=202)
async def start_generator_async(req: GeneratorStartRequest) -> dict:
    resource_key = f"generator:{req.dht_prefix}:{req.model_name}"

    def target(progress, cancelled) -> dict:
        progress("networking", "Starting the generator network client.")
        if cancelled.is_set():
            return {"status": "cancelled"}
        progress("validating_route", "Proving a complete RPC-healthy tensor route.")
        try:
            result = asyncio.run(
                _start_generator_runtime(req, external_cancel=cancelled)
            )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            result = {"status": "error", **detail}
        if cancelled.is_set() and result.get("status") == "ready":
            return {
                **result,
                "cancel_requested": True,
                "message": (
                    "Cancellation arrived after the generator committed. The ready "
                    "runtime remains loaded; unload it explicitly if it is unwanted."
                ),
            }
        return result

    return _lifecycle_jobs.submit("generator_start", resource_key, target)


@app.get("/lifecycle/jobs/{job_id}")
async def get_lifecycle_job(job_id: str) -> dict:
    job = _lifecycle_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Lifecycle job not found")
    return job


@app.delete("/lifecycle/jobs/{job_id}")
async def cancel_lifecycle_job(job_id: str) -> dict:
    job = _lifecycle_jobs.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Lifecycle job not found")
    return job


@app.get("/generator/status")
async def get_generator_status() -> dict:
    with _generator_lifecycle_lock:
        cleanup_active = _generator_cleanup_in_progress
        cleanup_handles = _pending_generator_cleanup
        active_generator = generator
    if cleanup_active or cleanup_handles is not None:
        pending_generator = (
            cleanup_handles.candidate_generator
            if cleanup_handles is not None
            else None
        )
        components_loaded = bool(
            pending_generator is not None
            and getattr(pending_generator, "is_loaded", lambda: False)()
        )
        snapshot = _runtime_state.generator_snapshot()
        cleanup_reasons = [
            "Generator cleanup is in progress; a replacement cannot start "
            "until all owned handles have stopped."
        ]
        return {
            "ready": False,
            "state": "stopping",
            "components_loaded": components_loaded,
            "model_name": (
                getattr(pending_generator, "model_name", None)
                or snapshot.get("model_name")
            ),
            "route_ready": False,
            "reasons": cleanup_reasons,
            "node_trace": snapshot["node_trace"],
            "performance": None,
            "health": snapshot["health"],
            "canary": snapshot["canary"],
        }
    if active_generator is None or not active_generator.is_loaded():
        snapshot = _runtime_state.generator_snapshot()
        state = snapshot["state"]
        if state not in {"starting", "validating_route", "loading", "failed"}:
            state = "stopped"
        reasons = snapshot["reasons"] or ["Generator not loaded."]
        return {
            "ready": False,
            "state": state,
            "components_loaded": False,
            "model_name": snapshot.get("model_name"),
            "route_ready": False,
            "reasons": reasons,
            "node_trace": snapshot["node_trace"],
            "performance": None,
            "health": snapshot["health"],
            "canary": snapshot["canary"],
        }

    reasons: list[str] = []
    node_trace: list[str] = []
    route_ready = False
    health: Optional[dict] = None

    route_validation_started_at = time.perf_counter()
    try:
        health_getter = getattr(
            active_generator.sequential,
            "get_health_readiness",
            None,
        )
        if callable(health_getter):
            health = health_getter()
        if isinstance(health, dict):
            route = health.get("selected_route", [])
            route_ready = bool(health.get("route_ready"))
            reasons.extend(str(reason) for reason in health.get("reasons", []))
        else:
            route = []
            health = {
                "enabled": False,
                "route_ready": False,
                "selected_route": [],
                "reasons": [
                    "Provider health snapshot is unavailable; status reads do not "
                    "start an unowned network probe."
                ],
            }
            reasons.extend(health["reasons"])
        node_trace = _format_route_trace(route)
    except Exception as e:
        reasons.append(str(e))
    route_validation_ms = (
        time.perf_counter() - route_validation_started_at
    ) * 1000
    performance_getter = getattr(active_generator, "get_performance_snapshot", None)
    performance = (
        performance_getter()
        if callable(performance_getter)
        else {
            "startup_duration_ms": None,
            "load_duration_ms": None,
            "last_generation": None,
        }
    )
    performance["route_validation_ms"] = route_validation_ms

    with _generator_lifecycle_lock:
        lifecycle_changed = bool(
            generator is not active_generator
            or _generator_cleanup_in_progress
            or _pending_generator_cleanup is not None
            or not active_generator.is_loaded()
        )
        if not lifecycle_changed:
            state = "ready" if route_ready else "suspended"
            snapshot = _runtime_state.transition_generator(
                state,
                model_name=active_generator.model_name,
                components_loaded=True,
                route_ready=route_ready,
                reasons=reasons,
                node_trace=node_trace,
                health=health,
            )
            generator_ready = active_generator.is_loaded() and route_ready
    if lifecycle_changed:
        snapshot = _runtime_state.generator_snapshot()
        return {
            "ready": False,
            "state": snapshot["state"],
            "components_loaded": bool(snapshot.get("components_loaded")),
            "model_name": snapshot.get("model_name"),
            "route_ready": False,
            "reasons": [
                "Generator lifecycle changed while route readiness was being checked."
            ],
            "node_trace": snapshot.get("node_trace", []),
            "performance": performance,
            "health": snapshot.get("health"),
            "canary": snapshot.get("canary"),
        }

    return {
        "ready": generator_ready,
        "state": state,
        "components_loaded": True,
        "model_name": active_generator.model_name,
        "route_ready": route_ready,
        "reasons": reasons,
        "node_trace": node_trace,
        "performance": performance,
        "health": health,
        "canary": snapshot.get("canary"),
    }


@app.get("/runtime/snapshot")
async def get_runtime_snapshot() -> dict:
    generator_status = await get_generator_status()
    snapshot = _runtime_state.snapshot()
    snapshot.update(
        {
            "generator": generator_status,
            "local_nodes": _local_node_infos(),
            "lifecycle_jobs": _lifecycle_jobs.list_recent(),
            "lifecycle": (
                _lifecycle_jobs.diagnostics()
                if hasattr(_lifecycle_jobs, "diagnostics")
                else None
            ),
            "network": _network_snapshot(),
        }
    )
    return snapshot


async def _require_generator_ready() -> DistributedGenerator:
    with _generator_lifecycle_lock:
        expected_generator = generator
    status = await get_generator_status()
    with _generator_lifecycle_lock:
        lifecycle_changed = bool(
            expected_generator is None
            or generator is not expected_generator
            or _generator_cleanup_in_progress
            or _pending_generator_cleanup is not None
        )
    if not status["ready"] or lifecycle_changed:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "generator_route_not_ready",
                "state": status["state"],
                "message": "; ".join(status["reasons"]) or "Generator route is not ready.",
                "route_ready": status["route_ready"],
                "components_loaded": status["components_loaded"],
            },
        )
    return expected_generator


@app.post("/generator/stop")
async def stop_generator() -> dict:
    with _generator_lifecycle_lock:
        active_generator = generator
    if active_generator is None or not active_generator.is_loaded():
        return {"status": "not_running"}
    active_generator.request_stop()
    return {"status": "stop_requested"}


@app.post("/generator/unload")
async def unload_generator() -> dict:
    """Stop the generator lifecycle, then release its DHT transport."""
    return await _unload_generator_runtime("Generator unloaded by the user.")


@app.post("/generator/parity/next-token")
async def compare_generator_next_token(req: NextTokenParityRequest) -> dict:
    active_generator = await _require_generator_ready()
    try:
        return await asyncio.to_thread(
            active_generator.compare_next_token_logits,
            prompt=req.prompt,
            atol=req.atol,
            rtol=req.rtol,
        )
    except Exception as e:
        logger.error(f"Next-token parity check failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generator/parity/generate")
async def compare_generator_output(req: GeneratedParityRequest) -> dict:
    active_generator = await _require_generator_ready()
    try:
        return await active_generator.compare_generated_output(
            prompt=req.prompt,
            max_new_tokens=req.max_new_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
            top_k=req.top_k,
            repetition_penalty=req.repetition_penalty,
            do_sample=req.do_sample,
        )
    except Exception as e:
        logger.error(f"Generated-output parity check failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generator/trace")
async def trace_generator(req: GenerationTraceRequest) -> dict:
    active_generator = await _require_generator_ready()
    try:
        trace = await asyncio.to_thread(
            active_generator.trace_generation,
            prompt=req.prompt,
            max_new_tokens=req.max_new_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
            top_k=req.top_k,
            repetition_penalty=req.repetition_penalty,
            do_sample=req.do_sample,
        )
        trace_metadata = _write_generation_trace(trace)
        return {
            **trace,
            **trace_metadata,
        }
    except Exception as e:
        logger.error(f"Generation trace failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generator/trace-async", status_code=202)
async def trace_generator_async(req: GenerationTraceRequest) -> dict:
    """Submit the legacy token trace as a visible, pollable diagnostic job.

    The submission request returns quickly and therefore does not inherit the
    renderer's short control-plane HTTP deadline. DistributedGenerator remains
    the operation owner and rejects overlap with an active chat generation.
    """
    active_generator = await _require_generator_ready()
    request = req.model_dump()

    def target(progress, cancel_event: threading.Event) -> dict:
        if cancel_event.is_set():
            return {"status": "cancelled"}
        progress(
            "legacy_trace",
            "Running the legacy expert token trace. This is separate from the chat stream.",
        )
        trace = active_generator.trace_generation(
            prompt=request["prompt"],
            max_new_tokens=request["max_new_tokens"],
            temperature=request["temperature"],
            top_p=request["top_p"],
            top_k=request["top_k"],
            repetition_penalty=request["repetition_penalty"],
            do_sample=request["do_sample"],
        )
        trace_metadata = _write_generation_trace(trace)
        return {
            "status": "ready",
            "trace": {
                **trace,
                **trace_metadata,
            },
        }

    job = _lifecycle_jobs.submit(
        "generation_trace",
        "generator:legacy-trace",
        target,
    )
    _runtime_state.record_event(
        kind="diagnostic",
        phase="legacy_trace",
        status="submitted",
        message="Legacy generation trace diagnostic submitted.",
        details={
            "job_id": job.get("job_id"),
            "reused": bool(job.get("reused")),
            "max_new_tokens": request["max_new_tokens"],
        },
    )
    return job


@app.get("/generator/traces/analysis")
async def analyze_generator_traces(model_name: Optional[str] = None) -> dict:
    if model_name is not None and model_name not in SUPPORTED_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported model '{model_name}'.",
        )
    return _analyze_generation_traces(model_name=model_name)


def _bearer_token(authorization: Optional[str]) -> str:
    if authorization is None or not authorization.startswith("Bearer "):
        raise AccessError("invalid_api_key", "Authorization must use a Bearer API key.")
    token = authorization[7:].strip()
    if not token:
        raise AccessError("invalid_api_key", "Bearer API key is empty.")
    return token


def _openai_prompt(
    active_generator: DistributedGenerator,
    messages: list[OpenAIChatMessage],
) -> str:
    documents = [message.model_dump() for message in messages]
    tokenizer = getattr(active_generator, "tokenizer", None)
    template = getattr(tokenizer, "apply_chat_template", None)
    if callable(template):
        try:
            return str(
                template(
                    documents,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            )
        except Exception as exc:
            logger.warning("Tokenizer chat template failed; using role labels: %s", exc)
    return "\n".join(
        [*(f"{message.role}: {message.content}" for message in messages), "assistant:"]
    )


def _prompt_position_count(active_generator: DistributedGenerator, prompt: str) -> int:
    tokenizer = getattr(active_generator, "tokenizer", None)
    encoder = getattr(tokenizer, "encode", None)
    if callable(encoder):
        encoded = encoder(prompt, add_special_tokens=True)
        return max(1, len(encoded))
    return max(1, len(prompt.split()))


def _authorize_free_chat(
    active_generator: DistributedGenerator,
    *,
    prompt: str,
    max_new_tokens: Optional[int],
) -> None:
    manager = get_api_access_manager()
    request_id = f"electron-{uuid4()}"
    positions = _prompt_position_count(active_generator, prompt) + int(max_new_tokens or 64)
    capability = manager.issue_capability(
        request_id=request_id,
        key_id="electron-free-chat",
        model_name=active_generator.model_name,
        max_positions=positions,
    )
    manager.verify_and_consume_capability(
        capability,
        request_id=request_id,
        model_name=active_generator.model_name,
        positions=positions,
    )


@app.post("/v1/chat/completions")
async def openai_chat_completions(
    req: OpenAIChatCompletionRequest,
    authorization: Optional[str] = Header(default=None),
    x_request_id: Optional[str] = Header(default=None),
):
    active_generator = await _require_generator_ready()
    if active_generator.model_name != req.model:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "model_not_loaded",
                "message": (
                    f"Generator has {active_generator.model_name}; start {req.model} first."
                ),
            },
        )
    manager = get_api_access_manager()
    reservation: Optional[dict] = None
    try:
        key = manager.store.authenticate(_bearer_token(authorization))
        credits = await _verified_credit_snapshot()
        prompt = _openai_prompt(active_generator, req.messages)
        prompt_positions = _prompt_position_count(active_generator, prompt)
        max_positions = prompt_positions + req.max_tokens
        reservation = manager.store.reserve(
            key_id=key["key_id"],
            model_name=req.model,
            estimated_positions=max_positions,
            verified_credits=int(credits.get("verified_credits", 0)),
            mode=manager.config.mode,
            price_scale=manager.config.price_scale,
            request_id=x_request_id,
        )
        capability = manager.issue_capability(
            request_id=reservation["request_id"],
            key_id=key["key_id"],
            model_name=req.model,
            max_positions=max_positions,
        )
        manager.verify_and_consume_capability(
            capability,
            request_id=reservation["request_id"],
            model_name=req.model,
            positions=max_positions,
        )
    except AccessError as exc:
        if reservation is not None:
            manager.store.release(reservation["request_id"])
        _raise_access_error(exc)

    completion_id = f"chatcmpl-{reservation['request_id']}"
    created_at = int(time.time())

    if req.stream:
        async def event_stream():
            settled = False
            try:
                stream = active_generator.generate_stream(
                    prompt=prompt,
                    max_new_tokens=req.max_tokens,
                    temperature=req.temperature,
                    top_p=req.top_p,
                )
                async with aclosing(stream):
                    async for chunk in stream:
                        if "token" in chunk:
                            event = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_at,
                                "model": req.model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": chunk["token"]},
                                        "finish_reason": None,
                                    }
                                ],
                            }
                            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
                        elif "error" in chunk:
                            manager.store.release(reservation["request_id"])
                            settled = True
                            yield f"data: {json.dumps({'error': chunk['error']})}\n\n"
                            yield "data: [DONE]\n\n"
                            return
                        elif chunk.get("done"):
                            metrics = chunk.get("metrics") or {}
                            if metrics.get("stopped"):
                                manager.store.release(reservation["request_id"])
                            else:
                                manager.store.complete(
                                    reservation["request_id"],
                                    actual_positions=(
                                        prompt_positions + int(metrics.get("generated_tokens", 0))
                                    ),
                                    price_scale=manager.config.price_scale,
                                )
                            settled = True
                            event = {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_at,
                                "model": req.model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": "stop",
                                    }
                                ],
                            }
                            yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
                            yield "data: [DONE]\n\n"
            except GeneratorOperationBusyError as exc:
                manager.store.release(reservation["request_id"])
                settled = True
                error = {
                    "error": {
                        "code": "generator_busy",
                        "message": str(exc),
                        "type": "conflict_error",
                    }
                }
                yield f"data: {json.dumps(error, separators=(',', ':'))}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                if not settled:
                    manager.store.release(reservation["request_id"])

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    response_text = ""
    metrics: dict = {}
    try:
        stream = active_generator.generate_stream(
            prompt=prompt,
            max_new_tokens=req.max_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
        )
        async with aclosing(stream):
            async for chunk in stream:
                if "token" in chunk:
                    response_text += str(chunk["token"])
                elif "error" in chunk:
                    raise RuntimeError(str(chunk["error"]))
                elif chunk.get("done"):
                    metrics = dict(chunk.get("metrics") or {})
        if metrics.get("stopped"):
            raise RuntimeError("Generation was cancelled before completion.")
        generated_tokens = int(metrics.get("generated_tokens", 0))
        usage = manager.store.complete(
            reservation["request_id"],
            actual_positions=prompt_positions + generated_tokens,
            price_scale=manager.config.price_scale,
        )
    except Exception as exc:
        manager.store.release(reservation["request_id"])
        logger.error("Developer API generation failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created_at,
        "model": req.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_positions,
            "completion_tokens": generated_tokens,
            "total_tokens": prompt_positions + generated_tokens,
            "credit_units": usage["charged_units"],
            "projected_credit_units": usage["projected_units"],
        },
    }


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    active_generator = await _require_generator_ready()
    try:
        _authorize_free_chat(
            active_generator,
            prompt=req.message,
            max_new_tokens=req.max_new_tokens,
        )
    except AccessError as exc:
        _raise_access_error(exc)

    full_response         = ""
    node_trace: list[str] = []
    generation_metrics: Optional[dict] = None

    stream = active_generator.generate_stream(
        prompt=req.message,
        max_new_tokens=req.max_new_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
        top_k=req.top_k,
        repetition_penalty=req.repetition_penalty,
        do_sample=req.do_sample,
    )
    async with aclosing(stream):
        async for chunk in stream:
            if "token"  in chunk: full_response += chunk["token"]
            elif "done" in chunk:
                node_trace = chunk.get("node_trace", [])
                generation_metrics = chunk.get("metrics")
            elif "error" in chunk:
                diagnostic = _record_generation_failure(active_generator, str(chunk["error"]))
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "generation_failed",
                        "message": chunk["error"],
                        "diagnostic": diagnostic,
                    },
                )

    return {
        "response":         full_response,
        "node_trace":       node_trace,
        "tokens_generated": (
            generation_metrics.get("generated_tokens", 0)
            if generation_metrics is not None
            else 0
        ),
        "performance": generation_metrics,
    }


def _record_generation_failure(
    active_generator: DistributedGenerator,
    error: str,
) -> dict:
    """Persist a prompt-free, request-correlated summary of the last route failure."""
    details: dict = {"error": error}
    sequential = getattr(active_generator, "sequential", None)
    session_metrics_getter = getattr(sequential, "get_last_session_metrics", None)
    if callable(session_metrics_getter):
        try:
            session_metrics = session_metrics_getter()
            session_failure = session_metrics.get("failure")
            if isinstance(session_failure, dict):
                # Keep the terminal session operation in the same bounded event
                # as the browser-visible error.  This distinguishes a reset at
                # a particular hop from a generic generator failure without
                # persisting prompt text or tensor values.
                details.update(
                    {
                        "request_id": session_failure.get("request_id"),
                        "failure_class": session_failure.get("failure_class"),
                        "peer_id": session_failure.get("peer_id"),
                        "layer_start": session_failure.get("layer_start"),
                        "layer_end": session_failure.get("layer_end"),
                        "reason": session_failure.get("reason"),
                        "session": {
                            key: session_failure.get(key)
                            for key in (
                                "session_id",
                                "route_id",
                                "operation",
                                "operation_id",
                                "position_start",
                                "token_count",
                                "hop_index",
                                "hop_count",
                                "rpc_uid",
                                "input_bytes",
                                "elapsed_ms",
                                "exception_type",
                            )
                        },
                        "completed_hops": [
                            {
                                key: hop.get(key)
                                for key in (
                                    "peer_id",
                                    "rpc_uid",
                                    "layer_start",
                                    "layer_end",
                                    "latency_ms",
                                    "input_bytes",
                                    "expected_position",
                                )
                            }
                            for hop in session_metrics.get("hops", [])
                            if isinstance(hop, dict)
                        ],
                    }
                )
        except Exception as exc:
            details["session_diagnostic_error"] = str(exc)
    health_getter = getattr(sequential, "get_health_readiness", None)
    if callable(health_getter):
        try:
            health = health_getter()
            failover = health.get("last_failover") or {}
            reasons = failover.get("reasons") or []
            terminal = dict(reasons[-1]) if reasons else {}
            details.update(
                {
                    "route_ready": bool(health.get("route_ready")),
                    "route_revision": health.get("route_revision"),
                    "coverage_revision": health.get("coverage_revision"),
                    "attempt_count": failover.get("attempt_count", 0),
                    "failed_over": bool(failover.get("failed_over")),
                }
            )
            # A session failure includes its precise hop.  Stateless-route
            # failures retain the existing health-monitor terminal reason.
            if not details.get("request_id"):
                details.update(
                    {
                        "request_id": terminal.get("request_id"),
                        "failure_class": terminal.get("failure_class"),
                        "peer_id": terminal.get("peer_id"),
                        "layer_start": terminal.get("layer_start"),
                        "layer_end": terminal.get("layer_end"),
                        "reason": terminal.get("reason"),
                    }
                )
        except Exception as exc:
            details["diagnostic_error"] = str(exc)

    _runtime_state.record_event(
        kind="generation",
        phase="forward",
        status="error",
        message=error,
        operation_id=details.get("request_id"),
        details=details,
    )
    return details


@app.websocket("/stream")
async def stream(websocket: WebSocket) -> None:
    if not _browser_origin_is_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008, reason="Untrusted browser origin")
        return
    await websocket.accept()
    logger.info(f"WebSocket connected: {websocket.client}")
    try:
        while True:
            try:
                raw  = await websocket.receive_text()
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                await websocket.send_json({"error": f"Invalid JSON: {e}"})
                continue

            message        = str(data.get("message",    "")).strip()
            raw_tokens     = data.get("max_new_tokens")
            raw_temp       = data.get("temperature" )
            raw_top_p      = data.get("top_p")
            raw_top_k      = data.get("top_k")
            raw_rep_penalty = data.get("repetition_penalty")
            raw_do_sample  = data.get("do_sample")

            max_new_tokens = int(raw_tokens)   if raw_tokens is not None else None
            temperature    = float(raw_temp)   if raw_temp   is not None else None
            top_p          = float(raw_top_p)  if raw_top_p  is not None else None
            top_k          = int(raw_top_k)    if raw_top_k  is not None else None
            repetition_penalty = (
                float(raw_rep_penalty) if raw_rep_penalty is not None else None
            )
            do_sample = (
                raw_do_sample
                if isinstance(raw_do_sample, bool)
                else None
            )

            if not message:
                await websocket.send_json({"error": "message must not be empty"})
                continue
            if max_new_tokens is not None and not (1 <= max_new_tokens <= 2048):
                await websocket.send_json({"error": "max_new_tokens out of range"})
                continue
            if temperature is not None and not (0.0 < temperature <= 2.0):
                await websocket.send_json({"error": "temperature out of range"})
                continue
            if top_p is not None and not (0.0 < top_p <= 1.0):
                await websocket.send_json({"error": "top_p out of range"})
                continue
            if top_k is not None and not (0 <= top_k <= 50000):
                await websocket.send_json({"error": "top_k out of range"})
                continue
            if repetition_penalty is not None and not (0.1 <= repetition_penalty <= 5.0):
                await websocket.send_json({"error": "repetition_penalty out of range"})
                continue
            if raw_do_sample is not None and not isinstance(raw_do_sample, bool):
                await websocket.send_json({"error": "do_sample must be a boolean"})
                continue

            try:
                active_generator = await _require_generator_ready()
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                await websocket.send_json(
                    {
                        "error": "generator_route_not_ready",
                        "state": detail.get("state", "stopped"),
                        "message": detail.get(
                            "message",
                            "Generator route is not ready.",
                        ),
                    }
                )
                continue
            try:
                _authorize_free_chat(
                    active_generator,
                    prompt=message,
                    max_new_tokens=max_new_tokens,
                )
            except AccessError as exc:
                await websocket.send_json(
                    {"error": exc.code, "message": exc.message}
                )
                continue
            stream = active_generator.generate_stream(
                prompt=message,
                max_new_tokens=max_new_tokens,
                temperature=temperature,
                top_p=top_p,
                top_k=top_k,
                repetition_penalty=repetition_penalty,
                do_sample=do_sample,
            )
            async with aclosing(stream):
                async for chunk in stream:
                    if "error" in chunk:
                        diagnostic = _record_generation_failure(
                            active_generator,
                            str(chunk["error"]),
                        )
                        await websocket.send_json({**chunk, "diagnostic": diagnostic})
                    else:
                        await websocket.send_json(chunk)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
