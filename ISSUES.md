# Code Issues

> Historical running test log. Findings record what was true when observed and are not rewritten when later sprints fix them. Use `docs/CURRENT_ARCHITECTURE.md`, `docs/FLOWS.md`, and `docs/ERRORS_AND_DEBUGGING.md` for current behavior.

## 2026-07-06 Sprint 04 local smoke test findings

**Scope tested:** local bootstrap node, backend API, `/status`, `/models`, one CPU serving node for `facebook/opt-125m` layers `0-12`, `/nodes`, generator startup, `/generator/status`, `/chat`, and a local multi-node split attempt.

**Passing checks:**

- Local bootstrap started on `127.0.0.1:7001` with peer ID `QmY54qgx7Si9KCWXFVdrn4J7kGPfdJUoNHNeTy1JqnDnX7`.
- Backend API started on `127.0.0.1:8000`.
- `/status` returned online with `node_running=false` and `generator_ready=false` before startup.
- `/models` returned the supported model registry and local default bootstrap peers.
- `/node/start` loaded `facebook/opt-125m` layers `0-12` on CPU.
- `/nodes` discovered the serving node with `rpc_uid` `distribllm.0.12`.
- `/generator/start` returned `{"status":"ready"}`.
- `/generator/status` returned `ready=true`, `route_ready=true`, and route trace `12D3KooW… (layers 0→12)`.
- `/chat` with prompt `Hello, my name is` and `max_new_tokens=8` returned without crashing through the local route.

**Observed generated response:**

```json
{"response":" Michael. I��m a writer","node_trace":["12D3KooW… (layers 0→12)"],"tokens_generated":4}
```

This proves the local single-node distributed path executes, but the replacement characters in `I��m` should be watched in later output-quality work.

### 24. Local multi-node split cannot be tested from one backend process

**Observed behavior:** after the full-layer node was running, a second `/node/start` request for layers `4-8` returned:

```json
{"status":"already_running", "info": {"layer_start": 0, "layer_end": 12, "...": "..."}}
```

**Why this matters:** Sprint 04 asks for a local split route such as `0-4`, `4-8`, `8-12` after single-node inference works. The current backend has one global `node`, so one backend process cannot host multiple local layer slices.

**Plan:** resolve in Sprint 06 by choosing either a backend node registry or one backend process per serving participant. Until then, local multi-node split testing requires multiple backend processes or a dedicated test harness.

**Owner:** Sprint 06.

### 25. Node stop/API shutdown can hang after local serving node starts

**Observed behavior:** after the smoke test, `/node/stop` did not return promptly. The API logs showed Hivemind shutdown starting, but the HTTP cleanup call hung. The backend process also did not exit after normal interrupts and had to be force-killed by exact PID.

**Why this matters:** users need reliable stop controls, and Sprint 05 depends on backend stop operations that do not leave the app stuck.

**Plan:** add bounded shutdown behavior for node/RPC/DHT cleanup, move blocking shutdown out of the event loop if needed, and add tests around stop behavior. This should be fixed before exposing primary node stop controls in Sprint 05.

**Partial resolution 2026-07-06:** `RPCServer.stop()` and `Node.stop()` now use bounded shutdown wrappers around Hivemind RPC and DHT shutdown. Focused tests cover stuck RPC and DHT shutdown. A live `/node/stop` check returned `{"status":"stopped"}` in about five seconds instead of hanging.

**Remaining risk:** the live check still left the API parent and Hivemind worker child processes alive after the app reported shutdown complete. They were terminated by exact PID after verification. See finding 27.

### 27. Hivemind worker processes can remain after bounded node shutdown

**Observed behavior:** after the bounded shutdown fix, `/node/stop` returned successfully, but process inspection still showed the backend parent and Hivemind worker children from the verification run until they were terminated.

**Why this matters:** bounded shutdown makes the API usable again, but leftover worker processes can keep resources, ports, or model state alive after a node is supposed to stop.

