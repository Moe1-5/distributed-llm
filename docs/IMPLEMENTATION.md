# Implementation Plan

This plan turns the current prototype into a reliable Petals-inspired distributed inference system for this project's own public/discoverable swarm. The network should be open enough for external devices to join and contribute resources, but isolated from public Petals/IPFS infrastructure by project-owned bootstrap nodes, DHT namespaces, metadata contracts, model registry, and routing rules.

## Current Position - 2026-07-13

Phases 0 through 3 have substantial implemented foundations: structured readiness, contiguous route planning, cancellation, OPT/Llama-family adapter behavior, parity/trace tooling, multi-node local registries, monitoring, Hugging Face OAuth downloads, validated gated local imports, and instruction-ready model metadata. These areas still require broader live multi-machine validation; their presence in earlier roadmap phases no longer means they are wholly unimplemented.

Current active validation work:

- Sprint 10: complete live gated-model startup/generation/offline reuse evidence.
- Sprint 11: complete TinyLlama/Llama 2 instruction-ready inference evidence.
- Sprint 12: live-check anonymous public loading and failed-start cleanup after the expired-token fix.
- Sprint 13: real incentives and settlement remain gated by live multi-device correctness evidence.
- Sprint 14: chat/instruct templates, context-aware streaming, local lifecycle separation, and performance contracts are implemented; live TinyLlama and two-device baselines remain open.

## Phase 0: Stabilize the Current Prototype

Goal: make current failures clear, prevent known bad inference paths, and avoid late user-facing errors.

### Tasks

1. Add generator readiness validation before inference.
   - DHT connection exists.
   - nodes are discovered.
   - selected model matches node metadata.
   - layer coverage is complete.
   - route is contiguous and non-overlapping.
   - every selected node has `rpc_uid`.

2. Add a `/generator/status` or `/inference/readiness` endpoint.
   - Return `ready: true/false`.
   - Return structured reasons if not ready.
   - Frontend should disable send/start inference when not ready.

3. Add inference cancellation.
   - Track active generation job id.
   - Add cancellation flag.
   - Check flag between token steps.
   - Add WebSocket `cancel` message or HTTP stop endpoint.

4. Remove runtime `assert` usage in request/runtime paths.
   - Replace with explicit `ValueError`, `RuntimeError`, or FastAPI `HTTPException`.
   - Keep asserts only for developer invariants that are not user/runtime input.

5. Add better frontend connection state.
   - Do not mark WebSocket connected until `onopen`.
   - Show generator readiness separately from WebSocket connection.
   - Surface backend errors with actionable messages.

## Phase 1: Correct Route Planning

Goal: remote layer execution should run each layer exactly once in order.

### Current Problem

Historical problem: `RemoteSequential` sorted every discovered node and could execute overlaps. The current route planner validates metadata and selects a contiguous, non-overlapping model-compatible route. Remaining work is live failover/health-based selection across competing providers.

### Target Design

Add a route planner:

```python
def build_route(nodes: list[dict], num_layers: int, model_name: str) -> list[dict]:
    ...
```

It should:

- filter nodes by `model_name`
- validate metadata types and ranges
- remove stale/offline nodes
- select exactly one node for each next span
- reject gaps
- reject overlaps in the final chosen route
- return a route where:
  - first node starts at `0`
  - last node ends at `num_layers`
  - each node's `layer_start` equals previous node's `layer_end`

For a first implementation, choose the longest valid next span. Later, add latency and health scoring.

## Phase 2: Architecture Adapters

Goal: the distributed forward path should match HuggingFace model behavior.

### Current Problem

The current split assumes:

```text
embed_tokens -> decoder layers -> final_norm -> lm_head
```

That is not enough for OPT and may not be enough for other architectures.

### Target Design

Create an adapter interface, likely under `backend/models/`:

```python
class ArchitectureAdapter:
    model_type: str

    def load_local_components(...)
    def prepare_inputs(input_ids, generated_ids, device, dtype) -> PreparedInputs
    def apply_output_head(hidden_states) -> logits
    def extract_layers(model) -> nn.ModuleList
```

Potential concrete adapters:

- `OPTAdapter`
- `LlamaAdapter`
- `MistralAdapter`

### OPT Adapter Requirements

For OPT:

- use token embeddings
- add learned positional embeddings before first decoder layer
- prepare OPT-compatible causal attention mask
- handle `project_in` if present
- after remote layers, apply final norm and `project_out` if present
- apply LM head

### Llama/Mistral Adapter Requirements

For Llama/Mistral:

- token embeddings are usually enough before layers
- rotary position handling happens inside attention modules
- position ids still need to be correct
- attention mask format must match the installed Transformers version
- final norm and LM head stay local

## Phase 3: Local Parity Tests

Goal: prove the split path produces the same logits as the normal model before testing P2P.

### Test Shape

For each supported architecture:

1. Load normal HuggingFace model.
2. Load local components and all layers through the split path.
3. Run the same prompt through both.
4. Compare final-token logits.

Expected:

- logits should be close within a dtype-appropriate tolerance
- generated top tokens should usually match for greedy decoding

This catches missing positional embeddings, wrong mask shapes, wrong final norm, wrong projection, and dtype mistakes.

## Phase 4: Health and Probe Calls

Goal: catch dead nodes before inference.

### Tasks

- Add a lightweight node health probe.
- Verify `get_experts(...)` resolves each selected `rpc_uid`.
- Optionally send a tiny synthetic tensor through each node.
- Record latency and last success timestamp.
- Store health info in route planner scoring.

## Phase 5: Session-Aware Generation

Goal: move toward Petals-like efficiency.

### Current Behavior

Each token step embeds and sends the full generated sequence through all remote layers.

### Target Behavior

Longer term:

- create a session id per generation
- hold route stable during the session
- support KV cache on remote nodes
- send only the new token after the initial prefill

This is a larger change because node RPC APIs need to support cache state.

## Phase 6: Multi-Node and Multi-Generator Management

Goal: make backend state explicit instead of relying on one global node and one global generator.

Options:

1. Keep one backend process equal to one participant.
   - Simpler.
   - Document this as a constraint.

2. Add registries.
   - `nodes: dict[node_id, Node]`
   - `generators: dict[session_id, DistributedGenerator]`
   - better for multiple models or concurrent users

The backend now has a local node registry for non-overlapping same-prefix replicas, while generator state remains process-global. Separate processes are still the normal model for independent remote participants.

### 2026-07-06 Sprint 06 Decision

Keep one backend process equal to one serving participant for the current prototype. Local split-route testing should use multiple backend processes with different ports and layer ranges until a node registry is designed. The app should still expose model runnable state from DHT coverage so users can distinguish a supported registry model from a currently runnable model.

## Phase 7: Frontend Product Flow

Goal: make the UI reflect actual distributed readiness.

### Add

- readiness badge for selected model
- route preview before inference
- stop generation button
- generator stop/reset button
- clear error recovery path
- model compatibility warnings
- backend URL configuration instead of hardcoded IP

### 2026-07-05 Product Flow Corrections

User review of the Electron screens identified these workflow changes:

- Bootstrap setup should move out of client navigation. Bootstrap nodes are internal discovery infrastructure; users should not see command-line bootstrap setup as a normal product tab.
- Node stopping should be available from the primary node management surface, including the main Nodes page for locally served nodes.
- Inference should have a visible stop/cancel control while generation is active.
- A dedicated Monitoring page should be added to main navigation for the network graph/status view.
- The UI should distinguish backend reachability, WebSocket connection, generator readiness, active inference, and route completeness.
- The app should not imply that a user can inference any model in the registry. A model is runnable only when the network has complete compatible layer coverage for that model.
- Incentive UI and accounting should be model-aware and contribution-aware, not a single undifferentiated token pool.

Suggested main navigation after this correction:

1. Nodes: local hardware stats and lifecycle controls for process-owned serving nodes.
2. Network: serve layer slices and connect a generator/client to the swarm.
3. Inference: prompt streaming, route trace, and active generation cancel.
4. Monitoring: network-wide graph/status view for discovered peer health, layer coverage, route state, and latency.
5. Settings: Hugging Face connection, imported models, and local configuration.

Implementation should follow validation: first prove the backend can report trustworthy route/readiness state, then bind the UI controls to that state.

## Phase 8: Fault Tolerance and Public Swarm Operations

Goal: make the network resilient enough for public participation.

### Add

- route failover when a selected node disappears
- retry with alternate providers for the same layer span
- node health scores based on latency, failures, and last successful probe
- stale member cleanup or expiry-aware discovery
- route caching with invalidation when health changes
- bootstrap-node deployment and rotation plan
- protocol/version fields in DHT metadata so incompatible nodes can be rejected

## Phase 9: Network Monitor Experience

Goal: help users understand the distributed network visually.

### Add

- animated graph of connected peers
- layer-range labels per node
- route animation during inference
- node health, latency, and contribution status
- model/prefix filters
- clear distinction between bootstrap nodes, serving nodes, and generator clients

## Phase 10: Incentives and Contributor Accounting

Goal: create a path toward token-based incentives for devices that serve useful compute.

### Add

- contribution accounting for served layer requests
- signed node identity and request receipts
- proof-of-work or proof-of-service design for completed inference hops
- anti-spam and anti-fake-work rules
- token/reward ledger design
- payout rules based on reliability, latency, served model, and resource cost

This phase should wait until core inference, health checks, and route correctness are reliable. Incentives before correctness would reward untrusted or useless work.

### 2026-07-06 Sprint 06 Decision

Initial incentives are simulated accounting only. The backend records model-aware and contribution-aware serving metrics for local nodes: peer identity, model, layer range, layers served, device, successful requests, failed requests, token positions served, latency totals, average latency, and last success/error timestamps. Token UI, balances, claims, and reward settlement stay disabled until route correctness, health checks, anti-abuse checks, and receipt/proof design are validated. Real incentives are deferred to Sprint 13.

## Phase 11: API Access for Served Models

Goal: let users request API keys for models inferenced by the distributed network.

### Add

- API key issuance and revocation
- per-key usage limits and accounting
- model access policy
- request authentication
- backend endpoints for non-UI inference clients
- billing or credit integration if incentives are enabled

## Phase 12: Distributed Training and Fine-Tuning Resources

Goal: optionally expand beyond inference into distributed training/fine-tuning resource requests.

This is intentionally last because training is harder than inference. It requires stronger scheduling, data privacy rules, gradient/optimizer handling, checkpointing, fault tolerance, and incentive design.

### Possible Direction

- users submit training/fine-tuning jobs
- available devices advertise compute, memory, and availability
- scheduler assigns work across resources
- network tracks completed work and failures
- model checkpoints are stored and resumed safely

## Priority Order

1. Complete live TinyLlama and gated Llama 2 startup/inference validation.
2. Prove a real multi-machine contiguous route through a public VPS bootstrap.
3. Add reliable worker RPC reachability through fixed ports, relay, or an overlay network.
4. Reduce peak full-model CPU memory during layer-slice loading.
5. Expand architecture parity evidence for every user-facing model.
6. Add health probes, scoring, and route failover.
7. Add stable session routing and distributed KV cache.
8. Harden public swarm operations, protocol/version compatibility, and bootstrap rotation.
9. Add API-key access for inferenced models.
10. Implement Sprint 13 receipts, anti-abuse checks, incentives, and settlement.
11. Consider distributed training/fine-tuning resource requests last.

## Definition of Done for Correct Inference

Before a model is considered supported:

- local split parity test passes
- distributed single-node parity test passes
- multi-node contiguous route test passes
- missing-node readiness check fails gracefully
- overlapping-node route is rejected or resolved correctly
- WebSocket streaming can be cancelled
- frontend shows readiness before send
