# Errors and Debugging

Start with the first meaningful exception. Later Transformers/Hivemind wrapper messages often obscure the root cause.

## Backend or WebSocket Unreachable

Symptoms:

```text
Backend not reachable
WebSocket not connected
curl: failed to connect to 127.0.0.1:8000
```

Checks:

1. Start `backend/main.py` in the same host/network namespace as Electron.
2. Confirm `http://127.0.0.1:8000/status` responds.
3. Verify `VITE_API_BASE_URL` and `VITE_WS_BASE_URL` if the backend is remote or uses another port.
4. Restart Electron/Vite after changing frontend environment variables.

The stream client waits for real WebSocket lifecycle events and inference remains gated by generator/route readiness.

## Bootstrap Peer Unreachable

Symptoms:

```text
failed to connect to bootstrap peers
Daemon failed to start in 15.0 seconds
```

The first message means TCP/libp2p bootstrap connection failed. A generic readiness timeout can also mean duplicate/stuck `p2pd` processes or runtime incompatibility.

Checks on the VPS:

```bash
pgrep -af "bootstrap.py|p2pd"
sudo ss -ltnp | grep 7001
sudo ufw status
```

Checks from a remote client:

```powershell
Test-NetConnection <public-ip> -Port 7001
```

Use the public multiaddress for remote machines and the loopback address only for processes on the VPS itself. Configure peers in root `.env`:

```env
DISTRIBLLM_INITIAL_PEERS=/ip4/<public-ip>/tcp/7001/p2p/<peer-id>
```

Preserve `bootstrap.id`. Run exactly one persistent bootstrap instance. Use Python 3.12; Hivemind/Pydantic is not reliable on Python 3.14.

TCP reachability proves only bootstrap transport. A worker RPC may still require another reachable port, relay support, or an overlay network.

## Public Model Returns 401 or “Invalid Model Identifier”

Observed root cause:

```text
OAuth token has expired: "exp" claim timestamp check failed
401 Unauthorized
... is not a valid model identifier
```

TinyLlama is public. Before Sprint 12, the backend passed a stored expired OAuth token to public requests, and Transformers mislabeled the resulting 401 as a missing repository.

Current behavior:

- public model loading explicitly uses `token=False`
- stored OAuth state is not read for public node/generator startup
- failed startup cleans partial runtime resources

Restart the backend after updating. If a public model still returns 401, confirm the running process is using the Sprint 12 code and inspect whether another wrapper overrides `token=False`.

## Hugging Face Reconnect Required

Structured error:

```text
huggingface_reconnect_required
```

The OAuth token used for an authenticated operation expired or was rejected. Reconnect Hugging Face in the Network UI and repeat the download. Existing validated local snapshots continue to load offline without OAuth.

## OAuth Client Not Configured

Symptom:

```text
OAuth client ID is not configured
```

Set the public OAuth application client ID in the gitignored root `.env`:

```env
DISTRIBLLM_HF_OAUTH_CLIENT_ID=<client-id>
```

Restart the backend. The client ID may be copied into a Colab secret, but access tokens must never be committed or placed in notebooks/source.

## OAuth Browser Does Not Open

Hugging Face device OAuth uses `https://hf.co/oauth/device`. Electron permits `hf.co` and `huggingface.co` only for the external authorization action.

WSL/headless Linux may have no browser for `xdg-open`. The app attempts a Windows browser fallback and always shows the URL/code for manual authorization. Chrome does not need to be open beforehand.

## Gated Repository Access Denied

Symptom:

```text
The connected Hugging Face account is not approved for <model>
```

OAuth succeeded, but account access did not. Check the Hugging Face gated-repository status:

- Pending: wait for approval.
- Accepted: ensure the exact repository belongs to the accepted gating group.
- Terms changed: revisit the model page and accept them.

Llama 2 access does not imply Llama 3.2 access. DistribLLM cannot request or accept model terms for the user.

## Gated Local Import Required or Invalid

Gated runtime startup requires a validated local snapshot. Common failures include missing tokenizer files, incomplete shards, wrong architecture/dimensions, moved folders, and a URL pasted where a local path is required.

Use the in-app OAuth download or Browse a complete local folder. Startup revalidates the registry before model construction and uses `local_files_only=True`.

## CUDA Is Unavailable

Symptoms:

```text
CUDA requested but not available - falling back to CPU
torch.cuda.is_available() == False
```

Checks:

```bash
nvidia-smi
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

In Colab, select a GPU runtime before installing/loading. Changing runtime resets `/content`; use a mounted Drive `HF_HOME` to persist model cache. Verify CUDA inside the project `.venv`, not only in the notebook kernel.

## CUDA Out of Memory

Structured error:

```text
cuda_out_of_memory
```

Reduce the served layer range, delete/unload other nodes or generator state, use CPU, or select a smaller model. Confirm the node reports `selective_safetensors`; a `full_model_fallback` checkpoint can still create a large CPU-memory peak before device transfer.

## CPU RAM Exhaustion During Model Loading

Llama 2 7B downloads about 13.5 GB of fp16 weights. With safetensors, workers map the checkpoint and materialize only the assigned decoder blocks. Disk requirements remain unchanged, and the generator still loads its local embedding and output components through Transformers.

If node status reports `full_model_fallback`, use a safetensors checkpoint, choose a smaller model, or provide more CPU RAM. Set `DISTRIBLLM_ALLOW_FULL_MODEL_FALLBACK=false` to reject such checkpoints before expensive construction. Loading diagnostics in Nodes and Monitoring report the selected strategy, parameter bytes, elapsed time, and measured RSS increase.

## No Nodes or Incomplete Route

Symptoms:

```text
No nodes found on the DHT
Incomplete layer coverage
Route is not contiguous
```

Check:

1. All participants use the same `DISTRIBLLM_DHT_PREFIX`.
2. Node and generator model IDs match exactly.
3. Layer ranges form a complete route from zero to model depth.
4. Nodes have fresh metadata, `rpc_uid`, loaded layers, and running RPC.
5. Remote worker addresses are reachable, not merely visible in DHT.

The `/models` and `/generator/status` responses include route reasons, coverage, and trace information. Registry support alone does not mean a model is runnable.

## Provider Present but RPC Health Degraded

Monitoring presents DHT presence, transport verification, and expert RPC health separately. A provider can still have a current advertisement while its metadata expert is timing out or unreachable. Relayed metadata probes default to fifteen seconds because the verified VPS path can spend several seconds establishing the circuit; override `DISTRIBLLM_HEALTH_PROBE_TIMEOUT` only when the measured path justifies it. Under defaults, one failed probe keeps a previously healthy route available, two consecutive failures mark it degraded, four mark it offline, and two successful probes are required for recovery.

Inspect `/generator/status` health providers for the failure reason, last probe/success/failure timestamps, latency, role, and health revision. Tune `DISTRIBLLM_HEALTH_*` only after checking relay and worker logs; shortening intervals increases DHT/RPC control traffic. Network's Stop Generator action unloads the monitor and client DHT, while Chat's stop action only cancels the active inference request.

## RPC Safety Rejection or Timeout

Worker errors beginning with `rpc_safety:` are pre-execution policy decisions, not model-output failures. `shape`, `batch`, `sequence`, `hidden_size`, `dtype`, `tensor_bytes`, `non_finite`, `mask`, `position_ids`, and `metadata` identify invalid input. `overloaded` means active or Hivemind queue capacity is full; `queue_timeout` means a bounded waiter was not admitted. `execution_timeout` is a cooperative deadline checked between layers.

Monitoring exposes only aggregate counts and policy limits. It never includes prompts, tensor values, receipt documents, identities, or local paths. Increase a limit only when the model context and available worker memory justify it. A timeout cannot interrupt a tensor kernel already executing inside one decoder block, so repeated timeouts also require model/device performance investigation.

## RPC Node Fails After Retries

Possible causes:

- worker RPC port is blocked by firewall/NAT
- stale DHT metadata or RPC UID
- remote node stopped after announcing
- tensor shape/dtype or architecture mismatch
- Colab joined discovery outbound but cannot accept inbound RPC

Inspect node logs, route trace, `backend/client/sequential.py`, `backend/node/handler.py`, and worker-visible multiaddresses.

Current clients attach a request ID, hop number, attempt budget, elapsed time, input byte count, and failure class to expert-call logs. A `stream reset` is classified as an ambiguous transport outcome and is not retried automatically because the worker may already have executed it. Known pre-execution dial failures may retry only within `DISTRIBLLM_RPC_MAX_ATTEMPTS` and the configured bounded backoff.

Sprint 21 adds a second, complete-route boundary around those expert calls. A definitely pre-execution failure can quarantine the failed provider and restart the forward from the original activation tensor on a healthy complete alternate. It never splices an alternate into the middle of a partially executed route. Failed-attempt receipt material is discarded, the replacement gets a fresh request ID, and only the complete accepted attempt is submitted for useful-work settlement. Ambiguous transport failures, remote execution failures, cancellation, and invalid requests do not cross this boundary.

`DISTRIBLLM_ROUTE_MAX_ATTEMPTS` caps complete forward attempts, `DISTRIBLLM_ROUTE_MAX_ALTERNATES` bounds retained plans, `DISTRIBLLM_ROUTE_BACKOFF_SECONDS` controls deterministic linear backoff, and `DISTRIBLLM_ROUTE_QUARANTINE_SECONDS` avoids immediately choosing a provider that just failed. `DISTRIBLLM_ROUTE_ALLOW_DEGRADED=false` excludes degraded providers entirely; the default allows one only when no healthy complete route exists. Monitoring shows the active route, ordered complete alternates, transport classification, provider route roles, and the latest failover reason.

When no alternate succeeds, the terminal error names the failed layer span. An offline or ineligible span in readiness is reported as an exact unavailable range. Active tensor kernels still cannot be forcibly interrupted mid-layer, and distributed key/value cache migration is not implemented; failover restarts the stateless full-sequence forward attempt.

VPS relay debug lines that report exactly `131072` bytes in one direction do not by themselves prove a 128 KiB relay quota. The Hivemind 1.1.12 bundled daemon reports a four-gigabyte default relay data allowance; 128 KiB can be the stream flow-control window visible when an endpoint resets. Correlate the same time window across generator, worker, and VPS logs. The worker must show either `Expert forward complete` with shape, bytes, and duration or an exception before the transport failure can be classified.

Legacy inference compresses floating activations to float sixteen on the wire and restores their original dtype. Receipt protocol version one remains uncompressed because its signed BLAKE3 commitment covers exact tensor bytes; applying lossy compression before worker verification would invalidate the commitment.

If the peer advertises only `/ip4/172.x.x.x/...`, another Windows device cannot normally reach that WSL-private address. Choose one path:

- Direct LAN: set a fixed `DISTRIBLLM_P2P_PORT`, announce the Windows LAN address, enable WSL mirrored networking or a Windows `portproxy`, and allow that TCP port through the Windows/Hyper-V firewall.
- Direct Internet: additionally forward the router's public port, unless NAT mapping succeeds.
- Production default: use `DISTRIBLLM_NETWORK_MODE=auto` with a reachable trusted relay and no manual announce address.

For direct LAN diagnosis, run `Test-NetConnection <windows-lan-ip> -Port <p2p-port>` from the other Windows device. If it fails, fix the Windows/WSL path before testing inference.

If startup reports that relay mode did not obtain a circuit address, verify the updated bootstrap is running with relay enabled, its address is reachable, the trusted relay peer ID matches, and the timeout is long enough. From the backend environment, run:

```bash
HIVEMIND_LOGLEVEL=DEBUG \
GOLOG_LOG_LEVEL=autorelay=debug,relay=debug \
uv run --python 3.12 python -m relay_probe --timeout 90 --json
```

The probe starts only a Hivemind DHT peer with relay settings, forces private reachability so AutoRelay engages, selects configured trusted relays as static candidates, and refreshes daemon addresses while waiting for a `/p2p-circuit/` address. It does not start FastAPI, load model layers, or advertise an expert. Its JSON reports the effective Python and Hivemind versions. A `/p2p-circuit/` address must appear before relay transport is considered verified.

Verified public baseline on 2026-08-12: Python `3.12.3` with Hivemind `1.1.12` obtained a complete circuit address through the project VPS in `1.633` seconds. A separate same-host Hivemind peer then used only a full OPT-125M worker's circuit address and completed the production `expert.info` readiness RPC. These results prove reservation and expert metadata transport, but a two-device tensor-forward and generation test is still required.

Activating `.venv` is optional when the command uses `uv run`; `uv` selects the project environment. Activation matters only for plain `python` commands. On both the VPS and participant, prefer `uv sync --frozen --python 3.12` followed by `uv run --python 3.12 ...` so the current lockfile supplies Hivemind 1.1.12 and its matching `p2pd` binary.

If TCP bootstrap succeeds but the probe still times out, compare the repository commit and runtime versions, then inspect the VPS debug launch line for `-relay=1` and `-forceReachabilityPublic=1`. Inspect the participant line for `-autoRelay=1`, `-trustedRelays=...`, `-relayDiscovery=0`, `-dhtClient=1`, and `-forceReachabilityPrivate=1`. The bundled daemon's discovery path normally waits up to three minutes for four candidates; DistribLLM disables that path when an explicit trusted relay is configured so one project VPS can be used immediately. Increasing the timeout alone does not correct an old checkout, missing relay service, or mismatched runtime.

If the probe JSON does not contain `python_version`, `hivemind_version`, `force_reachability`, and `relay_discovery: false`, the participant is still running the older probe implementation and must update to the same commit before retesting.

## Poor or Repetitive Output

Base completion models such as OPT are transport/parity targets, not reliable chat models. First compare direct Hugging Face and distributed behavior:

- `/generator/parity/next-token`
- `/generator/parity/generate`
- `/generator/trace`
- `/generator/traces/analysis`

If deterministic next-token parity passes, classify weak prose as model/sampling behavior before assuming route corruption. Prefer TinyLlama chat or Llama 2 chat for instruction-ready validation.

## Trace Files

Trace JSON defaults to `backend/traces/`; override with `DISTRIBLLM_TRACE_DIR`. Traces are gitignored. Review token IDs, top candidates, decoded output, tensor shapes, replacement-character flags, and route trace. Never add raw tokens, local paths, or secrets to trace artifacts.

## Fast Checklist

1. Backend `/status` responds.
2. Correct public bootstrap peer appears in `/models.default_peers`.
3. Bootstrap TCP port is externally reachable.
4. Public models are loading anonymously; gated models have validated local imports.
5. Requested device exists and has enough VRAM.
6. Host has enough CPU RAM for the reported selective slice, or for full-model construction when fallback is active.
7. `/nodes` shows fresh compatible nodes and RPC UIDs.
8. `/generator/status` reports complete contiguous coverage.
9. Worker transport reports direct or relay, and every selected expert passes its RPC probe.
10. Use parity/trace tools before diagnosing output quality.
11. For a reset, match the generator request ID to worker completion/error timing and the VPS circuit interval before changing relay limits.