**Plan:** add explicit worker/process cleanup if Hivemind exposes process handles, or isolate serving nodes in a lifecycle-managed subprocess that can be terminated as a unit. This should be addressed before relying on repeated start/stop cycles in the Electron UI.

**Owner:** Sprint 06 for serving lifecycle strategy, with Sprint 05 blocked from relying on repeated UI start/stop cycles until cleanup is trustworthy.

### 26. OPT-125M local distributed output fails factual/arithmetic sanity checks

**Observed behavior:** local single-node distributed inference executes without crashing, but short sanity prompts are often irrelevant or wrong:

| Prompt | Response |
| --- | --- |
| `Hello, my name is` | `Zoraida. I live in the southern hemisphere and have` |
| `The capital of France is` | `to host the annual G7 summit in early July.` |
| `Once upon a time` | `, I was the youngest to go on this trip and it has been so fun` |
| `The capital of France is` with lower temperature | `the most populous city in Europe.` |
| `2 + 2 =` with lower temperature | `-3*m. Suppose m*` |

**Why this matters:** Sprint 04 has proven the route executes, but users should not trust answer quality yet. The unrelated responses in the Electron screenshots are reproducible as an output-quality problem, especially for factual prompts.

**Likely causes to separate next:**

- `facebook/opt-125m` is a very small base completion model, not an instruction/chat model.
- Sampling settings may still be too loose for sanity checks.
- Prompt formatting is raw completion text, not chat/instruction formatting.
- The distributed split path still needs parity comparison against normal HuggingFace logits for the same model and prompt.

**Plan:** add a local HuggingFace-vs-distributed parity check for OPT-125M logits or greedy next-token outputs before using generated text quality as proof. Until parity passes, treat successful `/chat` as a transport/execution success only.

**Owner:** Sprint 07.

## 2026-07-05 User review findings from Electron screenshots

**Scope reviewed:** Electron Inference, Network, Nodes, and Bootstrap screens during local prototype usage.

### 17. Inference responses are readable but unrelated to the user prompt

**Observed behavior:** prompts such as `test` and `what's your name ?` produced coherent-looking but unrelated completions about driving, family, Canada, and firefighting.

**Likely causes to distinguish during validation:**

- the split inference path still does not match the HuggingFace model forward path
- the selected model is a base completion model rather than an instruction/chat model
- the generator may be using stale state or a fallback/mock response path
- prompt formatting is too raw for the selected model
- the frontend may be mixing connection state and generator readiness

**Plan:** add a readiness/parity gate before trusting generated text. Compare normal local HuggingFace logits against the split path, and make the UI show whether the generator is connected to a complete compatible route.

**Update 2026-07-06:** Sprint 04 output sanity checks reproduced this concern. Local single-node distributed inference returns without crashing, but factual/arithmetic prompts are not reliable. See finding 26.

**Owner:** Sprint 07 for output parity, Sprint 06 for complete-compatible-route semantics, and Sprint 05 for UI readiness display.

### 18. WebSocket UI can show connected while send reports not connected

**Observed behavior:** the Inference screen showed `CONNECTED`, but a model message reported `Error: WebSocket not connected`.

**Likely cause:** the frontend connection state is set optimistically before the WebSocket `onopen` event, or a send is attempted while the socket is still connecting.

**Plan:** make WebSocket state event-driven, queue or block the first send until `onopen`, and keep backend reachability, WebSocket connection, generator readiness, and active streaming as separate UI states.

**Resolution 2026-07-06:** frontend WebSocket creation now waits for `onopen` before marking the socket connected, queues payloads while the socket is connecting, and uses local configurable API/WebSocket base URLs instead of the hardcoded LAN address.

**Owner:** Sprint 04 resolved the first-send lifecycle bug. Sprint 05 owns the broader visible state model.

### 19. Local backend can serve only one node/layer slice

**Observed behavior:** the UI/backend workflow behaves as if only one local serving node can run at a time, preventing local multi-node split testing from one backend process.

