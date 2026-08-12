# Validation and Test Plan

This project needs tests at several levels because distributed inference can fail in many ways that look like "bad text output."

## Purpose of This Document

This document is the source of truth for what has to be proven before the project moves from prototype work into broader public-swarm features such as real bootstrap nodes, fault tolerance, API keys, incentives, and distributed training resources.

Future agents should update this document after each meaningful validation pass so the project history shows which functionality has actually been tested, which tests failed, and which assumptions are still unproven. Do not mark a phase complete just because unit tests pass; distributed inference needs staged system validation.

## Current Testing Phase Gate

The current project priority is to finish live instruction-ready and gated-model multi-machine inference validation before adding advanced features.

### Current Status - 2026-07-13

- Backend regression suite: 96 tests plus 19 subtests passing.
- Python compilation and frontend typecheck pass.
- Local OPT-125M and OPT-1.3B single-route smoke/parity evidence exists.
- Hugging Face browser/device OAuth and a real approved Llama 2 download have succeeded.
- Public loading now explicitly ignores stale OAuth state; live TinyLlama retry remains pending.
- A public VPS bootstrap has been reachable from Windows on TCP port 7001.
- Complete laptop plus VPS/Colab worker RPC and generated Llama 2 response remain unproven.
- Colab CPU-only/low-RAM sessions and NAT are known blockers, not successful GPU-worker evidence.

Recommended order:

```text
1. Backend regression tests
2. Local one-machine system smoke test
3. Local bootstrap + local serving node + local generator test
4. Local single-node full-layer inference test
5. Local multi-node layer-split inference test
6. Real multi-machine distributed network test
7. Stable real bootstrap node deployment
8. Gated OAuth/download/offline startup validation
9. Instruction-ready TinyLlama/Llama 2 parity and quality evidence
10. Only then: fault tolerance, API keys, Sprint 13 incentives, and training resources
```

### Phase A: Backend Regression Tests

- [x] Run backend regression tests with `uv run pytest`.
- [x] Run optimized Python validation with `uv run python -O -m pytest tests/test_generation_readiness.py`.
- [x] Run backend compile check with `uv run python -m compileall api client node models -q`.

Last known status: passed on 2026-07-13 during Sprint 12 validation. The focused backend suite passed 96 tests and 19 subtests; backend compileall and frontend `npm run typecheck` passed. The older 24-test Sprint 04 result remains historical evidence, not the current suite size.

### Phase B: Local One-Machine Smoke Test

- [x] Start a local bootstrap node.
- [x] Start the backend API.
- [x] Start the frontend or call backend endpoints directly.
- [x] Confirm `/status` responds.
- [x] Confirm `/models` returns supported models.
- [x] Confirm the frontend/backend URL is correct for the local machine.

Suggested first model: `facebook/opt-125m`.

2026-07-06 result: passed using backend endpoints directly. The backend was reachable on `http://127.0.0.1:8000`, matching the frontend's localhost default. The local bootstrap address was `/ip4/127.0.0.1/tcp/7001/p2p/QmY54qgx7Si9KCWXFVdrn4J7kGPfdJUoNHNeTy1JqnDnX7`. The first sandboxed API run was not reachable from separate localhost probes, so the live smoke test ran the API outside the sandbox.

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

- [x] Bootstrap starts and prints reachable multiaddresses.
- [x] Serving node starts and loads all requested layers.
- [x] Serving node announces metadata to the DHT.
- [x] `/nodes` shows the serving node.
- [x] Generator starts with the same model and DHT prefix.
- [x] Generator validates/discovers a full route.
- [x] A short prompt returns without crashing.
- [x] Output is at least structurally coherent enough to prove the path is executing.
- [x] Any failure is recorded in `ISSUES.md` with logs and reproduction steps.

