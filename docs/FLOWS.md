# Runtime Flows

## 1. Bootstrap Operations

```text
operator starts backend/bootstrap.py with persistent bootstrap.id
  -> bootstrap listens on configured TCP port
  -> relay support and the direct-reachability checker start
  -> loopback and public multiaddresses are printed
  -> public address is placed in DISTRIBLLM_INITIAL_PEERS
  -> serving nodes and generators use it for discovery and relay fallback
```

The bootstrap is infrastructure, not a model-serving node or normal client tab. Preserve `bootstrap.id`; replacing it changes the peer ID. Processes on the VPS may use its loopback address, while laptops, Colab, and other machines must use the public address. The same first VPS process may provide both bootstrap discovery and circuit forwarding, although those are separate responsibilities.

## 2. Public Model Serving

```text
Network -> Serve Layers
  -> select an open model, layer range, and device
  -> POST /node/start
  -> backend explicitly disables Hugging Face credentials
  -> Node tests direct reachability with relay disabled
  -> Node starts direct, or reserves an outbound circuit-relay path
  -> Transformers downloads/loads the model anonymously
  -> selected layers are retained on CPU or CUDA
  -> RPC server starts and model/transport metadata is announced
```

Public loading uses `token=False`, so expired OAuth or Hugging Face CLI credentials cannot turn a public request into a 401 failure.

## 2A. Transactional Layer Placement

```text
Network -> Recommended or Custom
  -> backend submits participant identity, model revision, and capacity
  -> coordinator serializes allocation in one SQLite write transaction
  -> reservation returns exact range, token, topology revision, and expiry
  -> backend marks JOINING before model load
  -> worker loads layers, starts its peer-addressed RPC, and publishes metadata
  -> backend cross-checks exact peer, RPC UID, model revision, and fresh publications
  -> coordinator marks the lease ONLINE
  -> authenticated heartbeats renew the lease
  -> stop/delete releases it; crash or abandoned startup expires it
```

The renderer displays the coordinator decision but never calculates ownership.
New starts fail closed when the authority is unavailable. An existing online
worker can survive a short outage, but its local safety deadline stops service
before the coordinator may reallocate the expired range.

## 3. Gated Model Connection and Download

```text
user accepts model terms / receives access on Hugging Face
  -> Connect Hugging Face
  -> POST /settings/huggingface/oauth/device
  -> browser opens https://hf.co/oauth/device
  -> user signs in and authorizes the displayed code
  -> frontend polls /settings/huggingface/oauth/device/poll
  -> scoped OAuth token is stored in user config storage
  -> Download Approved Model starts a background snapshot job
  -> frontend polls job status and may request cancellation
  -> completed snapshot is validated and registered locally
```

DistribLLM never receives the Hugging Face password and never asks the user to paste a personal access token. OAuth still gives the app a scoped local access token for downloads. Disconnect deletes that stored auth state.

If browser opening fails in WSL/headless environments, the UI shows the verification URL and code for manual opening. The Electron main process permits only Hugging Face hosts for this action.

## 3A. Windows Managed Backend Startup

```text
Packaged Electron app starts
  -> loads non-secret launcher configuration from Electron user data
  -> checks wsl.exe and the configured distro
  -> validates WSL backend path plus bootstrap/relay settings
  -> optionally runs uv sync --python 3.12
  -> rejects an already occupied loopback backend port
  -> launches uv run --python 3.12 python main.py through wsl.exe
  -> polls /status until ready, process exit, or timeout
  -> publishes lifecycle state and diagnostics over preload IPC
```

Restart and app shutdown use the launcher's WSL PID file to terminate the backend process without terminating the distro or deleting durable state.

## 4. Local Import Fallback

```text
user downloads approved model outside DistribLLM
  -> Browse selects a local directory
  -> POST /settings/local-models/inspect
  -> validate config, architecture, dimensions, tokenizer, and weight shards
  -> POST /settings/local-models
  -> sanitized registry metadata is stored
```

Normal list/inspect responses avoid echoing raw filesystem paths. Startup revalidates registered snapshots so moved, missing, or incomplete folders fail before expensive loading.

## 5. Gated Runtime Startup

```text
/node/start or /generator/start
  -> require a validated local import for gated model
  -> use local snapshot path
  -> local_files_only=True
  -> no OAuth token is passed to Transformers
```

This allows offline startup after a successful download/import. OAuth expiry affects future downloads, not an already validated local runtime.

## 6. Node Lifecycle

```text
start
  -> reserve placement -> direct probe -> direct DHT or relay reservation
  -> layer load -> RPC -> announce -> exact readiness attestation -> ONLINE lease

turn off
  -> stop announcing/RPC/DHT serving handles -> release placement
  -> preserve loaded layers

turn on
  -> reserve the preserved custom range -> reconnect DHT/RPC and reannounce
  -> attest exact readiness -> ONLINE lease

delete
  -> stop RPC/DHT -> release placement -> unload layers -> remove local replica
```

Multiple local nodes may serve non-overlapping ranges under one prefix. Overlapping local ranges and mixed local prefixes are rejected. Failed startup cleans partial resources before returning an actionable error.

## 7. Generator and Route Readiness

```text
Network -> Run Inference
  -> POST /generator/start
  -> create client DHT
  -> load tokenizer/embeddings/norm/LM head
  -> record full startup and local-component load duration
  -> GET /generator/status
  -> discover compatible nodes
  -> validate a complete contiguous non-overlapping route
  -> resolve every selected expert and probe RPC metadata
  -> report route validation duration
  -> enable inference only when ready
```

The model registry distinguishes supported models from currently runnable models. Runnable requires complete compatible DHT coverage.

## 8. Streaming Inference

```text
Inference page opens /stream WebSocket
  -> validates generator and route readiness
  -> applies the publisher chat template once for chat/instruct models, or keeps a raw base-model prompt
  -> tokenizes the resulting prompt
  -> prepares architecture-specific inputs
  -> sends hidden states through selected RPC route
  -> optionally verifies signed useful-work receipts and queues generator acceptance
  -> applies local output components
  -> samples/decodes next token
  -> records first-token, total, throughput, and per-hop RPC timings
  -> cumulatively decodes generated token ids and streams stable text deltas
  -> sends route trace and completion metrics
```

The user can request cancellation between token steps. Diagnostic endpoints can compare next-token logits/generated output with direct Hugging Face execution and write redacted JSON traces.

## 9. Remote Worker Flow

`backend/colab_worker.py` runs a headless serving node:

```text
clone testing branch on remote machine
  -> install locked backend environment
  -> optional browser OAuth for gated model
  -> connect to public bootstrap address
  -> load assigned layer range
  -> start RPC and announce
  -> keep process/cell alive
```

Every worker currently downloads/builds the complete model before retaining its range. Colab therefore needs both GPU availability and sufficient system RAM. Joining the DHT does not guarantee inbound RPC reachability through Colab NAT.

In automatic network mode, a NAT-separated worker keeps an outbound reservation to a trusted public relay and advertises a circuit route. A generator dials the relay, which forwards the encrypted libp2p stream to the worker. Directly reachable workers avoid the extra hop.

## 10. Monitoring and Cleanup

- `/nodes/local` supplies process-owned nodes for lifecycle controls, while `/nodes` supplies DHT-discovered and local peers for network-wide Monitoring. `/status`, `/stats`, `/models`, and `/generator/status` drive readiness and performance views. Monitoring combines sampled machine/process/GPU metrics with the latest completed generation and aggregated RPC-hop timings.
- `/incentives/accounting` reports read-only useful-work mode, public identity, verified credits, receipt counts, useful positions, queue state, and settlement connectivity. It never exposes private keys, transfers, withdrawals, or claims.
- Managed Hugging Face snapshots can be removed with file deletion; arbitrary manual folders are unregistered but not recursively deleted.
- Backend shutdown uses bounded cleanup for local nodes and the generator DHT.
- Trace files live under `backend/traces/` or `DISTRIBLLM_TRACE_DIR` and remain gitignored.

## 11. Useful-Work Settlement

```text
off mode
  -> use the unchanged legacy inference expert

shadow or credit mode
  -> worker advertises signed application presence and a separate receipt expert
  -> generator signs request, selected route, tensor commitment, and counters
  -> worker verifies request and route membership, runs layers, and signs response receipt
  -> generator verifies output, countersigns acceptance, and queues settlement submission
  -> settlement validates signatures, freshness, route, bounds, revision, and replay keys
  -> shadow stores the pair without balance change; credit appends a ledger entry
```

Receipt failure falls back to legacy inference without credit. Failed RPCs, rejected output, advertisements, idle nodes, and standby providers do not produce accepted receipt pairs.
