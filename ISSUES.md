# Code Issues

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
- The peer address in `backend/constants.py` is stale, incorrect, or mismatched with the running bootstrap node.
- Network or firewall restrictions are blocking the DHT connection.

**Verification evidence:**

- Reproduced by invoking the startup endpoints through the FastAPI test client; both returned the same bootstrap-peer connection failure.
