# Repo Map

This map ignores generated folders such as `frontend/node_modules`, `frontend/out`, `backend/.venv`, and `__pycache__`.

## Backend

### Entry Points

- `backend/main.py`
  - Starts the FastAPI app with Uvicorn.
  - Default host is `127.0.0.1`, default port is `8000`.

- `backend/bootstrap.py`
  - Starts a stable Hivemind DHT bootstrap node.
  - Uses `identity_path` so the peer id remains stable across restarts.
  - The printed multiaddress is copied into `DISTRIBLLM_INITIAL_PEERS`.

### API Layer

- `backend/api/server.py`
  - Main FastAPI app.
  - Owns global process state: `gpu_monitor`, `node`, `generator`, `client_dht`.
  - Provides:
    - status and stats endpoints
    - model list endpoint
    - node start/stop endpoints
    - generator start endpoint
    - HTTP chat endpoint
    - WebSocket streaming endpoint

- `backend/api/settings.py`
  - Stores a HuggingFace token in `backend/.hf_token`.
  - Used for gated models.

### Configuration

- `backend/constants.py`
  - Bootstrap peers.
  - Supported model registry.
  - Model metadata: layer count, hidden size, gated flag, VRAM estimate, generation defaults.
  - DHT prefix and expiry settings.

### Node Serving

- `backend/node/node.py`
  - Represents a serving node.
  - Starts a DHT client.
  - Loads the configured transformer layer range.
  - Starts the Hivemind RPC server.
  - Announces metadata to the DHT under:
    - `{prefix}.node_info.{peer_id}`
    - `{prefix}.members`

- `backend/node/block_loader.py`
  - Downloads a HuggingFace causal LM.
  - Extracts only a range of decoder layers.
  - Supports common layout patterns such as Llama/Mistral-style `model.layers`, GPT-style `transformer.h`, and OPT-style `model.decoder.layers`.

- `backend/node/handler.py`
  - Wraps loaded layers and runs forward passes through them.
  - Receives hidden states from RPC.
  - Moves tensors to the node device and dtype.
  - Calls each local transformer layer in order.

- `backend/node/rpc_server.py`
  - Wraps `InferenceHandler` in an `nn.Module`.
  - Exposes it via `hivemind.moe.Server`.
  - Stores a Hivemind expert UID in the DHT metadata so clients can call it.

- `backend/node/gpu_monitor.py`
  - Background thread for CPU, RAM, GPU, and VRAM stats.
  - Used by the dashboard.

### Client Inference

- `backend/client/generation.py`
  - Loads local tokenizer, token embeddings, final norm, and LM head.
  - Performs autoregressive generation.
  - Calls `RemoteSequential.forward(...)` for the remote decoder layers.
  - Handles sampling: temperature, top-p, top-k, repetition penalty.

- `backend/client/sequential.py`
  - Discovers serving nodes through the DHT.
  - Checks layer coverage.
  - Sorts nodes by `layer_start`.
  - Calls each remote node sequentially through Hivemind `get_experts`.

- `backend/client/distributed_model.py`
  - Currently empty.
  - Good future home for a model-level wrapper or adapter facade.

### Model-Specific Code

- `backend/models/llama/*`
  - Currently empty placeholder files.
  - Good future home for architecture adapters.

## Frontend

### Electron Shell

- `frontend/src/main/index.ts`
  - Creates the Electron window.
  - Disables Chromium GPU rendering for WSL compatibility.
  - Hardcodes the backend origin in the CSP.

- `frontend/src/preload/index.ts`
  - Exposes Electron APIs to the renderer.

### Renderer App

- `frontend/src/renderer/src/App.tsx`
  - Page router with local React state.
  - Pages: dashboard, chat, network, settings.

- `frontend/src/renderer/src/api/client.ts`
  - Central HTTP/WebSocket client.
  - Currently hardcodes backend IP `172.27.32.227:8000`.

- `frontend/src/renderer/src/pages/Dashboard.tsx`
  - Polls backend status, stats, and discovered nodes.

- `frontend/src/renderer/src/pages/Network.tsx`
  - UI for starting/stopping a serving node.
  - UI for starting the generator.
  - Shows model selection and bootstrap peer fields.

- `frontend/src/renderer/src/pages/Chat.tsx`
  - Inference tab.
  - Opens a WebSocket lazily on first message.
  - Streams tokens into the last assistant message.

- `frontend/src/renderer/src/pages/Settings.tsx`
  - Stores and deletes the HuggingFace token through the backend.

- `frontend/src/renderer/src/components/Sidebar.tsx`
  - Main navigation.

## Notable Generated or Local Artifacts

- `backend/.venv/`
- `frontend/node_modules/`
- `frontend/out/`
- `backend/__pycache__/`
- `backend/cuda-keyring_1.1-1_all.deb`
- `backend/cuda-keyring_1.1-1_all.deb.1`

These should generally not be part of architectural reasoning or committed source changes.

