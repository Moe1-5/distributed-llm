"""
server.py — FastAPI backend for DistribLLM.

New in this version:
    GET /models  — returns the validated list of supported models
                   from constants.py. Frontend uses this to build
                   the model dropdown instead of free text input.
"""

import asyncio
import json
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse
from uuid import uuid4

import hivemind
import torch
from fastapi import FastAPI, Header, Request, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
from api.lifecycle_jobs import LifecycleJobStore
from api.runtime_state import RuntimeStateStore
from node.gpu_monitor import GPUMonitor
from node.node import Node
from node.rpc_server import DEFAULT_SHUTDOWN_TIMEOUT_SECONDS, _run_with_timeout
from client.sequential import RemoteSequential, shutdown_remote_expert_p2p
from client.coverage import build_serving_plan, evaluate_candidate
from client.generation import DistributedGenerator
from incentives.runtime import get_useful_work_runtime
from incentives.access import AccessError, get_api_access_manager
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
)

load_project_env()

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


def _cleanup_failed_node(candidate: object | None) -> None:
    if candidate is None or not hasattr(candidate, "stop"):
        return
    try:
        candidate.stop()
    except Exception as exc:
        logger.warning("Failed to clean up partially started node: %s", exc)


def _cleanup_failed_generator(candidate: object | None) -> None:
    if candidate is not None and hasattr(candidate, "unload"):
        try:
            candidate.unload()
        except Exception as exc:
            logger.warning("Failed to clean up partially loaded generator: %s", exc)
    torch.cuda.empty_cache()


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
        local_nodes.pop(node_id, None)
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


def _local_node_key(info: dict) -> str:
    node_id = info.get("node_id")
    if node_id:
        return f"node:{node_id}"
    return (
        f"peer:{info.get('peer_id')}:{info.get('model_name')}:"
        f"{info.get('layer_start')}:{info.get('layer_end')}"
    )


def _local_node_infos() -> list[dict]:
    return [local_node.get_info() for local_node in _local_node_list()]


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
    if (
        generator is None
        or not generator.is_loaded()
        or generator.model_name != local_node.model_name
    ):
        return {"required": False, "alternate_available": False}
    health_getter = getattr(generator.sequential, "get_health_readiness", None)
    if not callable(health_getter):
        return {"required": True, "alternate_available": False}
    health = health_getter()
    selected_route = health.get("selected_route", [])
    if not _route_contains_local_node(selected_route, local_node):
        return {"required": False, "alternate_available": False, "health": health}
    alternate_available = any(
        not _route_contains_local_node(candidate.get("route", []), local_node)
        for candidate in health.get("alternate_routes", [])
    )
    return {
        "required": not alternate_available,
        "alternate_available": alternate_available,
        "health": health,
    }


async def _unload_generator_runtime(reason: str) -> dict:
    global generator
    if generator is None:
        _runtime_state.transition_generator(
            "stopped",
            model_name=None,
            components_loaded=False,
            route_ready=False,
            reasons=[reason],
        )
        return {"status": "not_running"}
    candidate = generator
    generator = None
    _runtime_state.transition_generator(
        "stopping",
        model_name=getattr(candidate, "model_name", None),
        route_ready=False,
        reasons=[reason],
    )
    candidate.request_stop()
    await asyncio.to_thread(candidate.unload)
    network = await asyncio.to_thread(_shutdown_client_dht)
    _runtime_state.transition_generator(
        "stopped",
        model_name=None,
        components_loaded=False,
        route_ready=False,
        reasons=[reason],
    )
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
    return {
        "initial_peers": initial_peers,
        "start": True,
        "use_ipfs": False,
        "use_relay": True,
        "client_mode": True,
    }


