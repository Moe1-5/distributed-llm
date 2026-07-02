# Implementation Plan

This plan turns the current prototype into a more reliable Petals-inspired distributed inference system.

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

`RemoteSequential` sorts every discovered node by `layer_start` and calls all of them. Coverage checks only prove that every layer is covered at least once. Overlaps can run layers twice.

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

The current frontend and backend are closer to option 1.

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

## Priority Order

1. Route validation and model filtering.
2. OPT architecture adapter or temporarily disable OPT inference until adapter exists.
3. Readiness endpoint and UI gating.
4. Cancellation.
5. Local parity tests.
6. Health probes.
7. Session/KV cache.

## Definition of Done for Correct Inference

Before a model is considered supported:

- local split parity test passes
- distributed single-node parity test passes
- multi-node contiguous route test passes
- missing-node readiness check fails gracefully
- overlapping-node route is rejected or resolved correctly
- WebSocket streaming can be cancelled
- frontend shows readiness before send