**Why this matters:** Sprint 04 needs to prove both single-node full-layer inference and a split route such as `0-4`, `4-8`, `8-12`. The project must either support multiple local node instances in one backend process or document that each local slice requires a separate backend process.

**Plan:** decide between a node registry inside one backend process or a one-process-per-participant testing model. If using a registry, `/status`, `/nodes`, `/node/start`, and `/node/stop` need per-node semantics.

**Owner:** Sprint 06.

### 20. Bootstrap tab is exposed as client workflow

**Observed behavior:** the Network page includes a Bootstrap tab with command-line setup instructions and hardcoding guidance.

**Why this matters:** bootstrap nodes are discovery infrastructure, not model-serving nodes and not a client task. Showing this as a normal product tab confuses the serving/inference workflow.

**Plan:** move bootstrap setup to docs/internal operations. The app should expose serving, inference setup, node visibility, settings, and monitoring. Bootstrap peers can remain advanced/default configuration, but not a primary client tab.

**Owner:** Sprint 05.

### 21. Stop controls are missing or misplaced

**Observed behavior:** stop node is only visible in the Network serving tab, and active inference has no visible stop/cancel control.

**Plan:** expose stop controls where the user is looking: stop local served nodes from the Nodes/main page and cancel active generation from the Inference page.

**Partial resolution 2026-07-06:** active inference cancellation is implemented through a generator stop request and Inference page stop button. Moving local node stop controls to the primary Nodes page remains planned in Sprint 05.

**Owner:** Sprint 04 resolved active inference cancellation. Sprint 05 owns primary node stop placement.

### 22. Monitoring screen is missing from main navigation

**Observed behavior:** there are Nodes, Network, Inference, and Settings views, but no dedicated monitoring screen for the network visualization/status view described in the product direction.

**Plan:** add a fourth main workflow tab/page for monitoring. It should show peer graph/status, layer coverage, model filters, route health, latency, and a clear distinction between bootstrap nodes, serving nodes, and generator clients.

**Owner:** Sprint 05.

### 23. Model access and incentive semantics are not yet explicit

**Open questions:**

- Can a user inference any listed model, or only models with complete compatible served coverage in the current network?
- Are token incentives global across all models, or model-specific/contribution-specific?

**Planned decisions:** inference should be gated by supported model registry plus complete compatible route coverage. Incentives should be model-aware and contribution-aware because hardware cost, layer count, model size, reliability, latency, and successful completed work differ across served models.

**Owner:** Sprint 06.

## 2026-07-02 Backend flow unit-test audit

**Scope tested:** bootstrap argument/DHT setup, backend entrypoint argument parsing, generator streaming loop, route validation, DHT discovery/status handling, RPC server UID construction, and `/nodes` discovery endpoint wiring.

**Verification commands:**

```bash
backend/.venv/bin/python -m pytest backend/tests -q
backend/.venv/bin/python -m compileall backend/main.py backend/bootstrap.py backend/api backend/client backend/node backend/models -q
backend/.venv/bin/python -m pytest /tmp/backend_flow_audit_tests.py -q
```

**Passing checks:**

- Existing backend regression suite passed: 13 passed, 1 warning.
- Backend files compiled successfully with `compileall`.
- `backend/main.py` parses default and override host/port/reload arguments correctly.
- `backend/bootstrap.py` constructs a private Hivemind DHT with `use_ipfs=False`, no initial peers, configured host/port, and stable identity path.
- `DistributedGenerator.generate_stream()` can run a mocked happy path, creates boolean attention masks shaped like token IDs, advances position IDs across steps, streams tokens, and emits a final `done` chunk with node trace.
- FastAPI request models reject invalid model names, invalid max token counts, and invalid HuggingFace token format in normal Python mode.

**Findings from the audit:**

### 11. Route validation accepts nodes serving the wrong model

**Location:** `backend/client/sequential.py`

The audit test passed a full-coverage node with:

```python
model_name = "meta-llama/Llama-3.2-1B"
num_layers = 2
```

