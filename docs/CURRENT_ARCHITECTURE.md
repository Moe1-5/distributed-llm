# Current Architecture

## System Shape

DistribLLM is an Electron desktop client backed by a local FastAPI process. The backend can host one or more non-overlapping local layer slices for the same model and DHT prefix, run one generator, expose monitoring/status APIs, manage local Hugging Face authentication and model imports, and optionally produce useful-work receipts for selected inference hops.

The target network is a project-owned public/discoverable swarm. External devices join through DistribLLM bootstrap peers, but `use_ipfs=False` keeps the system isolated from public Petals/IPFS infrastructure.

## Runtime Roles

- Bootstrap node: stable discovery entry point only; never serves transformer layers.
- Serving node: owns a contiguous model layer range and exposes legacy plus optional receipt-capable Hivemind RPC.
- Generator client: keeps tokenizer, embeddings, final normalization, and LM head locally, then routes hidden states through serving nodes.
- Electron/FastAPI client: controls local serving, generator startup, inference, monitoring, settings, OAuth, and managed model downloads.
- Headless worker: `backend/colab_worker.py` runs a serving node on Colab or another GPU host without Electron.

## Frontend

The renderer has six pages:

- Nodes: local hardware state and discovered/local nodes.
- Network: serve layers, manage local replicas, connect Hugging Face, download/import gated models, and start the generator.
- Inference: prompt streaming, readiness, route trace, and cancellation.
- Monitoring: route coverage, peers, and accounting/health information.
- Incentives: app identity, settlement status, verified credits, receipt outcomes, and local useful-work counters.
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

`backend/bootstrap.py` binds a stable identity, enables Hivemind/libp2p circuit-relay support, hosts the independent direct-reachability check protocol, and prints loopback/public multiaddresses. Remote clients must use the public address. Runtime peers come from the comma- or newline-separated `DISTRIBLLM_INITIAL_PEERS` environment value, with development defaults in `backend/constants.py`.

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

1. Probe direct reachability with relay disabled when network mode is `auto`.
2. Start a direct DHT peer or reserve an outbound circuit-relay path.
3. Load the selected model and retain the requested layer range.
4. Start a Hivemind RPC expert.
5. Announce validated model and transport metadata and periodically refresh it.

Nodes support pause, resume, and delete/unload as distinct operations. Failed startup calls cleanup so partial DHT, RPC, handler, and CUDA state are released.

`DISTRIBLLM_NETWORK_MODE`, `DISTRIBLLM_P2P_PORT`, `DISTRIBLLM_ANNOUNCE_MADDRS`, and the relay settings control transport. Direct Windows/WSL operation requires a fixed port, a Windows LAN/public announce address, and mirrored networking or Windows port forwarding. Auto mode falls back to an outbound relay reservation and does not require the ordinary participant to expose a public router port.

Current layer-loading limitation: Transformers constructs the complete model in CPU memory before DistribLLM retains the assigned layers. Layer slicing reduces final device memory, but not peak download/CPU-loading memory.

## Routing and Generation

`RemoteSequential` validates DHT metadata, filters by model, and uses dynamic programming to select a complete contiguous non-overlapping subset. It minimizes hop count, round-robins exact replicas, ignores unselected overlap as standby capacity, and calls only the selected experts in layer order. The serving-plan API ranks missing coverage for a requested contribution size and protects starts with a coverage revision.

When serving and generating on the same machine, generator startup directly seeds matching local node multiaddresses alongside configured bootstrap peers. Generator peers enable relay dialing. Readiness resolves every selected expert and probes RPC metadata so DHT coverage or a claimed relay address alone cannot produce a false-ready state.

`DistributedGenerator` loads local model components and performs autoregressive generation through that route. It supports exact generation controls, stop requests, route readiness, next-token parity probes, generated-output comparisons, and JSON trace artifacts. Generator status also exposes startup/load duration, current route-probe duration, latest time to first token, total generation duration, token throughput, and per-hop RPC latency aggregates. These measurements are observational and do not alter route selection.

When incentives are in shadow or credit mode, compatible peers use a separate receipt expert. Ed25519-signed generator requests and worker receipts commit to route, model revision, layer range, tensor input/output, session, nonce, position count, and timestamps. A generator countersigns accepted work and submits it asynchronously to the project VPS settlement service. SQLite WAL storage rejects replay, tampering, self-dealing, incomplete routes, invalid revisions, and position overclaims. Credit mode remains gated behind live shadow validation.

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

- Backend regression suite: 149 tests and 19 subtests passing as of 2026-08-12.
- Frontend TypeScript typecheck, lint, production build, and Python compilation pass.
- Local OPT-125M and OPT-1.3B smoke/parity evidence exists.
- Hugging Face device OAuth and real gated Llama 2 download have been exercised.
- A Windows/WSL participant obtained a complete circuit address through the public VPS relay in 1.63 seconds, and a second same-host Hivemind peer completed an OPT-125M expert metadata RPC using only that circuit address. Tensor forwarding, direct two-device routing, and relayed two-device inference remain to be validated live.
- A complete laptop plus VPS/Colab Llama 2 route and generated response remain unproven.

## Known Operational Limits

- Colab sessions are temporary, may lack GPU/high RAM, and may block inbound peer RPC behind NAT.
- VPS workers need enough RAM for full-model construction and reachable worker RPC ports or relay support.
- Python 3.12 is the supported runtime; Hivemind/Pydantic compatibility is unreliable on Python 3.14.
- Bootstrap reachability proves discovery transport only, not model-worker RPC reachability.
- Live receipt-capable two-device validation, credit-mode approval, API keys, failover, stronger Sybil resistance, and distributed training remain future work.
