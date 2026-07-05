"""
server.py — FastAPI backend for DistribLLM.

New in this version:
    GET /models  — returns the validated list of supported models
                   from constants.py. Frontend uses this to build
                   the model dropdown instead of free text input.
"""

import asyncio
import json
import sys
from contextlib import asynccontextmanager
from typing import Optional

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


# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

gpu_monitor: Optional[GPUMonitor]           = None
node:        Optional[Node]                 = None
generator:   Optional[DistributedGenerator] = None
client_dht:  Optional[hivemind.DHT]         = None
client_dht_prefix: str = DHT_PREFIX


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
    if node        is not None: node.stop()
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
    return {
        "status":          "online",
        "node_running":    node.is_running()     if node      else False,
        "node_info":       node.get_info()        if node      else None,
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
    dht = (node.dht if node is not None else None) or client_dht
    if dht is None:
        return {"nodes": [], "warning": "No DHT connection yet."}
    try:
        active_prefix = (
            getattr(node, "dht_prefix", None)
            if node is not None
            else client_dht_prefix
        ) or DHT_PREFIX
        active_model = (
            getattr(node, "model_name", None)
            if node is not None
            else (generator.model_name if generator is not None else None)
        )
        seq = RemoteSequential(
            dht=dht,
            dht_prefix=active_prefix,
            num_layers=0,
            model_name=active_model,
        )
        status = seq.get_network_status()
        return {"nodes": status["nodes"]}
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
    models = []
    for model_id, info in SUPPORTED_MODELS.items():
        models.append({
            "id":           model_id,
            "num_layers":   info["num_layers"],
            "hidden_size":  info["hidden_size"],
            "gated":        info["gated"],
            "description":  info["description"],
            "vram_gb":      info["vram_gb"],
            # Can this model be used right now?
            "available":    not info["gated"] or token_available,
        })
    return {
        "models":          models,
        "token_available": token_available,
        "default_peers":   DISTRIBLLM_INITIAL_PEERS,
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
    global node

    if node is not None and node.is_running():
        return {"status": "already_running", "info": node.get_info()}

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
        node = Node(
            model_name=req.model_name,
            layer_start=req.layer_start,
            layer_end=req.layer_end,
            dht_prefix=req.dht_prefix,
            initial_peers=peers,
            device=req.device,
            hf_token=hf_token,
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, node.start)
        if not node.is_running():
            raise RuntimeError("node.start() completed but is_running() is False")
        return {"status": "started", "info": node.get_info()}

    except AssertionError as e:
        node = None
        return {"status": "error", "error": str(e)}
    except Exception as e:
        logger.error(f"Node start failed: {e}", exc_info=True)
        node = None
        return {"status": "error", "error": str(e)}


@app.post("/node/stop")
async def stop_node() -> dict:
    global node
    if node is None or not node.is_running():
        return {"status": "not_running"}
    try:
        node.stop()
        node = None
        return {"status": "stopped"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


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
        node_trace = [
            f"{item['peer_id'][:8]}… (layers {item['layer_start']}→{item['layer_end']})"
            for item in route
        ]
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
        top_p=req.top_p
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

            max_new_tokens = int(raw_tokens)   if raw_tokens is not None else None
            temperature    = float(raw_temp)   if raw_temp   is not None else None
            top_p          = float(raw_top_p)  if raw_top_p  is not None else None

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

            if generator is not None and generator.is_loaded():
                async for chunk in generator.generate_stream(
                    prompt=message,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature,
                    top_p=top_p,
                ):
                    await websocket.send_json(chunk)
            else:
                await _mock_stream(websocket, message)

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {websocket.client}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json({"error": str(e)})
        except Exception:
            pass


async def _mock_stream(websocket: WebSocket, message: str) -> None:
    response = (
        "Generator not connected yet. "
        "Go to the Network page, start a node, then start the generator. "
        f"Your message was: '{message}'"
    )
    for word in response.split():
        await websocket.send_json({"token": word + " "})
        await asyncio.sleep(0.04)
    await websocket.send_json({"done": True, "node_trace": []})