`RemoteSequential.validate_route()` accepted the route because it checks coverage and contiguous layer ranges, but it does not verify that DHT node metadata matches the generator's selected model.

**Why this is a bug:** a generator for one model can route hidden states through layers from another model if the layer range happens to cover the expected depth. That can cause shape errors, invalid logits, or silently corrupted inference.

**Verification evidence:** temporary audit test `test_route_validation_rejects_wrong_model_metadata` failed with `AssertionError: RuntimeError not raised`.

### 12. DHT discovery accepts non-integer layer metadata and later crashes status/route checks

**Location:** `backend/client/sequential.py`

`_discover_nodes()` only checks that required keys exist. It accepts metadata like:

```python
{"layer_start": "0", "layer_end": "2"}
```

The malformed node is returned from discovery. Later, `_check_coverage()` calls `range(node["layer_start"], node["layer_end"])`, which raises:

```text
TypeError: 'str' object cannot be interpreted as an integer
```

This breaks both `get_network_status()` and `validate_route()`.

**Why this is a bug:** DHT metadata is untrusted external input. A stale, malformed, or malicious metadata entry can crash network status and readiness checks instead of being skipped with a clear reason.

**Verification evidence:** temporary audit test `test_discovery_rejects_non_integer_layer_metadata_before_status` failed because discovery returned the malformed node. A focused probe then reproduced `TypeError` from both `get_network_status()` and `validate_route()`.

### 13. Coverage check can report complete coverage for invalid negative layer indexes

**Location:** `backend/client/sequential.py`

`_check_coverage()` adds every integer from `layer_start` to `layer_end` without validating that `layer_start >= 0`. A node with `layer_start=-1` and `layer_end=2` produced:

```python
{"complete": True, "covered": [-1, 0, 1], "missing": []}
```

`validate_route()` later rejects the same node as non-contiguous, but status reporting can still claim complete coverage.

**Why this is a bug:** status/readiness output can report a complete network even when the metadata contains invalid layer ranges. Users may see the network as ready before inference fails.

**Verification evidence:** focused unit probe against `_check_coverage()` reproduced a complete coverage result for a negative layer range.

### 14. RPC server UID collides across multiple nodes using the same DHT prefix

**Location:** `backend/node/rpc_server.py`

`RPCServer.start()` always sets:

```python
self._uid = f"{self.dht_prefix}.0.0"
```

Two nodes serving different layer ranges under the same prefix therefore register the same expert UID and announce the same `rpc_uid`.

**Why this is a bug:** multi-node distributed inference needs a unique RPC target per serving node. UID collisions can make DHT expert lookup ambiguous or route multiple layer slices to the same backend registration.

**Verification evidence:** temporary audit test `test_rpc_uid_is_unique_per_node_slice` created two mocked RPC servers for different layer ranges and both registered `audit.0.0`.

### 15. `/nodes` endpoint ignores the active node or generator DHT prefix

**Location:** `backend/api/server.py`

`get_nodes()` creates `RemoteSequential` with the global `DHT_PREFIX` constant:

```python
seq = RemoteSequential(dht=dht, dht_prefix=DHT_PREFIX, num_layers=0)
```

It does not use the prefix from the running node or generator request. If a node or generator was started with a custom `dht_prefix`, `/nodes` still queries the default `distribllm` namespace.

**Why this is a bug:** status and frontend node discovery can show an empty or wrong node list for custom prefixes even when the active node/generator is connected to a different namespace.

**Verification evidence:** temporary audit test `test_nodes_endpoint_uses_active_node_prefix` failed because the endpoint constructed `RemoteSequential` with `distribllm` instead of the expected active prefix.

### 16. Runtime validation still relies heavily on `assert`

**Locations:** `backend/api/server.py`, `backend/client/generation.py`, `backend/client/sequential.py`, `backend/node/handler.py`, `backend/node/node.py`, `backend/node/rpc_server.py`, `backend/node/block_loader.py`