2026-07-06 result: passed for single-node full-layer local inference. `/chat` returned `Michael. I��m a writer` for prompt `Hello, my name is`, through route `12D3KooW… (layers 0→12)`. The path executed without crashing. Replacement characters in the response and output quality remain later validation concerns.

2026-07-06 output sanity addendum: completion-style prompts showed that the distributed path executes but answer quality is not dependable. `Hello, my name is` produced a plausible continuation, and `Once upon a time` produced a story-like continuation. However, `The capital of France is` produced unrelated factual continuations, and `2 + 2 =` produced algebraic nonsense. Treat this as transport/execution success only until local HuggingFace-vs-distributed parity is checked.

2026-07-06 Sprint 07 parity update: local single-node full-layer OPT-125M route passed deterministic next-token argmax parity against direct HuggingFace for the baseline prompt set. Strict `1e-4` logits allclose was too tight for the live mixed-device float path, but every direct/distributed next token matched and logits were close with `atol=0.02`, `rtol=0.02`. Distributed generated text remains weak in the same ways as the direct baseline, so the current root cause is model/prompt quality rather than route corruption.

2026-07-06 OPT-1.3B smoke update: local bootstrap, backend API, one CUDA serving node for `facebook/opt-1.3b` layers `0-24`, and a matching generator all started successfully. `/generator/status` reported route `12D3KooW… (layers 0→24)`. `/generator/trace` for prompt `The capital of France is` generated `Paris` as the first token and wrote a JSON trace file under `backend/traces/`; no replacement characters appeared in selected token text or decoded output. A normal sampled `/chat` call with the same prompt returned a coherent but factually bad continuation, while `/generator/parity/next-token` matched direct HuggingFace on first-token argmax (`Paris`) with `max_abs_diff=0.015625`, `mean_abs_diff=0.002189`, and `allclose=true` at `atol=0.02`, `rtol=0.02`. Current classification: the tested 1.3B route is faithful for deterministic first-token parity, and bad sampled prose is not evidence of route corruption by itself.

### Model Quality Policy

Changing to a better model is the right solution for user-facing answer quality, but it is not a substitute for distributed correctness validation.

The OPT models currently used in local testing are base completion models. They are useful smoke-test targets because they are open, relatively small, and quick to load, but they are not reliable chat or factual-answer demos. If direct HuggingFace and the distributed route agree on deterministic next-token logits, then poor sampled prose should be classified first as model/sampling behavior, not as a distributed inference failure.

For demos, prefer an instruction-tuned model that fits the current serving hardware and has a supported architecture adapter. Candidate demo models should pass the same gates as smoke-test models before they are shown as product quality:

- model is registered with correct layer count, hidden size, and generation defaults
- architecture adapter matches the HuggingFace forward path
- direct HuggingFace versus distributed next-token parity passes
- exact generation controls are available for reproduction, including greedy `do_sample=false`, `top_k`, and `repetition_penalty`
- `/generator/trace` shows clean token selection and decoding, with no replacement-character corruption
- route readiness requires complete compatible layer coverage before inference starts

Why: changing models can make the answer better, but only parity and trace evidence can prove that the distributed path is correct. Without those checks, a stronger model could hide routing, adapter, decoding, or streaming bugs until a larger multi-node route fails.

2026-07-06 shutdown addendum: `/node/stop` originally hung after a serving node started. Bounded RPC/DHT shutdown now lets `/node/stop` return `{"status":"stopped"}` in about five seconds. Remaining risk: backend/Hivemind worker processes can still remain after shutdown and need explicit cleanup before repeated UI start/stop cycles are trusted.

### Phase D: Local Multi-Node Layer-Split Inference Test

Run multiple local serving nodes, each with a contiguous layer slice.

Example:

```text
node A: layers 0-4
node B: layers 4-8
node C: layers 8-12
```

Checklist:

- [x] Each node has a unique RPC UID.
- [x] DHT discovery sees all serving nodes.
- [x] Route planning selects an exact adjacent split route.
- [x] No layer is skipped.
- [x] No layer is executed twice.
- [x] A short prompt returns without crashing.
- [x] Output behavior is compared with direct Hugging Face and the single-node parity baseline.

2026-07-06 result: blocked in the current one-backend process flow. RPC UID uniqueness is covered by unit tests, but a second `/node/start` call returned `already_running` with the existing full-layer node. See `ISSUES.md` findings 24 and 25.

2026-08-13 result: passed after later node-registry work removed the old one-node limitation. `python -m local_split_probe` started an isolated loopback bootstrap, two real OPT-125M serving peers for `0-6` and `6-12`, and a separate generator DHT. The route used both peers, direct and distributed next-token logits matched exactly, two-token greedy text matched exactly, and each worker recorded three successful requests plus 19 useful positions with zero failures. Explicit shutdown completed with no remaining `p2pd` process; Hivemind still emitted late event-loop destructor warnings and one pending control task, recorded in `ISSUES.md`. See [Local Split Acceptance](LOCAL_SPLIT_ACCEPTANCE.md).

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

2026-07-12/13 update: the VPS bootstrap at a public address passed an external TCP connectivity check after opening port 7001. Laptop and Colab attempts exposed duplicate/stuck bootstrap process risk, Python 3.14 incompatibility on the VPS, Colab CPU-only runtime selection, insufficient 12.7 GB RAM for Llama 2 full-model construction, and possible Colab NAT restrictions. No successful remote worker RPC/inference route has been recorded yet.

2026-08-13 tooling update: `python -m acceptance_evidence` captures sanitized local ownership, selected/standby coverage, transport, generation timing, and shadow-settlement evidence, then validates a complete executed route across at least two participant labels. The exact relay, direct, and standby workflows are in [Two-Device Acceptance Evidence](TWO_DEVICE_ACCEPTANCE_EVIDENCE.md). The tool does not replace physical-device observation, clean Windows packaging validation, or live VPS restart verification.

### Phase E.1: Hugging Face OAuth and Gated Local Model Validation

- [x] Browser/device OAuth completes without token paste.
- [x] Auth state is redacted from frontend/API/log output.
- [x] Unapproved gated repositories return an actionable access message.
- [x] An approved Llama 2 snapshot can be downloaded.
- [x] Downloaded snapshots are validated and registered.
- [x] Gated runtime code uses validated local files without a token.
- [ ] Live gated serving node starts successfully.
- [ ] Live gated generator starts successfully.
- [ ] Inference completes through the gated route.
- [ ] Disconnect then offline startup succeeds from the validated snapshot.

### Phase E.2: Instruction-Ready Model Validation

- [x] TinyLlama chat and Llama 2 chat entries have explicit tuning/shape/generation metadata.
- [x] Registry and local-import contracts have regression coverage.
- [x] Public model loading is explicitly anonymous and independent from OAuth state.
- [x] TinyLlama live node/generator/inference smoke test passes.
- [ ] Llama 2 chat distributed route produces a response.
- [ ] Direct versus distributed parity/quality notes are recorded before user-facing support is claimed.

2026-08-13 performance update: a cached bfloat16 CPU TinyLlama full-range worker and separate generator completed a two-token chat-template request through real Hivemind RPC after the Transformers 5.3 `BatchEncoding` compatibility fix. Time to first token was 1795.227 ms, total generation was 3116.633 ms, throughput was 0.642 tokens/s, and two RPC calls totaled 2883.702 ms. See [TinyLlama Performance Baseline](TINYLLAMA_PERFORMANCE_BASELINE.md). This is performance/transport evidence, not direct-versus-distributed TinyLlama parity approval.

### Phase E.3: Failed Startup Cleanup Validation

- [x] Failed node startup invokes cleanup for partial DHT/RPC/handler state in regression tests.
- [x] Failed generator startup unloads partial model state and shuts down client DHT in regression tests.
- [x] Expired authenticated operations map to `huggingface_reconnect_required`.
- [ ] Live repeated failed-start test confirms no orphaned `p2pd` processes remain.

