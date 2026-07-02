# Validation and Test Plan

This project needs tests at several levels because distributed inference can fail in many ways that look like "bad text output."

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

