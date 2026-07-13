# Current Architecture

## System Shape

DistribLLM is an Electron desktop client backed by a local FastAPI process. The backend can host one or more non-overlapping layer slices for the same model and DHT prefix, run one generator, expose monitoring/status APIs, and manage local Hugging Face authentication and model imports.

The target network is a project-owned public/discoverable swarm. External devices join through DistribLLM bootstrap peers, but `use_ipfs=False` keeps the system isolated from public Petals/IPFS infrastructure.

## Runtime Roles

- Bootstrap node: stable discovery entry point only; never serves transformer layers.
- Serving node: owns a contiguous model layer range and exposes it through Hivemind RPC.
- Generator client: keeps tokenizer, embeddings, final normalization, and LM head locally, then routes hidden states through serving nodes.
- Electron/FastAPI client: controls local serving, generator startup, inference, monitoring, settings, OAuth, and managed model downloads.
- Headless worker: `backend/colab_worker.py` runs a serving node on Colab or another GPU host without Electron.

## Frontend

The renderer has five pages:

- Nodes: local hardware state and discovered/local nodes.
- Network: serve layers, manage local replicas, connect Hugging Face, download/import gated models, and start the generator.
- Inference: prompt streaming, readiness, route trace, and cancellation.
- Monitoring: route coverage, peers, and accounting/health information.
- Settings: backend and Hugging Face connection/local-model state.

Bootstrap configuration is intentionally hidden from normal product workflow. The backend URL defaults to `http://127.0.0.1:8000`; Vite overrides use `VITE_API_BASE_URL` and `VITE_WS_BASE_URL`.

## Backend State

`backend/api/server.py` currently owns process-level state:

```python
gpu_monitor: Optional[GPUMonitor]
local_nodes: dict[str, Node]
generator: Optional[DistributedGenerator]
client_dht: Optional[hivemind.DHT]
client_dht_prefix: str
```

One backend can serve multiple non-overlapping slices, but all local slices must use one model-compatible DHT prefix. Only one generator is active per backend process. True multi-machine testing uses separate backend/worker processes.

## Bootstrap Configuration

`backend/bootstrap.py` binds a stable identity and prints loopback/public multiaddresses. Remote clients must use the public address. Runtime peers come from the comma- or newline-separated `DISTRIBLLM_INITIAL_PEERS` environment value, with development defaults in `backend/constants.py`.

The stable `bootstrap.id` is a private identity file. It must not be committed or shared. A VPS bootstrap should run under a persistent process manager and expose its TCP port through both host and provider firewalls.

## Model Registry and Access

`backend/constants.py` is the supported-model registry. Each model declares layer count, hidden size, gated status, tuning type, VRAM estimate, description, and generation defaults.

Current model classes include:

- OPT base models for small transport/parity tests.
- TinyLlama chat for open instruction-ready smoke testing.
- Llama 2 base/chat variants for approved gated testing.
- Llama 3.2 and Mistral gated entries where account approval still applies.

Public models load explicitly with `token=False`; stored OAuth state cannot break anonymous access. Gated downloads use Hugging Face browser/device OAuth, then validate and register the downloaded snapshot. Runtime serving uses the validated local path with `local_files_only=True` and no token. Manual folder import remains the privacy-first fallback.

OAuth credentials are stored outside the repository by default under the user configuration directory. Raw tokens are not returned to the frontend, logs, traces, or normal API responses.

## Serving Lifecycle

`Node.start()` performs:

1. Connect to the bootstrap/DHT.
2. Load the selected model and retain the requested layer range.
3. Start a Hivemind RPC expert.
4. Announce validated metadata and periodically refresh it.

Nodes support pause, resume, and delete/unload as distinct operations. Failed startup calls cleanup so partial DHT, RPC, handler, and CUDA state are released.

Current layer-loading limitation: Transformers constructs the complete model in CPU memory before DistribLLM retains the assigned layers. Layer slicing reduces final device memory, but not peak download/CPU-loading memory.

## Routing and Generation

`RemoteSequential` validates DHT metadata, filters by model, builds a contiguous non-overlapping route, rejects gaps/incompatible ranges, and calls selected RPC experts in layer order.

When serving and generating on the same machine, generator startup directly seeds matching local node multiaddresses alongside configured bootstrap peers. Readiness resolves every selected expert and probes RPC metadata so DHT coverage alone cannot produce a false-ready state.

`DistributedGenerator` loads local model components and performs autoregressive generation through that route. It supports exact generation controls, stop requests, route readiness, next-token parity probes, generated-output comparisons, and JSON trace artifacts.

The current data plane is:

```text
token ids
  -> local embeddings / architecture preparation
  -> remote contiguous transformer-layer route
  -> local final normalization and LM head
  -> token selection
```

There is no distributed KV cache, stable session routing, failover, or concurrent generator registry yet. Each token can still process the full sequence, so performance is prototype-grade.

## Current Validation State

- Backend regression suite: 96 tests plus 19 subtests passing as of 2026-07-13.
- Frontend TypeScript typecheck and Python compilation pass.
- Local OPT-125M and OPT-1.3B smoke/parity evidence exists.
- Hugging Face device OAuth and real gated Llama 2 download have been exercised.
- TinyLlama live retry remains pending after Sprint 12 isolated public loading from expired OAuth state.
- A complete laptop plus VPS/Colab Llama 2 route and generated response remain unproven.

## Known Operational Limits

- Colab sessions are temporary, may lack GPU/high RAM, and may block inbound peer RPC behind NAT.
- VPS workers need enough RAM for full-model construction and reachable worker RPC ports or relay support.
- Python 3.12 is the supported runtime; Hivemind/Pydantic compatibility is unreliable on Python 3.14.
- Bootstrap reachability proves discovery transport only, not model-worker RPC reachability.
- Real incentives, API keys, failover, anti-abuse proofs, and distributed training remain future work.
