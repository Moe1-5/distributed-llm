# Petals Comparison

Reference repository: https://github.com/bigscience-workshop/petals

Petals is a mature system for collaborative, distributed LLM inference and fine-tuning. This project is similar in broad idea: peers host model blocks, clients discover peers, and inference routes hidden states through remote blocks.

## What This Project Shares With Petals

### Layer/Block Serving

Both systems split a large model into sequential transformer blocks. Servers host a contiguous range of blocks and expose them over the network.

In this project:

- `backend/node/block_loader.py` extracts a slice of decoder layers.
- `backend/node/handler.py` runs that slice.
- `backend/node/rpc_server.py` exposes it with Hivemind.

### DHT-Based Discovery

Both systems use DHT-style discovery so clients can find which peers serve which model blocks.

In this project:

- serving nodes announce metadata to `{prefix}.node_info.{peer_id}`
- clients read `{prefix}.members`
- clients fetch each peer's metadata

### Client-Side Routing

Both systems need a client that chooses a route through remote blocks.

In this project:

- `RemoteSequential` discovers nodes, checks layer coverage, sorts nodes, and forwards through them.

Petals has a more advanced routing layer that tracks peer availability and selects usable spans rather than blindly calling every discovered peer.

### Private Swarm Option

Petals can run over a swarm of peers. This project intentionally uses `use_ipfs=False` and a private bootstrap node so it does not accidentally join public Petals/IPFS infrastructure.

## What Petals Has That This Project Does Not Yet Have

### 1. Robust Route Selection

Petals-style clients need to select a path through model blocks based on availability, performance, and block ranges.

This project currently:

- only checks that each layer is covered at least once
- does not reject overlaps
- does not choose among duplicate providers
- does not keep route performance history

Needed direction:

- build a route planner that outputs exactly one contiguous chain from layer `0` to `num_layers`
- reject gaps and overlaps
- prefer healthy nodes
- cache routes for a session

### 2. Session and KV Cache Management

Petals supports efficient autoregressive inference by managing state across repeated token steps.

This project currently sends the full sequence hidden states on every token step and does not use a distributed KV cache.

Needed direction:

- introduce generation sessions
- keep route stable during a session
- support per-node cache state if the serving layer API supports it
- only process the new token once cache support exists

### 3. Model-Specific Adapters

Petals handles model implementations carefully so the distributed path matches the original model forward pass.

This project currently treats many decoder-only architectures as if they can all be split into:

```text
embed_tokens -> raw decoder layers -> final_norm -> lm_head
```

That is not always true. OPT, Llama, Mistral, and GPT-style models differ in positional embeddings, rotary embeddings, mask formats, projection layers, and cache structures.

Needed direction:

- add per-architecture adapters
- make each adapter own embeddings, masks, positions, final projection, and output head logic
- add parity tests against normal HuggingFace forward output

### 4. Health Checks and Readiness

Petals-style systems need proactive health checks because remote peers can disappear.

This project currently discovers failures during generation.

Needed direction:

- preflight DHT connectivity
- validate model match
- validate full route
- check every selected node has `rpc_uid`
- optionally run a lightweight RPC probe
- expose readiness status to frontend

### 5. Cancellation and Job Control

This project has no stop button for active inference. Petals-like systems need cancellation because remote inference can be slow or fail mid-route.

Needed direction:

- assign job ids to generation requests
- expose stop/cancel endpoint or WebSocket message
- check cancellation between token steps and remote calls

### 6. Concurrency Model

This project uses process-global `node` and `generator` objects.

Needed direction:

- decide whether one backend is one local participant, or whether it can manage multiple nodes/generators
- if multiple, replace globals with registries keyed by model, prefix, node id, or session id

## Design Lesson From Petals

The biggest lesson is that distributed LLM inference is not just "send tensors through remote layers." The hard parts are:

- reproducing the exact model forward pass
- choosing reliable routes
- managing session state
- handling peers that appear, disappear, or slow down
- validating readiness before user-facing inference starts

This project has a good prototype skeleton. The next step is to make correctness explicit before expanding model support.

## Useful Petals References

- Repository: https://github.com/bigscience-workshop/petals
- Project documentation: https://github.com/bigscience-workshop/petals/tree/main/docs
- Source tree: https://github.com/bigscience-workshop/petals/tree/main/src/petals
- Client routing area: https://github.com/bigscience-workshop/petals/tree/main/src/petals/client
- Server area: https://github.com/bigscience-workshop/petals/tree/main/src/petals/server