### Phase F: Public-Swarm Readiness Gate

Do not start token incentives, API-key product flows, or distributed training features until these are true:

- [x] Local single-node inference is proven.
- [x] Local multi-node split inference is proven.
- [ ] Real multi-machine inference is proven.
- [x] Route readiness is exposed before inference starts.
- [x] Basic cancellation or recovery exists for stuck inference.
- [x] Bootstrap deployment and replacement process is documented.

### Phase G: Product Workflow Validation

These checks come from the 2026-07-05 Electron screen review and should be verified before the UI is considered usable:

- [ ] Inference page does not allow sending when generator readiness or route coverage is false.
- [ ] WebSocket status is driven by real `onopen`, `onclose`, and `onerror` events, not optimistic state.
- [ ] First message either waits for WebSocket open or clearly blocks until connected.
- [ ] Active inference can be stopped/cancelled from the Inference page.
- [ ] Locally served nodes can be stopped from the primary node management surface.
- [ ] Bootstrap setup is not shown as a normal client workflow tab.
- [ ] A Monitoring page exists for network graph/status, layer coverage, route health, latency, and model/prefix filters.
- [ ] Model selection explains that inference requires complete compatible served coverage, not only a registry entry.
- [ ] Incentive/accounting design is model-aware and contribution-aware before token UI is added.

2026-07-06 Sprint 04 disposition: product workflow validation should not block the local smoke-test result, but each finding now has an owner before advanced features continue.

| Finding | Current status | Owner |
| --- | --- | --- |
| Unrelated prompt output | Transport works; quality is not trusted until direct HuggingFace parity is checked. | Sprint 07 |
| WebSocket connected/send mismatch | First-send lifecycle fixed; broader state display still needs UI cleanup. | Sprint 04 / Sprint 05 |
| One backend can serve only one layer slice | Current limitation documented; local split route blocked in one process. | Sprint 06 |
| Stop local node control placement | Backend stop returns, but UI placement is still deferred. | Sprint 05 |
| Bootstrap exposed as client workflow | Deferred; bootstrap should move out of normal client navigation. | Sprint 05 |
| Active inference cancellation | Fixed with generator stop handling and an Inference page stop button. | Sprint 04 |
| Monitoring navigation page | Deferred to client workflow cleanup. | Sprint 05 |
| Runnable model semantics | Deferred; runnable should mean registry support plus complete compatible coverage. | Sprint 06 |
| Incentive accounting semantics | Deferred; accounting should be model-aware and contribution-aware. | Sprint 06 |

The earlier Sprint 05 through Sprint 09 implementation owners are closed. Remaining advanced features are deferred until Sprints 10 through 12 finish their live gates and Sprint 13 prerequisites are met.

2026-07-06 Sprint 06 update: runnable model semantics are implemented as registry support plus complete compatible route coverage reported by `/models`. Incentive semantics are simulated accounting only: contribution records are model-aware and layer-aware, but token UI and reward settlement remain disabled until correctness, health, and anti-abuse checks are proven.

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

2026-07-06 priority note: output sanity checks make this parity test the next correctness gate. The local distributed route runs, but generated text quality is not enough to prove the split path matches HuggingFace behavior.

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
- UI does not show connected while send still reports `WebSocket not connected`

## 8.1 UI Workflow Tests

Cases:

- Bootstrap setup is documented as infrastructure, not exposed as a client tab.
- Nodes page shows locally served nodes and stop controls.
- Network page can start serving and generator workflows without implying bootstrap ownership.
- Inference page shows generator readiness, active route trace, and a cancel control.
- Monitoring page shows network-level status rather than duplicating setup controls.
- Model dropdown distinguishes supported models from currently runnable models with complete route coverage.

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