def _shutdown_local_nodes(
    timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> list[dict]:
    results: list[dict] = []
    for local_node in list(_local_node_list()):
        node_id = getattr(local_node, "node_id", None)

        def _stop_node() -> None:
            try:
                local_node.stop(timeout=timeout)
            except TypeError:
                local_node.stop()

        finished = _run_with_timeout(
            f"local-node-stop-{node_id or 'unknown'}",
            _stop_node,
            timeout,
        )
        if finished:
            _unregister_local_node(local_node)
        results.append(
            {
                "node_id": node_id,
                "status": "stopped" if finished else "timeout",
            }
        )
    local_nodes.clear()
    _sync_primary_node()
    return results


def _shutdown_client_dht(
    timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
) -> Optional[dict]:
    global client_dht
    if client_dht is None:
        return None
    dht = client_dht
    client_dht = None
    cleanup = {"remote_expert_p2p": "not_started"}

    def _shutdown() -> None:
        cleanup["remote_expert_p2p"] = (
            "stopped" if shutdown_remote_expert_p2p(dht) else "error"
        )
        dht.shutdown()

    finished = _run_with_timeout("client-dht-shutdown", _shutdown, timeout)
    return {
        "status": "stopped" if finished else "timeout",
        **cleanup,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gpu_monitor, generator
    _validate_environment()
    gpu_monitor = GPUMonitor(interval=2.0)
    gpu_monitor.start()
    logger.info("GPU monitor started.")
    yield
    logger.info("Shutting down...")
    _lifecycle_jobs.cancel_all()
    if gpu_monitor is not None: gpu_monitor.stop()
    node_shutdown_results = _shutdown_local_nodes()
    _cleanup_failed_generator(generator)
    generator = None
    client_shutdown_result = _shutdown_client_dht()
    _runtime_state.transition_generator(
        "stopped",
        model_name=None,
        components_loaded=False,
        route_ready=False,
        reasons=["Backend shutdown completed."],
    )
    logger.info(
        "Shutdown cleanup status | local_nodes=%s client_dht=%s",
        node_shutdown_results,
        client_shutdown_result,
    )
    logger.info("Shutdown complete.")


app = FastAPI(
    title="DistribLLM API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
    }


@app.get("/stats")
async def get_stats() -> dict:
    if gpu_monitor is None:
        raise HTTPException(status_code=503, detail="GPU monitor not ready.")
    return gpu_monitor.get_stats()


@app.get("/nodes")
async def get_nodes() -> dict:
    global _nodes_refresh_task
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


@app.get("/models/catalog")
async def get_model_catalog() -> dict:
    """Return local model choices without waiting for any DHT route scan."""
    token_available = token_is_set()
    local_imports = {
        record["model_name"]: record
        for record in list_local_models()
        if record.get("model_name")
    }
    models = []
    for model_id, info in SUPPORTED_MODELS.items():
        total_layers = int(info["num_layers"])
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
    dht = _active_local_dht() or client_dht
    active_prefix = _active_dht_prefix()
    models = []
    for model_id, info in SUPPORTED_MODELS.items():
        route_status = _get_model_route_status(
            model_id=model_id,
            model_info=info,
            dht=dht,
            dht_prefix=active_prefix,
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
        })
    return {
        "models":          models,
        "token_available": token_available,
        "default_peers":   get_initial_peers(),
    }


def _active_serving_nodes(model_id: str, dht_prefix: Optional[str] = None) -> list[dict]:
    model_info = SUPPORTED_MODELS[model_id]
    total_layers = int(model_info["num_layers"])
    active_prefix = dht_prefix or _active_dht_prefix()
    dht = _active_local_dht() or client_dht
    discovered: list[dict] = []
    sequential = RemoteSequential(
        dht=dht if dht is not None else object(),
        dht_prefix=active_prefix,
        num_layers=total_layers,
        model_name=model_id,
    )
    if dht is not None:
        try:
            discovered = sequential.get_network_status()["nodes"]
        except Exception as exc:
            logger.warning("Coverage discovery failed for %s: %s", model_id, exc)

    discovered_keys = {_local_node_key(info) for info in discovered}
    local_infos = [
        info
        for info in _local_node_infos()
        if info.get("model_name") == model_id
        and _local_node_key(info) not in discovered_keys
    ]
    candidates = [*discovered, *local_infos]
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
    key = (model_id, layer_count)
    now = time.monotonic()
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

    if dht is None:
        return {
            **empty_status,
            "reasons": ["No DHT connection yet."],
        }

    seq = RemoteSequential(
        dht=dht,
        dht_prefix=dht_prefix,
        num_layers=total_layers,
        model_name=model_id,
    )
    try:
        network_status = seq.get_network_status()
        nodes = network_status["nodes"]
        route = seq.validate_route(nodes)
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
            network_status = seq.get_network_status()
            nodes = network_status["nodes"]
            return {
                **empty_status,
                "reasons": [str(e)],
                "covered_layers": network_status["covered_layers"],
                "missing_layers": network_status["missing_layers"],
                "compatible_nodes": len(nodes),
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
    if origin is None or origin in {"null", "file://"}:
        return
    parsed = urlparse(origin)
    if parsed.scheme in {"http", "https"} and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
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
    if replicas and not req.confirm_local_replica:
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

    serving_nodes = _active_serving_nodes(req.model_name, req.dht_prefix)
    fresh_plan = {
        **_build_model_serving_plan(
            req.model_name,
            req.layer_end - req.layer_start,
            req.dht_prefix,
            serving_nodes,
        ),
        "snapshot_stale": False,
        "refreshing": False,
        "snapshot_source": "validated_dht",
        "snapshot_age_seconds": 0.0,
    }
    if (
        req.coverage_revision is not None
        and req.coverage_revision != fresh_plan["coverage_revision"]
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "coverage_revision_stale",
                "message": "Layer coverage changed. Review the fresh serving plan.",
                "plan": fresh_plan,
            },
        )

    candidate = evaluate_candidate(
        serving_nodes,
        int(SUPPORTED_MODELS[req.model_name]["num_layers"]),
        req.layer_start,
        req.layer_end,
    )
    if (
        fresh_plan["missing_ranges"]
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
                "plan": fresh_plan,
            },
        )

    overlapping_node = _has_overlapping_local_node(req)
    if overlapping_node is not None:
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

    # Use default peers if none provided
    peers = req.initial_peers or get_initial_peers()
    rpc_uid_suffix = _next_rpc_uid_suffix(req)
    local_node = None

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
        )
        local_node.rpc_uid_suffix = rpc_uid_suffix
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, local_node.start)
        if not local_node.is_running():
            raise RuntimeError("node.start() completed but is_running() is False")
        _register_local_node(local_node)
        _invalidate_serving_plan_cache(req.model_name)
        _runtime_state.record_event(
            kind="node",
            phase="ready",
            status="info",
            message=(
                f"Node {local_node.node_id} is serving {req.model_name} "
                f"layers {req.layer_start}-{req.layer_end}."
            ),
            details=local_node.get_info(),
        )
        return {"status": "started", "info": local_node.get_info()}

    except AssertionError as e:
        _cleanup_failed_node(local_node)
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"Node start failed: {e}", exc_info=True)
        _cleanup_failed_node(local_node)
        if _is_cuda_out_of_memory(e):
            return _cuda_memory_error_response(
                req.model_name,
                layer_start=req.layer_start,
                layer_end=req.layer_end,
            )
        if _is_huggingface_auth_expired(e):
            return _huggingface_reconnect_response(req.model_name)
        return {"status": "error", "error": str(e)}


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
            result = asyncio.run(start_node(req))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            result = {"status": "error", **detail}
        if cancelled.is_set() and result.get("status") == "started":
            node_id = (result.get("info") or {}).get("node_id")
            asyncio.run(delete_node(node_id))
            return {"status": "cancelled"}
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
    if local_node.is_running():
        return {"status": "already_running", "info": local_node.get_info()}
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, local_node.start)
        _invalidate_serving_plan_cache(local_node.model_name)
        return {"status": "turned_on", "info": local_node.get_info()}
    except Exception as e:
        logger.error(f"Node turn-on failed: {e}", exc_info=True)
        if _is_cuda_out_of_memory(e):
            return _cuda_memory_error_response(
                local_node.model_name,
                layer_start=local_node.layer_start,
                layer_end=local_node.layer_end,
            )
        return {"status": "error", "error": str(e)}


