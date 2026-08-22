# Current Architecture

## System Shape

DistribLLM is an Electron desktop client backed by a local FastAPI process. The backend owns one persistent network control plane, can host one or more non-overlapping layer slices for the same model and DHT prefix, run one generator, expose monitoring/status APIs, and manage local Hugging Face authentication and model imports.

The target network is a project-owned public/discoverable swarm. External devices join through DistribLLM bootstrap peers, but `use_ipfs=False` keeps the system isolated from public Petals/IPFS infrastructure.

## Runtime Roles

- Bootstrap node: stable discovery entry point only; never serves transformer layers.
- Network supervisor: owns the backend's persistent discovery-only DHT, last-good topology snapshot, role registry, publication verification, and exact-handle network shutdown/quarantine.
- Serving node: owns a contiguous model layer range and exposes it through Hivemind RPC.
- Generator client: keeps tokenizer, embeddings, final normalization, and LM head locally, then routes hidden states through serving nodes.
- Electron/FastAPI client: controls local serving, generator startup, inference, monitoring, settings, OAuth, and managed model downloads.
- Headless worker: `backend/colab_worker.py` runs a serving node on Colab or another GPU host without Electron.

## Frontend

The renderer has six pages:

- Nodes: local hardware state and lifecycle controls for nodes owned by this backend process.
- Network: serve layers, manage local replicas, connect Hugging Face, download/import gated models, and start the generator.
- Inference: prompt streaming, readiness, route trace, and cancellation.
- Monitoring: network-wide route coverage, discovered peers, and performance/health information.
- Incentives: application identity, verified useful-work credits, receipt activity, and settlement connectivity.
- Settings: backend and Hugging Face connection/local-model state.

Bootstrap configuration is intentionally hidden from normal product workflow. The backend URL defaults to `http://127.0.0.1:8000`; Vite overrides use `VITE_API_BASE_URL` and `VITE_WS_BASE_URL`.

Renderer HTTP calls have finite deadlines. Stats, node, status, and Monitoring polls do not overlap themselves, and Monitoring preserves successful partial responses when another endpoint times out. Network loads the local model catalog without a DHT scan, then requests coverage independently for the selected model.

In the Windows package, Electron main owns a managed WSL launcher. It validates persisted distro/path/relay configuration, checks WSL and distro availability, synchronizes the uv environment, launches FastAPI through `wsl.exe`, polls the loopback status endpoint, and publishes typed lifecycle diagnostics over preload IPC. The Nodes and inference views still communicate with FastAPI normally; Settings owns first-run launcher configuration and lifecycle commands. The launcher PID file stops only its managed backend process and leaves WSL model, identity, OAuth, trace, and receipt state intact.

## Backend State

`backend/api/server.py` currently owns process-level state:

```python
gpu_monitor: Optional[GPUMonitor]
local_nodes: dict[str, Node]
generator: Optional[DistributedGenerator]
client_dht: Optional[hivemind.DHT]
client_dht_prefix: str
network_supervisor: NetworkSupervisor
_lifecycle_jobs: LifecycleJobStore
_runtime_state: RuntimeStateStore
```

The supervisor starts with the FastAPI lifespan before any worker or generator. It exposes `disconnected`, `syncing`, `ready`, and `degraded` states, keeps immutable validated topology snapshots, and retains the last-good snapshot with its real age and failure stage when refresh fails. Production node, model, route, and serving-plan APIs read this state passively instead of starting request-owned DHT discovery.

Long node and generator starts run as lifecycle jobs with queued, running, ready, failed, cancelling, and cancelled state. The initiating request returns immediately, progress is polled by job ID, duplicate active resource starts are deduplicated, and cancellation joins each exact executor future before cleanup. Node pause, resume, and delete actions have per-node admission, while generator generation, parity, and trace operations retain their component owner until they finish. Shutdown closes admission, requests cancellation, waits to one shared bounded deadline, and retains unresolved handles instead of reporting them as stopped. Existing synchronous endpoints remain for compatible clients.

One backend can serve multiple non-overlapping slices, but all local slices must use one model-compatible DHT prefix. Only one generator is active per backend process. True multi-machine testing uses separate backend/worker processes.

The control plane, every worker, and the generator use distinct stable identity files. The supervisor records those roles but does not share a P2P handle between them; this preserves exact route ownership and prevents a co-located generator from self-dialing its own peer identity.

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
3. Build only the requested decoder blocks and materialize their indexed safetensors.
4. Start a Hivemind RPC expert.
5. Announce validated model and transport metadata and periodically refresh it.

