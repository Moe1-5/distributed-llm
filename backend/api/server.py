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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import hivemind
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from hivemind.utils.logging import get_logger
from pydantic import BaseModel, field_validator

from node.gpu_monitor import GPUMonitor
from node.node import Node
from client.sequential import RemoteSequential
from client.generation import DistributedGenerator
from api.settings import get_hf_token, save_hf_token, delete_hf_token, token_is_set
from constants import SUPPORTED_MODELS, DISTRIBLLM_INITIAL_PEERS, DHT_PREFIX

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


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

gpu_monitor: Optional[GPUMonitor]           = None
node:        Optional[Node]                 = None
local_nodes: dict[str, Node]                = {}
generator:   Optional[DistributedGenerator] = None
client_dht:  Optional[hivemind.DHT]         = None
client_dht_prefix: str = DHT_PREFIX


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
        if local_node.dht is not None:
            return local_node.dht
    return None


def _active_dht_prefix() -> str:
    for local_node in _local_node_list():
        return local_node.dht_prefix
    return client_dht_prefix or DHT_PREFIX


def _active_model_name() -> Optional[str]:
    models = {
        model_name
        for model_name in (getattr(local_node, "model_name", None) for local_node in _local_node_list())
        if model_name is not None
    }
    if len(models) == 1:
        return next(iter(models))
    return generator.model_name if generator is not None else None


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
        if req.layer_start < local_node.layer_end and req.layer_end > local_node.layer_start:
            return local_node
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gpu_monitor
    _validate_environment()
    gpu_monitor = GPUMonitor(interval=2.0)
    gpu_monitor.start()
    logger.info("GPU monitor started.")
    yield
    logger.info("Shutting down...")
    if gpu_monitor is not None: gpu_monitor.stop()
    for local_node in _local_node_list():
        local_node.stop()
    local_nodes.clear()
    _sync_primary_node()
    if client_dht  is not None: client_dht.shutdown()
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
        if v is not None:
            if not (1 <= v <= 2048):
                raise ValueError("max_new_tokens must be between 1 and 2048")
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


# ---------------------------------------------------------------------------
# Status & stats
# ---------------------------------------------------------------------------

@app.get("/status")
async def get_status() -> dict:
    local_infos = _local_node_infos()
    return {
        "status":          "online",
        "node_running":    any(local_node.is_running() for local_node in _local_node_list()),
        "node_info":       local_infos[0] if local_infos else None,
        "node_infos":      local_infos,
        "local_node_ids":  [info["node_id"] for info in local_infos if info.get("node_id")],
        "gpu_available":   torch.cuda.is_available(),
        "generator_ready": generator.is_loaded() if generator else False,
        "token_set":       token_is_set(),
    }


@app.get("/stats")
async def get_stats() -> dict:
    if gpu_monitor is None:
        raise HTTPException(status_code=503, detail="GPU monitor not ready.")
    return gpu_monitor.get_stats()


@app.get("/nodes")
async def get_nodes() -> dict:
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
            model_name=_active_model_name(),
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


@app.get("/models")
async def get_models() -> dict:
    """
    Return the validated list of supported models.
    Frontend uses this to build the model dropdown.
    Also returns whether each model needs a token, so the
    frontend can show the HuggingFace redirect prompt.
    """
    token_available = token_is_set()
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
            "description":  info["description"],
            "vram_gb":      info["vram_gb"],
            # Can this model be used right now?
            "available":    not info["gated"] or token_available,
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
        "default_peers":   DISTRIBLLM_INITIAL_PEERS,
    }


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
# Simulated contribution accounting
# ---------------------------------------------------------------------------

