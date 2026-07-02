# Errors and Debugging

## Error: WebSocket not connected

### Symptom

The inference tab shows:

```text
Error: WebSocket not connected
```

### Likely Cause

`Chat.tsx` calls `ensureConnected()`, and `createStreamSocket(...)` returns immediately after constructing the WebSocket. The socket may still be in `CONNECTING` state. If the UI sends immediately before `onopen`, `api/client.ts` sees `ws.readyState !== WebSocket.OPEN` and emits `WebSocket not connected`.

### Where to Inspect

- `frontend/src/renderer/src/pages/Chat.tsx`
- `frontend/src/renderer/src/api/client.ts`

### Fix Direction

Make `createStreamSocket` expose an `onopen`/ready promise or queue the first message until the socket opens.

## Error: Attention mask dtype mismatch

### Symptom

```text
Expected attn_mask dtype to be bool or float or to match query dtype,
but got attn_mask.dtype: long int and query.dtype: c10::Half instead.
```

### Root Cause

The previous code created the attention mask with `torch.arange(...)`, producing a `torch.long` tensor. The remote node converted hidden states to fp16, but the mask remained long.

### Current Fix

`backend/client/generation.py` now creates:

```python
attention_mask = torch.ones(
    generated_ids.shape,
    device=self.device,
    dtype=torch.bool,
)
```

Position ids remain `torch.long`.

## Bad Output From facebook/opt-1.3b

### Symptom

The model produces repetitive broken text:

```text
by ... course ... when ... punctuation fragments ...
```

### Likely Cause

The distributed split is not equivalent to OPT's normal forward pass. OPT needs decoder-specific preprocessing such as learned positional embeddings and model-specific mask handling. The current path only uses token embeddings before remote layers.

### Where to Inspect

- `backend/client/generation.py`
- `backend/node/block_loader.py`
- `backend/node/handler.py`

### Fix Direction

Create an OPT adapter that mirrors HuggingFace OPT decoder behavior before and after remote layers.

## Error: No nodes found on the DHT

### Symptom

```text
No nodes found on the DHT. Make sure at least one node is running.
```

### Possible Causes

- no serving node is running
- client and server use different `dht_prefix`
- bootstrap peer is unreachable
- node announce failed
- members list is stale or missing
- node metadata expired

### Where to Inspect

- `backend/node/node.py`, `_announce`
- `backend/client/sequential.py`, `_discover_nodes`
- backend logs from `/node/start`

## Error: Incomplete layer coverage

### Symptom

```text
Incomplete layer coverage - missing: [...]
```

### Root Cause

Discovered nodes do not cover every layer from `0` to `num_layers - 1`.

### Fix Direction

Start nodes that collectively cover the whole model. Longer term, route validation should show this before inference begins.

## Error: Node failed after 3 attempts

### Symptom

```text
Node 12D3KooW failed after 3 attempts. Last error: ...
```

### Possible Causes

- RPC server stopped
- `rpc_uid` is stale
- remote layer call raised a model error
- tensor shape/dtype mismatch
- peer is unreachable
- Hivemind expert lookup resolved but call failed

### Where to Inspect

- node backend logs
- `backend/client/sequential.py`, `_call_node` and `_rpc_forward`
- `backend/node/handler.py`, `forward`

## Error: Generator not ready

### Symptom

HTTP `/chat` returns `503`.

### Root Cause

`generator` global is `None` or `generator.is_loaded()` is false.

### Fix Direction

Start generator from the Network page before using the inference tab. Longer term, frontend should use a readiness endpoint and block sending until ready.

## Frontend Cannot Reach Backend

### Symptom

Dashboard says backend not reachable, or all API calls fail.

### Likely Cause

The frontend API client hardcodes:

```ts
const BASE_URL = "http://172.27.32.227:8000";
const WS_URL = "ws://172.27.32.227:8000";
```

If the backend is running elsewhere, requests fail.

### Fix Direction

Make backend URL configurable through environment variables or settings.

## Error: DHT bootstrap peers cannot be reached

### Symptom

The backend can start its API layer, but `/node/start` and `/generator/start` fail before a node or generator becomes ready. The reported error is:

```text
Daemon failed to start: ... failed to connect to bootstrap peers
```

### What was observed

- `/status` returned successfully.
- `/node/start` and `/generator/start` both failed with the same DHT bootstrap error.
- This prevented the distributed generation path from being exercised end to end.

### Where to inspect

- `backend/bootstrap.py`
- `backend/constants.py`
- `backend/api/server.py`
- `backend/node/node.py`

### Likely causes

- The bootstrap node is not running.
- The configured bootstrap address does not match the actual bootstrap node.
- The host/port or network path is blocked.
- The identity or peer metadata is stale or mismatched.

## Debugging Checklist

1. Is the bootstrap node running?
2. Does `backend/constants.py` contain the correct bootstrap peer?
3. Is at least one serving node running?
4. Does `/nodes` show the node?
5. Does the node metadata model match the generator model?
6. Does the route cover all layers exactly once?
7. Does every route node have `rpc_uid`?
8. Does generator readiness pass before inference?
9. Does the model have a correct architecture adapter?
10. Can a local split parity test reproduce HuggingFace logits?