The audit confirmed request validation works in normal Python mode, but many production runtime checks are still written as `assert`. This includes route/input validation, node startup checks, generator readiness checks, and Pydantic validators.

**Why this is a bug:** running Python with optimization enabled removes `assert` statements. In that mode, invalid API inputs or broken runtime state can bypass validation and fail later with less actionable errors.

**Verification evidence:** source audit during the backend flow unit-test pass; this also overlaps with existing issue 2.

**Resolution 2026-07-03:** Sprint 03 implemented tested hardening for findings 11 through 16. `RemoteSequential` now accepts the generator model name, validates discovered DHT node metadata before route planning, rejects wrong-model nodes, ignores malformed/negative layer coverage, and raises explicit runtime errors instead of critical `assert` checks in the touched routing path. `RPCServer` now builds unique Hivemind-compatible UIDs from the DHT prefix and served layer slice. `/nodes` now uses the active node or generator DHT prefix. Backend regression coverage was expanded to 20 tests and passes with `uv run pytest`.

## 1. Remote route can apply layers more than once

**Location:** `backend/client/sequential.py`

- `forward()` sorts all discovered nodes by `layer_start` and calls every node in that order.
- `_check_coverage()` only verifies that every layer index is covered at least once.

If DHT contains overlapping nodes, for example one node serves layers `0-10` and another serves `5-15`, coverage passes because all layers are present. But `forward()` will call both nodes, so layers `5-9` are applied twice.

**Why this is a bug:** transformer layers must run exactly once, in order. Duplicate or overlapping ranges corrupt the hidden states.

**Fix:** build and validate a single contiguous, non-overlapping route before calling nodes. Reject routes with gaps, overlaps, duplicate `layer_start`, or ranges outside `0..num_layers`.

## 2. Runtime checks use `assert`

**Location:** `backend/client/sequential.py`

Several runtime checks use `assert`, including:

- `hidden_states.dim() == 3`
- `rpc_uid` exists
- remote expert exists
- remote output is not `None`

**Why this is a bug:** Python removes `assert` statements when running with optimization enabled, for example `python -O`. That can turn useful validation into later, harder-to-debug failures.

**Fix:** replace runtime `assert` checks with explicit exceptions such as `ValueError` or `RuntimeError`.

## 3. `REQUEST_TIMEOUT` is unused

**Location:** `backend/client/sequential.py`

`REQUEST_TIMEOUT = 30` is defined but never passed into DHT lookup or remote expert calls.

**Why this is a bug:** a slow or dead remote call may block longer than expected, even though the code appears to define a timeout.

**Fix:** pass timeout parameters to the Hivemind APIs if supported by the installed version, or wrap remote calls with a timeout at the caller level.

## 4. DHT metadata is not fully validated

**Location:** `backend/client/sequential.py`

`_discover_nodes()` checks that required fields exist, but it does not validate that:

- `layer_start` and `layer_end` are integers
- `layer_start >= 0`
- `layer_end > layer_start`
- `layer_end <= num_layers`
- `rpc_uid` is a non-empty string

**Why this is a bug:** bad or stale DHT metadata can crash coverage checks or produce an invalid route.

**Fix:** validate metadata before appending it to `nodes`, and skip/log invalid entries.

## 5. Unused code

**Location:** `backend/client/sequential.py`

- `sys` is imported but unused.
- `_exc_summary()` is defined but unused.

**Fix:** remove them unless they are needed for upcoming logging changes.

## 6. Attention mask dtype and shape bug causing remote node failure

**Error:**

```text
Error: Node 12D3KooW failed after 3 attempts. Last error: RuntimeError('Expected attn_mask dtype to be bool or float or to match query dtype, but got attn_mask.dtype: long int and query.dtype: c10::Half instead.')
```

**Primary location:** `backend/client/generation.py`

The attention mask is created like this:

```python
attention_mask = torch.arange(
    generated_ids.shape[0], device=self.device
).unsqueeze(0)
```

There are two problems:

1. `torch.arange(...)` creates an integer tensor by default, so the mask dtype is `torch.long`.
2. `generated_ids.shape[0]` is the batch size, not the sequence length. For batch size `1`, this creates a mask shaped `[1, 1]` instead of `[1, seq_len]`.

The mask is then passed into:

- `backend/client/sequential.py`, `RemoteSequential.forward(...)`
- `backend/client/sequential.py`, `_rpc_forward(...)`
- `backend/node/handler.py`, `InferenceHandler.forward(...)`
- each transformer layer as `attention_mask=attention_mask`

Inside the node, `hidden_states` is converted to the node dtype:

```python
hidden_states = hidden_states.to(self.device, dtype=self.dtype)
```

When the node runs in fp16, the attention query tensor is `c10::Half`, but the attention mask is still `torch.long`. PyTorch attention rejects this:

```text
Expected attn_mask dtype to be bool or float or to match query dtype
```

**Why this is a bug:** attention masks are not token positions. They should mark valid tokens, usually with `1`s for real tokens and `0`s for padding, and they must have a dtype accepted by the model attention implementation.

**Fix option A, recommended:** create a proper boolean attention mask in `generation.py`:

```python
attention_mask = torch.ones(
    generated_ids.shape,
    device=self.device,
    dtype=torch.bool,
)
```

Keep `position_ids` as integer positions:

```python
position_ids = torch.arange(
    generated_ids.shape[1],
    device=self.device,
    dtype=torch.long,
).unsqueeze(0)
```

**Fix option B:** if the specific HuggingFace model expects an additive float mask, convert the mask to the same dtype as `hidden_states` and use the shape expected by that model.

```python
attention_mask = torch.ones(
    generated_ids.shape,
    device=self.device,
    dtype=hidden_states.dtype,
)
```

**Additional hardening:** in `backend/node/handler.py`, normalize incoming masks before passing them to layers. For example, convert integer masks to `bool` or to `hidden_states.dtype`, depending on the model family.

## 7. No stop/cancel control for an active inference run

**Location:** inference flow / API / UI

There does not appear to be a clear way to stop inference for a specific model once generation has started. If a model call hangs, is slow, or the user realizes they selected the wrong model, the flow relies on the request finishing or failing by itself.

**Why this is a bug:** distributed inference can be slow or unreliable. Without cancellation, users may get stuck waiting, backend resources stay occupied, and the UI cannot cleanly recover from long-running generation.

**Fix:** add explicit cancellation support for active inference jobs. A practical design would assign each generation request a job id, track active jobs by model/session, expose a stop endpoint or WebSocket message, and have the generation loop check a cancellation flag between token steps and before remote node calls.

## 8. No pre-inference validation before running a model

**Location:** inference flow / generator startup / inference tab

The system should validate that the selected model is actually ready before inference begins. Right now, failures can surface only after the user starts generation in the inference tab.

**Why this is a bug:** users get a late runtime error instead of an early, actionable readiness message. This is especially painful in a distributed setup where the model may fail because nodes are missing, DHT metadata is stale, layer coverage is incomplete, RPC is unavailable, or the selected model does not match the active node configuration.

**Fix:** add a readiness validation step before enabling or starting inference. At minimum, check that:

- the generator is loaded
- the selected model is supported
- the DHT has reachable nodes for the selected prefix
- layer coverage is complete for the model depth
- the planned route is contiguous and non-overlapping
- every selected node has a valid `rpc_uid`
- optionally, a lightweight health/probe call succeeds before the first real generation request

If validation fails, show the reason before inference starts instead of letting the error appear during generation.

## 9. OPT inference output is incoherent because decoder-specific preprocessing is skipped

**Observed output:** `facebook/opt-1.3b` responds with repetitive, low-quality text such as repeated `by`, `course`, `when`, punctuation fragments, and malformed continuations.

**Location:** `backend/client/generation.py`, `backend/node/block_loader.py`, `backend/node/handler.py`

The distributed inference path manually splits the model into:

- local `embed_tokens`
- remote transformer decoder layers
- local final norm and `lm_head`

For OPT, this is not equivalent to the normal HuggingFace `OPTForCausalLM.forward(...)` path. The current client creates hidden states with only:

```python
hidden_states = self.embed_tokens(generated_ids)
```

But OPT decoder normally also applies decoder-specific preprocessing before the layers, especially learned positional embeddings. Depending on the OPT variant, it may also apply projection layers such as `project_in` / `project_out`, and it prepares the causal attention mask in the shape expected by OPT decoder layers.

The current remote node runs raw decoder layers directly:

```python
out = layer(
    hidden_states,
    attention_mask=attention_mask,
    position_ids=position_ids,
)
```

That bypasses the architecture wrapper that normally prepares the hidden states and attention mask. Even if the RPC succeeds, the model is not receiving the same inputs it was trained with, so logits become unstable and generation collapses into repeated fragments.

**Why this is a bug:** distributed inference must reproduce the original model forward pass exactly. Token embeddings alone are not enough for OPT because layer inputs need positional information and model-specific mask handling.

**Fix:** add architecture-specific model adapters instead of treating all decoder-only models the same. For OPT, the adapter should:

- create token embeddings
- add OPT learned positional embeddings before the first remote layer
- prepare the causal attention mask in the format expected by OPT decoder layers
- apply `project_in` before remote layers if the model defines it
- apply final layer norm and `project_out` after remote layers if the model defines them
- then apply `lm_head`

Also add a local parity test: run a short prompt through the normal HuggingFace model and through the distributed split path with all layers on one node, then compare logits for the final token. The outputs should be close before trusting generated text quality.

**Note:** `facebook/opt-1.3b` is a base completion model, not an instruction/chat model, so it may still answer conversational prompts poorly. However, the repeated broken text shown in the inference tab is more consistent with a broken forward path than normal base-model behavior.

## 10. Node and generator startup fail because the DHT bootstrap peers cannot be reached

**Observed during runtime smoke testing:**

- The FastAPI app itself imported and the `/status` endpoint responded successfully.
- Calling `/node/start` and `/generator/start` failed before the node or generator became ready.
- The backend reported the following startup error:

```text
Daemon failed to start: ... failed to connect to bootstrap peers
```

**Where to inspect:**

- `backend/bootstrap.py`
- `backend/constants.py`
- `backend/api/server.py`
- `backend/node/node.py`

**Likely causes:**

- The bootstrap node is not running or is not reachable from the client.
- The runtime peer address from `DISTRIBLLM_INITIAL_PEERS` is stale, incorrect, or mismatched with the running bootstrap node; development fallback peers remain in `backend/constants.py`.
- Network or firewall restrictions are blocking the DHT connection.

**Verification evidence:**

- Reproduced by invoking the startup endpoints through the FastAPI test client; both returned the same bootstrap-peer connection failure.

**2026-07-13 smoke-test recurrence:** a fresh full TinyLlama smoke pass stopped at `/node/start` with the same `failed to connect to bootstrap peers` error for the configured public VPS address. No node or client DHT remained registered after cleanup. This blocked model loading, route validation, and prompt generation for that pass; it does not invalidate the separate successful verification of the local expert-routing fix in issue 11.

## 11. Route readiness passes but RPC peer routing is not found

**Initial harness observation:**

- TinyLlama layers `0-22` loaded successfully on CUDA.
- The RPC expert started and announced as `distribllm.0.22` through the public VPS bootstrap.
- The generator loaded its local components successfully.
- The first deterministic prompt failed after three retries before producing any token.

```text
failed to dial: dial to self attempted
Node ... failed after 3 attempts
```

The first ad hoc test reused the serving node's DHT object and produced `dial to self attempted`. That harness did not match the application lifecycle and was corrected.

**Corrected application-topology observation:**