@app.post("/node/turn-off")
async def turn_off_node(node_id: Optional[str] = None) -> dict:
    try:
        local_node = _find_local_node(node_id)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    if local_node is None:
        return {"status": "not_found"}
    if not local_node.is_running():
        return {"status": "already_off", "info": local_node.get_info()}
    try:
        dependency = _generator_dependency(local_node)
        if dependency.get("required") and generator is not None:
            generator.request_stop()
            _runtime_state.transition_generator(
                "suspended",
                model_name=generator.model_name,
                components_loaded=True,
                route_ready=False,
                reasons=[
                    "A required local serving node was turned off. "
                    "Restore complete RPC-healthy coverage before generating."
                ],
                health=dependency.get("health"),
            )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, local_node.turn_off)
        _invalidate_serving_plan_cache(local_node.model_name)
        _runtime_state.record_event(
            kind="node",
            phase="turned_off",
            status="info",
            message=f"Node {local_node.node_id} stopped serving.",
            details={"generator_suspended": bool(dependency.get("required"))},
        )
        return {"status": "turned_off", "info": local_node.get_info()}
    except Exception as e:
        logger.error(f"Node turn-off failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


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
        await asyncio.to_thread(local_node.stop)
        _unregister_local_node(local_node)
        _invalidate_serving_plan_cache(local_node.model_name)
        _runtime_state.record_event(
            kind="node",
            phase="deleted",
            status="info",
            message=f"Node {local_node.node_id} was deleted.",
            details={"generator": generator_result},
        )
        return {"status": "deleted", "generator": generator_result}
    except Exception as e:
        logger.error(f"Node delete failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


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
    global generator, client_dht, client_dht_prefix
    startup_started_at = time.perf_counter()

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
        req.initial_peers or get_initial_peers(),
        req.model_name,
        req.dht_prefix,
    )
    if generator is not None and generator.is_loaded():
        status = await get_generator_status()
        return {
            "status": "already_ready" if status["ready"] else "suspended",
            "message": (
                "Unload the current generator before starting another model."
                if generator.model_name != req.model_name
                else "The generator is already loaded."
            ),
            "generator": status,
        }

    operation_id = str(uuid4())
    _runtime_state.transition_generator(
        "starting",
        model_name=req.model_name,
        components_loaded=False,
        route_ready=False,
        reasons=["Starting the generator network client."],
    )
    sequential: Optional[RemoteSequential] = None
    try:
        _cleanup_failed_generator(generator)
        generator = None
        _shutdown_client_dht()

        client_dht = hivemind.DHT(**_generator_dht_kwargs(peers))
        if client_dht.peer_id is None:
            raise RuntimeError("Generator DHT started but peer_id is None")
        client_dht_prefix = req.dht_prefix

        sequential = RemoteSequential(
            dht=client_dht,
            dht_prefix=req.dht_prefix,
            num_layers=model_info["num_layers"],
            model_name=req.model_name,
        )

        start_health_monitor = getattr(sequential, "start_health_monitor", None)
        if callable(start_health_monitor):
            start_health_monitor()

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
                route = await asyncio.to_thread(route_validator)
                readiness = {"route_ready": True, "selected_route": route}
            else:
                readiness = {
                    "route_ready": True,
                    "selected_route": [],
                    "warnings": ["Legacy sequential implementation skipped route preflight."],
                }
            break

        canary_validator = getattr(sequential, "validate_tensor_route", None)
        if callable(canary_validator):
            canary = await asyncio.to_thread(
                canary_validator,
                int(model_info["hidden_size"]),
            )
        else:
            canary = {
                "ok": True,
                "skipped": True,
                "reason": "Legacy sequential implementation has no tensor canary.",
            }

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

        generator = DistributedGenerator(
            model_name=req.model_name,
            sequential=sequential,
            hf_token=None,
            local_model_path=local_model_path,
            device="cuda"if torch.cuda.is_available() else "cpu",
            dtype= torch.float16 if torch.cuda.is_available() else torch.float32,
        )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, generator.load)
        if not generator.is_loaded():
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

        startup_duration_ms = (time.perf_counter() - startup_started_at) * 1000
        set_startup_duration = getattr(generator, "set_startup_duration_ms", None)
        if callable(set_startup_duration):
            set_startup_duration(startup_duration_ms)
        _runtime_state.transition_generator(
            "ready",
            model_name=req.model_name,
            components_loaded=True,
            route_ready=True,
            reasons=[],
            node_trace=_format_route_trace(final_readiness.get("selected_route", [])),
            health=final_readiness,
            canary=canary,
        )
        _runtime_state.record_event(
            kind="generator",
            phase="ready",
            status="info",
            message=f"Generator for {req.model_name} passed route and tensor validation.",
            operation_id=operation_id,
            details={"startup_duration_ms": startup_duration_ms, "canary": canary},
        )
        return {
            "status": "ready",
            "route_ready": True,
            "canary": canary,
            "performance": (
                generator.get_performance_snapshot()
                if hasattr(generator, "get_performance_snapshot")
                else {"startup_duration_ms": startup_duration_ms}
            ),
        }

    except AssertionError as e:
        if sequential is not None:
            stop_health_monitor = getattr(sequential, "stop_health_monitor", None)
            if callable(stop_health_monitor):
                stop_health_monitor()
        _cleanup_failed_generator(generator)
        generator = None
        _shutdown_client_dht()
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
        if sequential is not None:
            stop_health_monitor = getattr(sequential, "stop_health_monitor", None)
            if callable(stop_health_monitor):
                stop_health_monitor()
        _cleanup_failed_generator(generator)
        generator = None
        _shutdown_client_dht()
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