Nodes support pause, resume, and delete/unload as distinct operations. Failed startup calls cleanup so partial DHT, RPC, handler, and CUDA state are released.

The Hivemind server is the sole publisher of expert UID leases. DistribLLM does not run a competing manual writer for the same expert keys; its worker heartbeat owns only project metadata and membership records. When a project-record store is ambiguous, the supervisor independently reads it back without local DHT caching and classifies accepted, equivalent-newer, conflicting, short-horizon, unverified, and local-transport-failed outcomes. Because Hivemind 1.1.12 defines `DHT.store(False)` as either no acknowledgement or a newer existing record, that boolean alone never restarts a healthy worker.

Transport recovery requires a typed, independently verified local transport failure. A justified repair keeps the stable worker identity and loaded model handler, while publication conflicts or missing acknowledgements leave serving online and surface degraded publication diagnostics for bounded repair. The server borrows the worker DHT without owning its shutdown, so the worker remains the single lifecycle owner of that transport.

`DISTRIBLLM_NETWORK_MODE`, `DISTRIBLLM_P2P_PORT`, `DISTRIBLLM_ANNOUNCE_MADDRS`, and the relay settings control transport. Direct Windows/WSL operation requires a fixed port, a Windows LAN/public announce address, and mirrored networking or Windows port forwarding. Auto mode falls back to an outbound relay reservation and does not require the ordinary participant to expose a public router port.

Workers use architecture-aware selective loading for indexed or single-file safetensors checkpoints from OPT, Llama, and Mistral families. Decoder blocks are created on the meta device, only keys for the requested half-open range are read, and tensors are materialized directly at the configured dtype/device. Node metadata reports strategy, selected shards, loaded parameter bytes, elapsed time, and measured RSS growth without exposing local paths. PyTorch binary or unsupported checkpoints use an explicitly reported full-model compatibility fallback by default; operators can reject that path with `DISTRIBLLM_ALLOW_FULL_MODEL_FALLBACK=false`.

On the cached TinyLlama checkpoint, a measured one-layer CPU float32 load completed in 0.28 seconds with about 230 MB of RSS growth. The prior full-model construction baseline took 4.38 seconds and added about 4.73 GB. Single-file safetensors still occupy their full size on disk, but memory mapping avoids materializing unrelated decoder blocks.

## Routing and Generation

`RemoteSequential` validates topology metadata, filters by model, builds a contiguous non-overlapping route, rejects gaps/incompatible ranges, and calls selected RPC experts in layer order. In production its discovery input merges the supervisor's last-good remote topology with authoritative co-located worker state, with the local state winning over a cached copy of the same node. The generator keeps a separate DHT/P2P identity for exact RPC transport. Version-two workers advertise peer-scoped normal and receipt UIDs plus explicit ownership. The generator constructs each Hivemind remote expert from the route-selected peer and UID, so another provider publishing the same layer range cannot redirect an in-flight or cached route. Legacy advertisements remain readable but are pinned to their advertised peer at execution time.

When incentives are in shadow or credit mode, selected nodes may advertise a separate receipt expert with signed Ed25519 presence. The generator signs the complete route and BLAKE3 input commitment, validates the worker's signed response commitment, and countersigns accepted output. Legacy fallback is allowed only when the advertised receipt expert is absent before execution; ambiguous failures stop without duplicate work or credit. A project-owned FastAPI service validates pairs and stores an append-only SQLite WAL ledger; credits remain read-only and non-transferable.

When serving and generating on the same machine, generator startup directly seeds matching local node multiaddresses alongside configured bootstrap peers. Generator peers enable relay dialing. A lifecycle-owned health monitor discovers DHT advertisements every two seconds by default, probes selected experts every five seconds and standbys every fifteen, and caches independent DHT, transport, protocol, and RPC-health signals. Generator status reads that snapshot without launching another blocking probe.

Provider health moves through checking, healthy, degraded, and offline. One transient failure does not change a healthy route; two consecutive failures invalidate readiness, four mark the provider offline, and two successes are required to recover. The health revision changes only when route-relevant health changes. Generator unload stops the monitor before closing the cached remote-expert P2P client and DHT.

`DistributedGenerator` loads local model components and performs autoregressive generation through that route. One immutable route snapshot is discovered and validated per generation session instead of once per token. Base models preserve raw completion prompts, while chat and instruct models apply the publisher tokenizer chat template once with a generation prompt. Stream chunks are derived from cumulative tokenizer decoding so concatenating them preserves spaces and matches the final decoded sequence. The generator also supports exact generation controls, stop requests, route readiness, next-token parity probes, generated-output comparisons, and JSON trace artifacts. Generator status exposes startup/load duration, current route-probe duration, latest time to first token, total generation duration, token throughput, and per-hop RPC latency aggregates.

Ordinary expert activations use float-sixteen wire compression and restore the source dtype after transport. Receipt protocol version one deliberately uses exact uncompressed tensors so request and response commitments remain verifiable. The dependency is pinned to Hivemind 1.1.12 because peer-addressed construction, replicated P2P cleanup, and the current RPC compatibility layer are verified against that exact implementation.

The public expert boundary uses one typed RPC safety policy. Default limits are 2,048 positions, batch size four, 256 MiB across inference tensors, 16,380 receipt payload bytes, one active forward, two queued forwards, a 250 ms admission wait, and a 120-second cooperative execution deadline. Hivemind task queues reject without blocking when full. Inputs are checked for shape, model hidden size, dtype, finite values, mask/position consistency, bytes, and metadata framing before model or signature work. Runtime-process-safe counters are announced with node metadata and shown in Monitoring.

Execution deadlines are checked before the handler lock, between decoder blocks, and after the route slice. PyTorch kernels already running inside one block cannot be force-terminated safely. Queued Hivemind cancellations are removed before batching; admitted work relies on cooperative deadlines and never records receipt success until the complete batch and signed response are ready.

The current data plane is:

```text
token ids
  -> local embeddings / architecture preparation
  -> remote contiguous transformer-layer route
  -> local final normalization and LM head
  -> token selection
```

There is no distributed KV cache or multi-generator registry yet. Health-aware failover can restart a complete route only after a classified pre-execution failure; ambiguous execution still stops without failover. The selected route is stable for a generation session, but each token still processes the full sequence, so compute throughput remains prototype-grade.

## Current Validation State

- Sprint 29 implementation is complete in source: tests cover asynchronous clean start, last-good retention, authoritative local overlays, role identity separation, publication-result classification, passive supervisor-backed APIs, admission closure, cancellation races, exact streaming-request ownership, atomic Turn Off admission, delayed reachability startup, bounded shutdown responses, and idempotent exact-handle cleanup/quarantine. Physical packaged two-device validation is still pending.
- Backend regression suite: 400 tests passing as of 2026-08-23, including adversarial RPC admission, deterministic stream-disconnect cleanup, cancellable provider-health probes, supervisor/DHT ownership races, selective layer parity, and independent-peer Hivemind normal and receipt RPC integration.
- Frontend TypeScript checks, 20 managed-launcher tests, four renderer-flow tests, production build, and lint with zero errors pass. The repository still has 78 formatting warnings in pre-existing frontend files.
- Local OPT-125M and OPT-1.3B smoke/parity evidence exists.
- Hugging Face device OAuth and real gated Llama 2 download have been exercised.
- A Windows/WSL participant obtained a complete circuit address through the public VPS relay in 1.63 seconds, and a second same-host Hivemind peer completed an OPT-125M expert metadata RPC using only that circuit address. Tensor forwarding, direct two-device routing, and relayed two-device inference remain to be validated live.
- A complete laptop plus VPS/Colab Llama 2 route and generated response remain unproven.

## Known Operational Limits

- Colab sessions are temporary, may lack GPU/high RAM, and may block inbound peer RPC behind NAT.
- Workers using selective safetensors need memory for their requested blocks and transient tensors. Binary-checkpoint compatibility fallback still needs enough RAM for full-model construction.
- Python 3.12 is the supported runtime; Hivemind/Pydantic compatibility is unreliable on Python 3.14.
- Bootstrap reachability proves discovery transport only, not model-worker RPC reachability.
- The persistent supervisor removes request-owned discovery and protects last-good topology, but clean packaged startup, role identity separation, identity-preserving recovery, and deterministic shutdown still require two-device physical evidence.
- The default serial Hivemind topology scan has no safe application-level cancellation deadline. A slow scan may outlive the supervisor stop deadline; the exact DHT and refresh owner remain retained and block identity reuse until the scan actually quiesces, rather than being reported as stopped.
- Worker RPC startup captures Hivemind 1.1.12's private P2P daemon address in the owning process so force-terminated connection-handler children never share its DHT command pipe or lock. This is deliberately coupled to the pinned dependency, and a wedged parent DHT can still delay that synchronous startup capture.
- Useful-work receipts and shadow/credit settlement are implemented locally, but credit approval still requires live two-device shadow evidence. API keys, route failover, stronger collusion/Sybil resistance, and distributed training remain future work.