- `/node/start` semantics created a full TinyLlama `0-22` CUDA serving node with its own peer identity.
- `/generator/start` semantics created a separate `client_dht` identity and loaded the generator.
- `/generator/status` returned `ready=true`, `route_ready=true`, and route `0-22`.
- The first prompt failed before producing a token after three RPC retries:

```text
routing: not found
Node ... failed after 3 attempts
```

**Current interpretation:**

Application-level DHT metadata discovery and route validation can see the serving node, but Hivemind expert lookup cannot resolve or route to its P2P peer. The serving node advertises WSL loopback/private addresses while both peers enter through the public VPS bootstrap. Bootstrap discovery success does not currently guarantee an RPC-dialable path.

**Cleanup observation:**

Node/RPC shutdown completed, but Hivemind destructors later reported no current event loop and a pending control-client task. This should be checked after the generator and serving node run in separate processes.

**Status:** confirmed transport/readiness defect. Same-machine serving and generation remain intended and previously worked with local bootstrap, but the VPS-backed route currently reports ready before proving expert peer reachability. No prompt response was produced, cleanup returned stopped for both DHT roles, and no fix was attempted during the smoke-test pass.

**2026-07-13 fix and verification:** generator startup now adds matching local node multiaddresses as direct initial peers, and readiness probes expert RPC metadata. The corrected live test passed routing for three prompts through TinyLlama layers `0-22`; this transport issue is fixed for same-machine serving/generation with a VPS bootstrap.

## 12. TinyLlama chat route returns an empty response for raw prompts

**Observed after RPC routing was fixed:**

- Full TinyLlama `0-22` route was ready and expert RPC probes passed.
- Three deterministic prompts completed through the route without transport errors.
- Every `/chat` result contained an empty response and zero visible tokens.

```text
response: ""
tokens_generated: 0
```

**Likely cause:**

`/chat` passes the raw user string directly to `DistributedGenerator.generate_stream`. TinyLlama Chat expects its tokenizer chat template with user/assistant role markers and a generation prompt. Greedy decoding selected an immediate special/end token, which is omitted by `skip_special_tokens=True`, leaving no visible text.

**Fix direction:**

Format prompts according to registry tuning metadata. Chat/instruct models should use `tokenizer.apply_chat_template(..., add_generation_prompt=True)` when supported, while base models must preserve raw completion prompts. Add tests that prevent double-formatting and verify a visible TinyLlama response.

**Status:** confirmed prompt-formatting/product behavior issue. It was discovered after the routing fix and was not changed during the same smoke-test pass.

**2026-07-13 retest status:** a planned chat-template probe could not reach prompt generation because the public VPS bootstrap connection failed during node startup. The prompt-formatting issue therefore remains open and was not reclassified by this pass.

**2026-07-13 successful template probe:** after the VPS bootstrap became reachable, a full TinyLlama `0-22` CUDA node, separate generator, reachable-route probe, and three manually chat-templated prompts all completed. The answers were non-empty and semantically correct, confirming that the model and distributed route work when the template is supplied. Production `/chat` still passes raw input and therefore still needs model-aware template application.

## 13. Streaming token decoding removes spaces from generated text

**Observed during the successful TinyLlama template smoke test:**

- Capital prompt: `ThecapitalofFranceisParis.`
- Arithmetic prompt: `2+2=4`
- Distributed-computing prompt: words were concatenated throughout the sentence.

All three responses were semantically correct and traversed the complete remote `0-22` route, but normal spaces between words were missing.

**Likely cause:** `backend/client/generation.py` decodes each generated token independently and immediately yields that fragment. Tokenizers such as TinyLlama's encode word boundaries in token context, so independent decoding can lose spacing that would be preserved when decoding the complete generated token sequence.

**Fix direction:** preserve generated token identifiers and derive each stream delta from context-aware decoding, or use the tokenizer's supported streaming decoder. Add a regression test proving that streamed fragments concatenate to the same visible text as decoding the full generated sequence once.

**Status:** confirmed output-formatting defect. It was recorded but not fixed during the smoke-test pass.