@app.post("/generator/start-async", status_code=202)
async def start_generator_async(req: GeneratorStartRequest) -> dict:
    resource_key = f"generator:{req.dht_prefix}:{req.model_name}"

    def target(progress, cancelled) -> dict:
        progress("networking", "Starting the generator network client.")
        if cancelled.is_set():
            return {"status": "cancelled"}
        progress("validating_route", "Proving a complete RPC-healthy tensor route.")
        try:
            result = asyncio.run(start_generator(req))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            result = {"status": "error", **detail}
        if cancelled.is_set() and result.get("status") == "ready":
            asyncio.run(_unload_generator_runtime("Generator startup was cancelled."))
            return {"status": "cancelled"}
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
    if generator is None or not generator.is_loaded():
        snapshot = _runtime_state.generator_snapshot()
        if snapshot["state"] not in {"starting", "validating_route", "loading", "failed"}:
            snapshot = _runtime_state.transition_generator(
                "stopped",
                model_name=None,
                components_loaded=False,
                route_ready=False,
                reasons=["Generator not loaded."],
            )
        return {
            "ready": False,
            "state": snapshot["state"],
            "components_loaded": False,
            "model_name": snapshot.get("model_name"),
            "route_ready": False,
            "reasons": snapshot["reasons"],
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
        health_getter = getattr(generator.sequential, "get_health_readiness", None)
        if callable(health_getter):
            health = health_getter()
        if health is not None and health.get("enabled"):
            route = health.get("selected_route", [])
            route_ready = bool(health.get("route_ready"))
            reasons.extend(str(reason) for reason in health.get("reasons", []))
        else:
            route = await asyncio.to_thread(generator.sequential.validate_reachable_route)
            route_ready = True
        node_trace = _format_route_trace(route)
    except Exception as e:
        reasons.append(str(e))
    route_validation_ms = (
        time.perf_counter() - route_validation_started_at
    ) * 1000
    performance_getter = getattr(generator, "get_performance_snapshot", None)
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

    state = "ready" if route_ready else "suspended"
    snapshot = _runtime_state.transition_generator(
        state,
        model_name=generator.model_name,
        components_loaded=True,
        route_ready=route_ready,
        reasons=reasons,
        node_trace=node_trace,
        health=health,
    )

    return {
        "ready": generator.is_loaded() and route_ready,
        "state": state,
        "components_loaded": True,
        "model_name": generator.model_name,
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
        }
    )
    return snapshot