@app.get("/incentives/accounting")
async def get_incentive_accounting() -> dict:
    """
    Return simulated contribution accounting only.
    This intentionally does not expose balances, token claims, or payout actions.
    """
    local_contributions = [
        local_node.get_accounting_snapshot() for local_node in _local_node_list()
    ]
    return {
        "mode": "simulated",
        "token_ui_enabled": False,
        "reward_settlement_enabled": False,
        "policy": (
            "Initial accounting is model-aware and contribution-aware only. "
            "No real token rewards are issued until route correctness, health checks, "
            "and anti-abuse validation are proven."
        ),
        "fields": [
            "peer_id",
            "model_name",
            "layer_start",
            "layer_end",
            "layers_served",
            "device",
            "requests_served",
            "failed_requests",
            "token_positions_served",
            "total_latency_ms",
            "avg_latency_ms",
            "last_success_at",
            "last_error_at",
        ],
        "local_contribution": local_contributions[0] if local_contributions else None,
        "local_contributions": local_contributions,
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/settings")
async def get_settings() -> dict:
    token = get_hf_token()
    return {
        "token_set":     token is not None,
        "token_preview": f"{token[:8]}..." if token else None,
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


# ---------------------------------------------------------------------------
# Node management
# ---------------------------------------------------------------------------

@app.post("/node/start")
async def start_node(req: NodeStartRequest) -> dict:
    for local_node in _local_node_list():
        if (
            local_node.model_name == req.model_name
            and local_node.layer_start == req.layer_start
            and local_node.layer_end == req.layer_end
            and local_node.dht_prefix == req.dht_prefix
            and local_node.device == req.device
        ):
            if local_node.is_running():
                return {"status": "already_running", "info": local_node.get_info()}
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, local_node.start)
                return {"status": "resumed", "info": local_node.get_info()}
            except Exception as e:
                logger.error(f"Node resume failed: {e}", exc_info=True)
                return {"status": "error", "error": str(e)}

    existing_models = {local_node.model_name for local_node in _local_node_list()}
    if existing_models and req.model_name not in existing_models:
        return {
            "status": "error",
            "error": "multi_model_local_nodes_not_supported",
            "message": (
                "This backend currently serves multiple local nodes only for one model. "
                "Delete existing local nodes before serving a different model."
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

    overlapping_node = _has_overlapping_local_node(req)
    if overlapping_node is not None:
        if overlapping_node.layer_start == req.layer_start and overlapping_node.layer_end == req.layer_end:
            return {
                "status": "error",
                "error": "duplicate_layer_replicas_not_supported",
                "message": (
                    "This backend does not yet support duplicate local replicas "
                    "for the same model layer range."
                ),
            }
        return {
            "status": "error",
            "error": "overlapping_layer_range",
            "message": (
                f"Requested layers {req.layer_start}-{req.layer_end} overlap "
                f"existing local node {overlapping_node.layer_start}-{overlapping_node.layer_end}. "
                "Use a non-overlapping slice."
            ),
        }

    # Check token for gated models
    model_info = SUPPORTED_MODELS[req.model_name]
    hf_token   = get_hf_token()

    if model_info["gated"] and not hf_token:
        return {
            "status": "error",
            "error":  "gated_model_no_token",
            "message": (
                f"{req.model_name} is a gated model. "
                f"Please add your HuggingFace token in Settings first."
            ),
        }

    # Use default peers if none provided
    peers = req.initial_peers or DISTRIBLLM_INITIAL_PEERS

    try:
        local_node = Node(
            model_name=req.model_name,
            layer_start=req.layer_start,
            layer_end=req.layer_end,
            dht_prefix=req.dht_prefix,
            initial_peers=peers,
            device=req.device,
            hf_token=hf_token,
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, local_node.start)
        if not local_node.is_running():
            raise RuntimeError("node.start() completed but is_running() is False")
        _register_local_node(local_node)
        return {"status": "started", "info": local_node.get_info()}

    except AssertionError as e:
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"Node start failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


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
        return {"status": "turned_on", "info": local_node.get_info()}
    except Exception as e:
        logger.error(f"Node turn-on failed: {e}", exc_info=True)
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
        local_node.turn_off()
        return {"status": "turned_off", "info": local_node.get_info()}
    except Exception as e:
        logger.error(f"Node turn-off failed: {e}", exc_info=True)
        return {"status": "error", "error": str(e)}


@app.delete("/node")
async def delete_node(node_id: Optional[str] = None) -> dict:
    try:
        local_node = _find_local_node(node_id)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    if local_node is None:
        return {"status": "not_found"}
    try:
        local_node.stop()
        _unregister_local_node(local_node)
        return {"status": "deleted"}
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

    model_info = SUPPORTED_MODELS[req.model_name]
    hf_token   = get_hf_token()

    if model_info["gated"] and not hf_token:
        return {
            "status": "error",
            "error":  "gated_model_no_token",
            "message": f"{req.model_name} requires a HuggingFace token.",
        }

    peers = req.initial_peers or DISTRIBLLM_INITIAL_PEERS

    try:
        if client_dht is not None:
            client_dht.shutdown()
            client_dht = None

        client_dht = hivemind.DHT(
            initial_peers=peers,
            start=True,
            use_ipfs=False,
        )
        if client_dht.peer_id is None:
            raise RuntimeError("Generator DHT started but peer_id is None")
        client_dht_prefix = req.dht_prefix

        sequential = RemoteSequential(
            dht=client_dht,
            dht_prefix=req.dht_prefix,
            num_layers=model_info["num_layers"],
            model_name=req.model_name,
        )

        generator = DistributedGenerator(
            model_name=req.model_name,
            sequential=sequential,
            hf_token=hf_token,
            device="cuda"if torch.cuda.is_available() else "cpu",
            dtype= torch.float16 if torch.cuda.is_available() else torch.float32,
        )

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, generator.load)
        if not generator.is_loaded():
            raise RuntimeError("generator.load() completed but is_loaded() is False")

        return {"status": "ready"}

    except AssertionError as e:
        generator = None; client_dht = None
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"Generator start failed: {e}", exc_info=True)
        generator = None; client_dht = None
        return {"status": "error", "error": str(e)}


@app.get("/generator/status")
async def get_generator_status() -> dict:
    if generator is None or not generator.is_loaded():
        return {
            "ready": False,
            "model_name": None,
            "route_ready": False,
            "reasons": ["Generator not loaded."],
            "node_trace": [],
        }

    reasons: list[str] = []
    node_trace: list[str] = []
    route_ready = False

    try:
        route = generator.sequential.validate_route()
        route_ready = True
        node_trace = _format_route_trace(route)
    except Exception as e:
        reasons.append(str(e))

    return {
        "ready": generator.is_loaded() and route_ready,
        "model_name": generator.model_name,
        "route_ready": route_ready,
        "reasons": reasons,
        "node_trace": node_trace,
    }


@app.post("/generator/stop")
async def stop_generator() -> dict:
    if generator is None or not generator.is_loaded():
        return {"status": "not_running"}
    generator.request_stop()
    return {"status": "stop_requested"}


@app.post("/generator/parity/next-token")
async def compare_generator_next_token(req: NextTokenParityRequest) -> dict:
    if generator is None or not generator.is_loaded():
        raise HTTPException(status_code=503, detail="Generator not ready.")
    try:
        return generator.compare_next_token_logits(
            prompt=req.prompt,
            atol=req.atol,
            rtol=req.rtol,
        )
    except Exception as e:
        logger.error(f"Next-token parity check failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/generator/parity/generate")
async def compare_generator_output(req: GeneratedParityRequest) -> dict:
    if generator is None or not generator.is_loaded():
        raise HTTPException(status_code=503, detail="Generator not ready.")
    try:
        return await generator.compare_generated_output(
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
    if generator is None or not generator.is_loaded():
        raise HTTPException(status_code=503, detail="Generator not ready.")
    try:
        trace = generator.trace_generation(
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


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

@app.post("/chat")
async def chat(req: ChatRequest) -> dict:
    if generator is None or not generator.is_loaded():
        raise HTTPException(status_code=503, detail="Generator not ready.")

    full_response         = ""
    node_trace: list[str] = []

    async for chunk in generator.generate_stream(
        prompt=req.message,
        max_new_tokens=req.max_new_tokens,
        temperature=req.temperature,
        top_p=req.top_p,
        top_k=req.top_k,
        repetition_penalty=req.repetition_penalty,
        do_sample=req.do_sample,
    ):
        if "token"  in chunk: full_response += chunk["token"]
        elif "done" in chunk: node_trace = chunk.get("node_trace", [])
        elif "error" in chunk:
            raise HTTPException(status_code=500, detail=chunk["error"])

    return {
        "response":         full_response,
        "node_trace":       node_trace,
        "tokens_generated": len(full_response.split()),
    }


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

            if generator is not None and generator.is_loaded():
                async for chunk in generator.generate_stream(
                    prompt=message,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    top_k=top_k,
                    repetition_penalty=repetition_penalty,
                    do_sample=do_sample,
                ):
                    await websocket.send_json(chunk)
            else:
                await websocket.send_json({"error": "Generator not ready."})

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass
