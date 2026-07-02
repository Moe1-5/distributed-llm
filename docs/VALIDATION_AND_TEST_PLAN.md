# Validation and Test Plan

This project needs tests at several levels because distributed inference can fail in many ways that look like "bad text output."

## Purpose of This Document

This document is the source of truth for what has to be proven before the project moves from prototype work into broader public-swarm features such as real bootstrap nodes, fault tolerance, API keys, incentives, and distributed training resources.

Future agents should update this document after each meaningful validation pass so the project history shows which functionality has actually been tested, which tests failed, and which assumptions are still unproven. Do not mark a phase complete just because unit tests pass; distributed inference needs staged system validation.

## Current Testing Phase Gate

The current project priority is to prove the core distributed LLM inference path before adding advanced features.

Recommended order:

```text
1. Backend regression tests
2. Local one-machine system smoke test
3. Local bootstrap + local serving node + local generator test
4. Local single-node full-layer inference test
5. Local multi-node layer-split inference test
6. Real multi-machine distributed network test
7. Stable real bootstrap node deployment
8. Only then: fault tolerance, monitor, API keys, incentives, and training resources
```

### Phase A: Backend Regression Tests

- [x] Run backend regression tests with `uv run pytest`.
- [x] Run optimized Python validation with `uv run python -O -m pytest tests/test_generation_readiness.py`.
- [x] Run backend compile check with `uv run python -m compileall api client node models -q`.

Last known status: passed during Sprint 03 closure.

### Phase B: Local One-Machine Smoke Test

- [ ] Start a local bootstrap node.
- [ ] Start the backend API.
- [ ] Start the frontend or call backend endpoints directly.
- [ ] Confirm `/status` responds.
- [ ] Confirm `/models` returns supported models.
- [ ] Confirm the frontend/backend URL is correct for the local machine.

Suggested first model: `facebook/opt-125m`.

### Phase C: Local Single-Node Full-Layer Inference Test

Start one local serving node for all layers of the smallest model.

Example:

```text
model: facebook/opt-125m
layer_start: 0
layer_end: 12
dht_prefix: distribllm
device: cpu
```

Checklist:

- [ ] Bootstrap starts and prints reachable multiaddresses.
- [ ] Serving node starts and loads all requested layers.
- [ ] Serving node announces metadata to the DHT.
- [ ] `/nodes` shows the serving node.
- [ ] Generator starts with the same model and DHT prefix.
- [ ] Generator validates/discovers a full route.
- [ ] A short prompt returns without crashing.
- [ ] Output is at least structurally coherent enough to prove the path is executing.
- [ ] Any failure is recorded in `ISSUES.md` with logs and reproduction steps.

### Phase D: Local Multi-Node Layer-Split Inference Test

Run multiple local serving nodes, each with a contiguous layer slice.

Example:

```text
node A: layers 0-4
node B: layers 4-8
node C: layers 8-12
```

Checklist:

- [ ] Each node has a unique RPC UID.
- [ ] DHT discovery sees all serving nodes.
- [ ] Route planning selects the exact route `0-4 -> 4-8 -> 8-12`.
- [ ] No layer is skipped.
- [ ] No layer is executed twice.
- [ ] A short prompt returns without crashing.
- [ ] Output behavior is compared with the single-node full-layer test.

### Phase E: Real Multi-Machine Distributed Network Test

Run the system across real devices.

Suggested layout:

```text
machine/server 1: bootstrap node
machine 2: serving node for first layer slice
machine 3: serving node for second layer slice
machine 4 or one of the above: generator + frontend
```

Checklist:

- [ ] Real bootstrap node is reachable from other machines.
- [ ] Serving nodes join using the configured bootstrap address.
- [ ] DHT metadata appears under the expected project prefix.
- [ ] Generator discovers only compatible model nodes.
- [ ] Route validation passes.
- [ ] Inference returns through the distributed route.
- [ ] Failures caused by firewall, NAT, stale peer IDs, or unreachable ports are documented.

### Phase F: Public-Swarm Readiness Gate

Do not start token incentives, API-key product flows, or distributed training features until these are true:

- [ ] Local single-node inference is proven.
- [ ] Local multi-node split inference is proven.
- [ ] Real multi-machine inference is proven.
- [ ] Route readiness is exposed before inference starts.
- [ ] Basic cancellation or recovery exists for stuck inference.
- [ ] Bootstrap deployment and replacement process is documented.

## 1. Static Validation

### Backend

- Python syntax check:

```bash
python3 -m py_compile backend/client/generation.py
```

- Extend this to all backend source files:

```bash
python3 -m compileall backend
```

### Frontend

Run:

```bash
cd frontend
npm run typecheck
npm run lint
```

## 2. DHT Metadata Validation

Add unit tests for node metadata validation.

Cases:

- missing `rpc_uid`
- missing `model_name`
- non-integer layer range
- negative `layer_start`
- `layer_end <= layer_start`
- `layer_end > num_layers`
- model mismatch
- stale timestamp

Expected: invalid nodes are rejected with a useful reason.

## 3. Route Planner Tests

Add tests for route building.

Cases:

- exact single-node route: `0-24`
- exact multi-node route: `0-8`, `8-16`, `16-24`
- missing gap: `0-8`, `10-24`
- overlap: `0-10`, `8-24`
- duplicate provider for same range
- wrong model in DHT
- route starts after zero
- route ends before model depth

Expected: only contiguous full routes are accepted.

## 4. Local Split Parity Tests

This is the most important correctness test.

For each supported model:

1. Load normal HuggingFace model.
2. Load split components locally.
3. Route through local layers without network.
4. Compare final logits.

Expected:

- final hidden state and logits should be close
- greedy next token should match

This proves model-specific adapter correctness.

## 5. Single-Node Distributed Parity Test

Run all model layers on one serving node:

```text
node: layers 0-num_layers
generator: same model
```

Then compare distributed logits/generation against the local split path.

Expected:

- outputs should match within tolerance
- no DHT or RPC shape/dtype errors

## 6. Multi-Node Distributed Test

Run layer slices across multiple nodes:

```text
node A: 0-8
node B: 8-16
node C: 16-24
```

Expected:

- route planner selects A -> B -> C
- each layer runs exactly once
- output matches single-node distributed test

## 7. Failure Tests

### Missing Node

Stop one node in the middle of the route.

Expected:

- readiness check fails before inference
- UI explains missing layer range

### Overlapping Nodes

Start:

```text
node A: 0-10
node B: 8-24
```

Expected:

- route planner rejects overlap or chooses a valid alternative
- layers are not run twice

### Stale DHT Entry

Stop a node and leave stale member id in `{prefix}.members`.

Expected:

- stale metadata is ignored
- route planner reports unavailable layers if needed

### Wrong Model

Start OPT node and Llama generator.

Expected:

- readiness rejects model mismatch

## 8. WebSocket Tests

Cases:

- first message waits for `onopen`
- backend unavailable gives clear error
- generator unavailable blocks send or returns readiness message
- stream completes and sets `done`
- stream can be cancelled
- WebSocket reconnect works after close

## 9. Performance Tests

Track:

- time to discover nodes
- time to build route
- per-hop latency
- tokens per second
- VRAM usage on node
- RAM usage on generator

This data should eventually feed route selection.

## 10. Acceptance Criteria

A model should be marked "supported" only when:

- architecture adapter exists
- local split parity passes
- single-node distributed parity passes
- multi-node route parity passes
- readiness checks catch missing/wrong routes
- frontend can start, stream, cancel, and recover from errors