async def _require_generator_ready() -> DistributedGenerator:
    status = await get_generator_status()
    if not status["ready"] or generator is None:
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
    return generator


@app.post("/generator/stop")
async def stop_generator() -> dict:
    if generator is None or not generator.is_loaded():
        return {"status": "not_running"}
    generator.request_stop()
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
                async for chunk in active_generator.generate_stream(
                    prompt=prompt,
                    max_new_tokens=req.max_tokens,
                    temperature=req.temperature,
                    top_p=req.top_p,
                ):
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
            finally:
                if not settled:
                    active_generator.request_stop()
                    manager.store.release(reservation["request_id"])

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    response_text = ""
    metrics: dict = {}
    try:
        async for chunk in active_generator.generate_stream(
            prompt=prompt,
            max_new_tokens=req.max_tokens,
            temperature=req.temperature,
            top_p=req.top_p,
        ):
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

    async for chunk in active_generator.generate_stream(
        prompt=req.message,
        max_new_tokens=req.max_new_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
        top_k=req.top_k,
        repetition_penalty=req.repetition_penalty,
        do_sample=req.do_sample,
    ):
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

            generator_status = await get_generator_status()
            if generator_status["ready"] and generator is not None:
                try:
                    _authorize_free_chat(
                        generator,
                        prompt=message,
                        max_new_tokens=max_new_tokens,
                    )
                except AccessError as exc:
                    await websocket.send_json(
                        {"error": exc.code, "message": exc.message}
                    )
                    continue
                async for chunk in generator.generate_stream(
                    prompt=message,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    repetition_penalty=repetition_penalty,
                    do_sample=do_sample,
                ):
                    if "error" in chunk:
                        diagnostic = _record_generation_failure(
                            generator,
                            str(chunk["error"]),
                        )
                        await websocket.send_json({**chunk, "diagnostic": diagnostic})
                    else:
                        await websocket.send_json(chunk)
            else:
                await websocket.send_json(
                    {
                        "error": "generator_route_not_ready",
                        "state": generator_status["state"],
                        "message": "; ".join(generator_status["reasons"])
                        or "Generator route is not ready.",
                    }
                )

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
