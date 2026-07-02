# Current Architecture

## High-Level Shape

DistribLLM is currently a single backend service plus an Electron frontend.

The backend can act as:

1. a serving node that hosts a slice of model layers
2. a client/generator that routes inference through discovered nodes
3. a dashboard API for node and hardware status

Those roles are all hosted inside one FastAPI process today. This makes local testing simple, but it also creates global state limitations.

## Main Components

### Electron Frontend

The frontend provides four pages:

- Dashboard: backend status, hardware stats, discovered nodes
- Network: start a node, stop a node, start generator, view bootstrap instructions
- Inference: WebSocket token streaming chat
- Settings: HuggingFace token storage

The frontend talks to the backend through `frontend/src/renderer/src/api/client.ts`.

Important current limitation: the backend URL is hardcoded to `http://172.27.32.227:8000` and `ws://172.27.32.227:8000`.

### FastAPI Backend

The main API is in `backend/api/server.py`.

It owns these global variables:

```python
gpu_monitor: Optional[GPUMonitor] = None
node: Optional[Node] = None
generator: Optional[DistributedGenerator] = None
client_dht: Optional[hivemind.DHT] = None
```

This means:

- only one serving node can run per backend process
- only one generator can be active per backend process
- there is no per-session or per-model job isolation
- stopping one generator or node means mutating global state

### Bootstrap Node

`backend/bootstrap.py` starts a DHT entry point with a stable identity. It is not a model-serving node. Its job is to let peers find each other.

Current default peers are stored in `backend/constants.py`.

### Serving Node

`Node.start()` does four steps:

1. start a Hivemind DHT client
2. load a range of transformer layers
3. expose those layers through Hivemind RPC
4. announce metadata to the DHT

DHT metadata includes:

```python
{
    "peer_id": "...",
    "model_name": "...",
    "layer_start": 0,
    "layer_end": 24,
    "device": "cuda",
    "layers_loaded": True,
    "rpc_running": True,
    "rpc_uid": "...",
    "timestamp": ...
}
```

### Remote Sequential Client

`RemoteSequential` discovers nodes by reading:

- `{dht_prefix}.members`
- `{dht_prefix}.node_info.{peer_id}`

Then it:

1. checks that every layer index is covered
2. sorts discovered nodes by `layer_start`
3. sends hidden states through every node

Current limitation: it checks coverage but does not choose a clean route. Overlapping node ranges can be called twice.

### Generator

`DistributedGenerator` loads:

- tokenizer
- token embeddings
- final norm
- LM head

Then each token step does:

1. embed full `generated_ids`
2. create attention mask and position ids
3. call remote sequential layers
4. apply final norm and LM head locally
5. sample next token
6. append token and repeat

Current limitation: this split is architecture-sensitive. For OPT, token embeddings alone are not enough because the real decoder adds learned positional embeddings and prepares internal masks.

## Current Data Plane

The data plane is:

```text
generated token ids
  -> local token embeddings
  -> remote node layers
  -> local final norm
  -> local lm_head
  -> sampled token
```

The model weights are split by layer range, but there is no KV cache, session routing, or route reuse yet.

## Current Control Plane

The control plane is:

```text
frontend
  -> FastAPI endpoint
  -> Hivemind DHT
  -> node metadata
  -> Hivemind RPC expert UID
```

DHT is used for node discovery. RPC is used for tensor forwarding.

## Current Trust Assumptions

The code currently trusts:

- DHT members list is fresh
- DHT node metadata is valid
- all discovered nodes are compatible with the requested model
- layer ranges do not overlap
- `rpc_uid` resolves to the expected serving node
- calling raw layer modules reproduces the original model forward path

Those assumptions are the main source of current bugs.

