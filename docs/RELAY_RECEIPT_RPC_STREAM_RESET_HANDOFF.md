# Relay Receipt RPC Stream Reset Handoff

**Date:** 2026-08-22; resolution evidence added 2026-08-25

**Owner:** Sprint 22, with runtime-state follow-up in Sprint 25 and UI follow-up in Sprint 27

**Status:** Historical reset analysis retained; incentives-off session-v1 adjacent split now passes bidirectionally after exact-peer handle retention. Shadow receipt acceptance remains open.

## 1. Executive Summary

Resolution update: source commit `81a768a60f42c0a951e4c7ef78b7085d8a16a1bd` retains the exact-peer `RemoteExpert` handles that prepared and opened a session instead of repeating expert discovery and `rpc_info` resolution before every prefill and decode. Four physical diagnostic bundles now prove five complete sessions through the relayed adjacent route in both generator directions. Both providers ended with five opens, five closes, zero active sessions, five prefills, 586 decodes, and zero RPC failures, rejections, or timeouts. Three ambiguous responses were recovered through the provider-retained, fingerprint-bound exact-operation result.

Device 1 generated 200 tokens with its local `0-6` hop averaging 28.54 milliseconds and remote `6-12` averaging 758.03 milliseconds. Device 2 generated 200 tokens with remote `0-6` averaging 760.42 milliseconds and local `6-12` averaging 33.58 milliseconds. This inversion confirms that the generator role really moved between machines and both relay directions executed. The historical stateless and receipt failures below remain useful chronology; they are no longer the latest session-path result.

The current two-device failure is no longer a discovery, DHT lease, clock, or worker-identity failure.

The repaired build reached all of these gates:

1. Device 2 started one full `facebook/opt-125m` worker for layers `0-12`.
2. An independent Device 1 observer repeatedly retrieved Device 2's member lease, provider metadata, normal expert, and receipt expert.
3. The observed peer stayed `QmRevwu67tzcBjWW21u11Q7oPuhbtEodmuDD87aoW9Yp6z` and the worker reported zero network recoveries.
4. Device 1 started its generator, validated a complete route, and passed the startup tensor canary.
5. The first real chat forward failed with `stream reset`, classified as `ambiguous_transport`.

The important source-level finding is that gate 4 and gate 5 use different Hivemind experts in shadow mode:

- startup validation has no useful-work session, so it calls the normal expert `distribllm.0.12`;
- real chat starts a useful-work session, so it calls the receipt expert `distribllm.999999.0.12`;
- the receipt expert carries an additional fixed-size metadata tensor and returns both the activation and signed receipt metadata.

Therefore, a green startup canary did not prove the RPC path used by real shadow-mode chat. Receipt RPC was initially the strongest difference between the passing canary and the failing prompt, so the next controlled comparison disabled incentives without changing the relay topology.

That comparison has now been captured: incentives-off legacy generation also failed with an ambiguous stream reset after the normal-expert startup canary passed. Receipt RPC is therefore not the unique cause. The current code's request and mode diagnostics identify the failed call as legacy request `cbdcb4fa-0647-4c26-ba64-a5c2f9cca00b` through `distribllm.0.12`.

The later adjacent `0-6` plus `6-12` pass reached ready and streamed visible output before a subsequent forward failed ambiguously at the `0-6` provider. This proves earlier token forwards traversed the complete split route, but the split topology still fails the sustained-execution gate.

No speculative receipt-only change should be merged. The next isolation target is the common sustained tensor-over-relay path, using controlled payload sizes and direct-versus-relayed comparison.

## 2. Physical Topology And Tested Artifact

The operator reports that the accepted session-v1 candidate embeds source commit `81a768a60f42c0a951e4c7ef78b7085d8a16a1bd`. Its audited portable executable is 87,848,603 bytes with SHA-256 `67eccff5aae01905c57f2df1832ace2a623a13258fcacda3d0ece3c31c4f3c91`; the package audit found 38 ASAR entries, 65 manifest-bound backend runtime files, no forbidden entries, and embedded backend manifest SHA-256 `6d33ac0fa756ff71f0714e0bcb2ec2aea94722527392e1044cfa9b34c6683c7b`. The four runtime diagnostic bundles do not contain this commit or executable hash, so a separate schema-four launcher acceptance report is still required for independent package-to-run binding.

An earlier untested acceptance candidate embedded source commit `232acb1bdcba5c7347a8f58aba056881115a8308` with a clean source flag. Its portable EXE was 87,663,434 bytes with SHA-256 `0b6121de080fb5f16df53d9f47df95d39a5ff07cd99892eef1f0e2af23de89ee`; its package audit reported 36 ASAR entries and zero forbidden entries. It was superseded by the accepted session-v1 candidate above.

```text
Device 1: generator and Electron UI
  -> project DHT bootstrap and circuit relay on VPS port 7001
  -> Device 2 worker through its relay circuit

Device 2: full OPT-125M worker, layers 0-12
  -> publishes provider metadata plus mode-dependent expert UIDs
  -> normal UID:  distribllm.0.12
  -> receipt UID in shadow mode: distribllm.999999.0.12
  -> incentives-off Test A publishes only the normal UID

Both devices:
  -> local FastAPI backend on 127.0.0.1:8000
  -> test-only SSH tunnel to settlement on 127.0.0.1:7101 in shadow mode
```

The persistent-identity shadow test used source commit `9fec4a8903162d34432bc2b7357b665fda8715c1` and the corresponding portable executable with SHA-256 `816893d26e6d12e6aae261a5cc15c574268e612cd1aadf4942a69b27d7a2dbba`.

The later incentives-off Test A used source commit `7c69382050f71cbd2ba41945714257d24af7c43c` and its matching portable executable. The executable was 87,654,628 bytes with SHA-256 `b7a8aeaf10587a0037849ffe67841720470ec03f0c7fec8200e42acf2c4b517b`; its package audit found 36 ASAR entries and no forbidden entries.

### 2.1 Chronological Testing Procedure Actually Performed

This is an audit trail of the physical investigation, including invalid and incomplete steps. It is not a claim that every command below must be repeated for future releases.

#### Stage 1: verify VPS settlement and relay infrastructure

In the VPS SSH shell, settlement was checked with:

```bash
sudo systemctl is-active distribllm-settlement.service
sudo ss -ltnp 'sport = :7101'
curl -fsS http://127.0.0.1:7101/v1/policy
```

The service was active, Uvicorn listened only on `127.0.0.1:7101`, and the policy returned `mode: shadow` with protocol version 1. The relay was checked separately with the project relay probe and later returned `ok: true`. Settlement availability and relay reservation are different gates.

#### Stage 2: open the test-only settlement tunnel for shadow runs

Device 1 and Device 2 each opened this command from their own WSL shell:

```bash
ssh -N \
  -i "$HOME/.ssh/distribllm_settlement" \
  -o IdentitiesOnly=yes \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:7101:127.0.0.1:7101 \
  mohammed@178.156.212.0
```

The silent terminal meant the foreground tunnel was running. A second WSL terminal verified it with:

```bash
curl -fsS http://127.0.0.1:7101/v1/policy
```

The incentives-off Test A did not require settlement or this tunnel. Closing the tunnel with Ctrl+C stops only that tunnel, not the worker or generator.

#### Stage 3: align source, dependencies, and packaged artifact

Device 1 used `/home/albad/FYP/fyp-projects`; Device 2 used `/home/odair/moe/distributed-llm`. Both checkouts were aligned with the test branch and revision before starting the packaged participant backends:

```bash
git fetch origin
git switch fix/remote-dht-lease-recovery
git pull --ff-only
git rev-parse HEAD
cd backend
uv sync --python 3.12
```

An earlier Device 1 fetch reported a malformed local `refs/codex/turn-diffs/...` object, although switching to the requested detached commit succeeded. That local Git maintenance issue was not treated as transport evidence. The final controlled legacy run used commit `7c69382050f71cbd2ba41945714257d24af7c43c` and the matching executable recorded above. Earlier runs on `9fec4a8` remain useful for the persistent-identity and shadow-path observations but are not the same artifact.

#### Stage 4: synchronize participant clocks

The first generator attempt exposed more than three seconds of clock skew. In an Administrator PowerShell terminal, Windows Time was started and synchronized:

```powershell
Start-Service W32Time
w32tm /resync /force
w32tm /query /status
```

The first non-administrator attempt returned access denied, and one intermediate query reported that the service had not started. The user later confirmed synchronization, and the clock-skew generator failure no longer appeared.

#### Stage 5: configure the mode and restart managed participant backends

The common relay and DHT variables were kept in each repository-root `.env`. Shadow runs used `DISTRIBLLM_INCENTIVES_MODE=shadow`. Controlled Test A changed both devices to:

```dotenv
DISTRIBLLM_INCENTIVES_MODE=off
```

Both backends were then restarted through the packaged Electron Settings workflow, because that is the proven participant lifecycle on these devices. Each backend was checked with:

```bash
curl -fsS http://127.0.0.1:8000/status | python3 -m json.tool
curl -fsS http://127.0.0.1:8000/incentives/accounting | python3 -m json.tool
```

For Test A, both had to report mode `off`; settlement was intentionally disabled.

#### Stage 6: start one full Device 2 worker

In Device 2 Electron, the operator selected Network, Serve Layers, `facebook/opt-125m`, the full `0-12` range, CUDA, and Start Node. The local worker was verified from Device 2 WSL with:

```bash
curl -fsS http://127.0.0.1:8000/nodes/local | python3 -m json.tool
curl -fsS http://127.0.0.1:8000/nodes/local \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["nodes"][0]["peer_id"])'
```

The latest Test A worker peer was `QmQXdUAw4SNPoA6gPMTAY6xPaCS7Em1EutF8FQpE9RWGMh`. In off mode it correctly published only `distribllm.0.12`.

#### Stage 7: observe Device 2 independently from Device 1

The corrected shadow-mode observer used the VPS bootstrap peer as `--initial-peer`, the current worker as `--expected-peer`, and required the receipt capability:

```bash
cd /home/albad/FYP/fyp-projects/backend
set -o pipefail
uv run --python 3.12 python -m lease_observer \
  --initial-peer "/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y" \
  --expected-peer "QmRevwu67tzcBjWW21u11Q7oPuhbtEodmuDD87aoW9Yp6z" \
  --require-receipt \
  --interval 10 \
  --duration 1000 \
  | tee "$HOME/distribllm-lease-soak-9fec4a8.jsonl"
```

It produced repeated `ok: true` samples, but the full 1,000-second soak was not completed in the recorded sequence.

The first Test A observer command was invalid: its bootstrap address ended in the worker peer ID, and it required a receipt expert while incentives were off. After the prompt failure, the corrected off-mode command omitted `--require-receipt` and used the current Test A peer:

```bash
cd /home/albad/FYP/fyp-projects/backend
set -o pipefail
uv run --python 3.12 python -m lease_observer \
  --initial-peer "/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y" \
  --expected-peer "QmQXdUAw4SNPoA6gPMTAY6xPaCS7Em1EutF8FQpE9RWGMh" \
  --interval 10 \
  --duration 1000 \
  | tee "$HOME/distribllm-lease-soak-legacy.jsonl"
```

The post-failure sample returned `ok: true`. There is no valid pre-failure observer sample for Test A.

#### Stage 8: start Device 1's generator and send one prompt

Device 1 kept no local worker. In Electron, the operator selected Network, Run Inference, `facebook/opt-125m`, and Start Generator. Both the shadow run and Test A reached Generator Ready and Route Ready after the startup tensor canary passed.

The controlled prompt was `Say hello in one short sentence.` Shadow mode failed through the receipt expert. Test A failed through the legacy expert under request `cbdcb4fa-0647-4c26-ba64-a5c2f9cca00b`. After each ambiguous reset, no further prompt was needed for that controlled case because the result was already terminal and unsafe to replay automatically.

#### Stage 9: exercise Trace as a separate diagnostic

Trace was clicked during the shadow investigation. It uses the legacy expert because it does not start a useful-work session, and the renderer aborted `POST /generator/trace` after its generic 8,000-millisecond deadline. This was recorded as a UI and diagnostic problem, not as a second receipt-path result.

#### Stage 10: preserve evidence before shutdown

The following capture pattern was used on Device 1:

```bash
mkdir -p "$HOME/distribllm-evidence/uncertain-forward"
curl -fsS http://127.0.0.1:8000/generator/status | python3 -m json.tool > "$HOME/distribllm-evidence/uncertain-forward/device1-generator.json"
curl -fsS http://127.0.0.1:8000/runtime/snapshot | python3 -m json.tool > "$HOME/distribllm-evidence/uncertain-forward/device1-runtime.json"
curl -fsS http://127.0.0.1:8000/nodes | python3 -m json.tool > "$HOME/distribllm-evidence/uncertain-forward/device1-nodes.json"
curl -fsS http://127.0.0.1:8000/incentives/accounting | python3 -m json.tool > "$HOME/distribllm-evidence/uncertain-forward/device1-incentives.json"
```

The corresponding Device 2 capture was:

```bash
mkdir -p "$HOME/distribllm-evidence/uncertain-forward"
curl -fsS http://127.0.0.1:8000/nodes/local | python3 -m json.tool > "$HOME/distribllm-evidence/uncertain-forward/device2-local-nodes.json"
curl -fsS http://127.0.0.1:8000/runtime/snapshot | python3 -m json.tool > "$HOME/distribllm-evidence/uncertain-forward/device2-runtime.json"
curl -fsS http://127.0.0.1:8000/incentives/accounting | python3 -m json.tool > "$HOME/distribllm-evidence/uncertain-forward/device2-incentives.json"
```

The Test A Device 2 snapshot and VPS relay journal for the exact failure window were still missing at the last recorded checkpoint.

### 2.2 Testing Outcome Ledger

| Gate | Result | Evidence or limitation |
|---|---|---|
| Windows clock synchronization | Passed | The earlier three-second skew error disappeared after synchronization |
| VPS settlement service | Passed for shadow setup | Active and loopback-only; not involved in incentives-off Test A |
| Participant settlement tunnels | Passed for shadow setup | Both loopback policy checks returned the VPS shadow policy |
| VPS circuit reservation | Passed | Project relay probe returned `ok: true` |
| Matching source and packaged artifact | Passed for Test A | Commit `7c69382`; executable SHA-256 `b7a8aea...b517b` |
| Device 2 full worker and normal RPC | Passed | Full `0-12` worker, fresh publication, normal expert resolvable |
| Independent shadow observer | Passed partially | Repeated `ok: true`; complete 1,000-second soak was not recorded |
| Independent Test A observer | Invalid before prompt; passed after failure | Initial command was malformed; corrected post-failure sample was healthy |
| Generator discovery and route validation | Passed | Generator and route reached ready |
| One-position tensor canary | Passed | Legacy canary completed in approximately 2.26 seconds in Test A |
| Real shadow prompt | Failed | Receipt expert ended in ambiguous `stream reset` |
| Real incentives-off prompt | Failed | Legacy expert ended in ambiguous `stream reset` |
| Session-v1 split, Device 1 generating | Passed | Two completed streams; final 200-token generation used exact `0-6 -> 6-12` peers with 199 bounded decodes |
| Session-v1 split, Device 2 generating | Passed | Three completed streams; final 200-token generation used the same peers in the reverse relay direction |
| Exact-operation retained-result recovery | Passed | Three physical retained results served across both providers with no duplicate positions or provider RPC failure |
| Trace diagnostic | Failed at renderer boundary | Generic 8-second HTTP timeout; not equivalent to shadow chat |
| Complete user-visible inference | Passed for incentives-off session v1 | Five real relayed generation streams completed across both generator directions |
| Correlated Device 2 and VPS failure logs | Incomplete | Still required for the Test A request window |
| Direct-versus-relayed comparison | Not run | This is the next transport isolation gate |
| Controlled physical payload sweep | Harness implemented; not physically run | Needed to locate the size, duration, or lifecycle threshold |

## 3. Confirmed Evidence

### 3.1 Independent discovery stayed healthy

The corrected observer used the real Device 2 peer ID, not a placeholder, and repeatedly reported `ok: true`. It confirmed:

- `members.v2` ownership by the expected peer;
- current provider metadata with a safe expiration horizon;
- normal and receipt expert UIDs resolving to the same peer;
- fresh remote-store-required publication;
- live RPC server, runtime, and Hivemind publisher.

This proves control-plane visibility. It does not send an activation through either expert.

### 3.2 The legacy startup tensor canary passed

Generator startup calls `RemoteSequential.validate_tensor_route`. That function calls `forward` without calling `start_session`. `RemoteSequential._receipt_route` returns `None` when there is no session, so the canary calls the normal expert.

This proves a small deterministic activation can cross the legacy expert path through the relay and return successfully.

### 3.3 Real chat selected the receipt expert and reset

`DistributedGenerator.generate_stream` starts a session before its first forward. In shadow mode, valid receipt capability metadata makes `_receipt_route` select the receipt expert. The first real prompt then failed with:

```text
Route attempt failed with uncertain execution at layers 0-12;
automatic failover was suppressed.
```

The underlying recorded failure was `ambiguous_transport` with `stream reset`.

Automatic failover suppression is correct. Once a request may have reached the worker, replaying it can duplicate model work and accounting side effects.

### 3.4 Trace did not reproduce the receipt path

`DistributedGenerator.trace_generation` currently calls `sequential.forward` without starting a useful-work session. It therefore uses the normal expert, not the receipt expert. The frontend also applies its generic 8,000-millisecond request deadline to `POST /generator/trace`.

The observed Trace timeout means only that the synchronous diagnostic did not finish before the renderer aborted the HTTP request. It is not proof of another receipt reset and is not equivalent to the chat failure.

### 3.5 The adjacent split also fails during sustained execution

The later whole-system pass configured adjacent `0-6` and `6-12` workers. The generator and route reached ready, and visible model text streamed before a later forward failed with uncertain execution at layers `0-6`. Therefore at least one earlier token traversed both split hops successfully, while the terminal attempt reset at the first split provider after dispatch. The split topology is no longer untested, but it has not passed acceptance.

This result strengthens the common sustained-transport boundary and weakens a pure full-provider-only explanation. It still does not choose between payload size, repeated stream or connection reuse, circuit-relay behavior, and a cross-machine Hivemind response-lifecycle defect. Run the existing controlled payload sweep in relay mode and then direct mode; do not retry the ambiguous chat attempt.

## 4. RPC Path Matrix

| Operation | Starts useful-work session | Selected expert in shadow mode | Current physical result | What it proves |
|---|---:|---|---|---|
| Independent lease observer | No | Neither | Repeated `ok: true` | DHT records and both UID ownership remain visible |
| Metadata health probe | No | Normal UID metadata RPC | Passed before readiness | Normal expert metadata is reachable |
| Startup tensor canary | No | `distribllm.0.12` | Passed | Small legacy tensor request/response works |
| Real streamed chat | Yes | `distribllm.999999.0.12` | `stream reset` | Receipt-capable forward/response did not complete at the generator |
| Real streamed chat with incentives off | No | `distribllm.0.12` | `stream reset` | Sustained legacy forward also fails, so receipt RPC is not the unique cause |
| Trace button | No | `distribllm.0.12` | Frontend timed out after 8 seconds | Current Trace is a slow legacy diagnostic, not a receipt-path reproduction |

## 5. Source Boundary

The relevant code is concentrated in these files:

- [`backend/client/generation.py`](../backend/client/generation.py): `generate_stream` starts and ends the useful-work session; `trace_generation` does not.
- [`backend/client/sequential.py`](../backend/client/sequential.py): `_receipt_route` selects the receipt capability only for an active session; `_call_node` dispatches either legacy or receipt RPC; `_rpc_forward_with_receipt` signs, sends, verifies, and queues a receipt submission.
- [`backend/node/rpc_server.py`](../backend/node/rpc_server.py): registers the normal and receipt experts. The receipt expert has a second metadata input/output and uses exact, uncompressed activation descriptors so tensor commitments remain verifiable.
- [`backend/client/rpc_policy.py`](../backend/client/rpc_policy.py): classifies a stream reset after dispatch as ambiguous and prevents unsafe automatic replay.
- [`frontend/src/renderer/src/api/client.ts`](../frontend/src/renderer/src/api/client.ts): applies the generic short HTTP deadline to Trace.

The local real-Hivemind receipt integration test passes, including a representative activation larger than 128 KiB. That proves the receipt protocol works locally. It does not prove that the same two-output, exact-tensor RPC works over the deployed circuit relay.

## 6. Diagnostics Added In This Handoff

Before this change, `_call_node` generated one outer route-attempt request ID, while `create_inference_request` generated a different signed receipt request ID. The generator failure and worker receipt-execution log could not be joined deterministically.

The client now:

- passes the outer route request ID into the signed inference request;
- logs `rpc_mode=legacy`, `rpc_mode=receipt`, or `rpc_mode=legacy_fallback`;
- logs the effective expert UID rather than labeling a receipt call with the normal UID;
- records receipt stages as `build_request`, `expert_lookup`, `remote_forward`, `decode_response`, and `verify_receipt`;
- includes the RPC mode and effective UID in the terminal `RouteAttemptError` message.

The worker already logs `Receipt expert forward complete | request=<id>`. After rebuilding, the same request ID can answer the key question:

```text
Generator fails at stage=remote_forward with request=X
Worker contains "Receipt expert forward complete | request=X"
```

If both lines exist, execution and receipt creation completed on Device 2 and the response was lost afterward. If the worker never logs request `X`, the failure occurred before receipt handler execution.

The latest source slice also persists prompt-free generation failures into the bounded runtime event store. Each terminal error now carries the last route request ID, failure class, peer, layer range, attempt count, route revision, and coverage revision through the WebSocket. Settings combines these backend events with a bounded, secret-redacting renderer history and exports one JSON diagnostic bundle. This instrumentation does not repair the relay reset; it makes the next physical occurrence independently reviewable after leaving the Inference page.

## 7. What Has Been Ruled Out For This Occurrence

Do not restart investigation from these already-closed explanations unless new evidence contradicts them:

- clock skew: Windows time was synchronized before the corrected run;
- literal observer placeholder: the corrected observer used the real peer ID;
- expired member/provider leases: the independent observer remained healthy;
- expert ownership mismatch: both UIDs resolved to the expected peer;
- peer rotation during this occurrence: Device 2 reported zero recovery and retained its peer;
- generator startup ordering: the generator reached ready;
- small legacy tensor reachability: the startup canary passed, while sustained legacy generation still fails;
- settlement connectivity as the cause of the reset: settlement submission occurs only after the receipt response is verified;
- safe automatic failover: replay is intentionally suppressed for ambiguous execution;
- a proven 128-KiB relay quota: the observed value can be a flow-control window and the project has no controlled evidence that it is a hard quota.

## 8. Remaining Unknowns And Ranked Hypotheses

### Highest priority: common sustained tensor-over-relay path

The incentives-off baseline selected the normal expert and still reset. The strongest remaining boundary is therefore the difference between the small one-position startup canary and a real generation forward: activation size, execution duration, response size, repeated-stream lifecycle, or relay/p2p connection behavior.

Use a controlled legacy RPC payload sweep so the first failing sequence length and byte count are known without chat sampling, tokenizer, receipt, or settlement variables.

### Response lost after worker execution

Test A's retrieved provider snapshot showed eleven accepted and completed worker RPCs with no worker-side failures, rejections, or timeouts. That aggregate counter is consistent with work completing before the response reset, but it does not identify the exact failed request. The Device 2 request log and VPS journal must be joined to request `cbdcb4fa-0647-4c26-ba64-a5c2f9cca00b` before this becomes a confirmed conclusion.

### Relay-specific versus transport-library-wide behavior

A direct legacy run with the same worker, model, prompt payload, and incentives-off mode is the next topology control. Direct success would isolate the VPS relay/circuit path. Direct failure would shift attention to the two-machine Hivemind/p2p stream or worker response lifecycle shared by both connection modes.

### Receipt-specific behavior is now secondary

Receipt mode still has a larger exact payload and a two-output response, so it may expose additional thresholds. It is no longer the primary explanation because legacy Test A failed first. Receipt-path work should resume only after the common legacy transport boundary is understood.

## 9. Required Controlled Test Matrix

Run these tests from one clean commit and one matching packaged artifact. Keep the same two devices, model, full `0-12` Device 2 worker, relay, prompt, and generation settings.

### Test A: relayed legacy baseline

Set `DISTRIBLLM_INCENTIVES_MODE=off` on both participant backends, restart both backends, start Device 2's full worker, start Device 1's generator, and send one short prompt.

- If this fails with a stream reset, the defect is a general sustained tensor relay problem.
- If this succeeds, the relay can carry a real prompt on the legacy expert and Test B becomes decisive.
- No useful-work receipt or credit is expected in this mode.

**Physical result:** Failed. Device 1 was in incentives mode `off`; Device 2 published only `distribllm.0.12`; relay transport and the startup canary passed; the first real prompt failed through the legacy expert with `ambiguous_transport` and `stream reset`. Request: `cbdcb4fa-0647-4c26-ba64-a5c2f9cca00b`.

### Test B: relayed receipt path

Restore `DISTRIBLLM_INCENTIVES_MODE=shadow` on both backends, restart both, repeat the same topology and prompt, and capture the new request-correlated logs.

- If A passes and B fails, isolate the fix to the receipt RPC/relay interaction.
- Compare the generator's `request`, `rpc_mode`, `rpc_uid`, and `stage` with Device 2's receipt completion log.

**Decision after Test A:** Do not run Test B merely to prove receipt specificity. Test A already selects the common relay-path branch. Preserve the failed-run evidence and proceed to controlled legacy payload and direct-path isolation.

### Test C: direct legacy path

When direct reachability is available, repeat Test A in incentives-off mode without the circuit relay.

- If direct legacy succeeds, the common model and Hivemind RPC path works across machines and the defect is relay-specific.
- If direct legacy also fails, inspect the two-machine Hivemind/p2p stream and worker response lifecycle before changing receipt code.
- Run direct shadow only after this common legacy control or when receipt capability needs its own later acceptance evidence.

### Test D: controlled legacy payload sweep

Use `backend/tensor_payload_probe.py`. It is a dialing-only diagnostic client, so it can run from Device 1 while the packaged Electron backend already owns port `8000`; do not press Settings **Start** and do not start another Uvicorn process. It always calls the normal stateless expert with one attempt per case and no receipt route, even if the repository `.env` is currently in shadow mode.

The default order is `1, 1, 2, 4, 8, 16, 32, 64, 96, 128`. Repeating sequence length one distinguishes a second-call lifecycle failure from a payload-size failure. The final cases cross the region around the observed `131072`-byte relay copy without claiming that value is a hard limit. The reported `serialized_tensor_protobuf_bytes` values cover the three serialized tensor protobufs, not the complete libp2p RPC envelope or framing.

On Device 1, leave Device 2's full `0-12` worker running through the EXE, open a separate WSL shell, and run:

```bash
cd /home/albad/FYP/fyp-projects/backend
mkdir -p "$HOME/distribllm-evidence/payload-relay"
set -o pipefail
uv run --python 3.12 python -m tensor_payload_probe \
  --expected-peer "<DEVICE_2_WORKER_PEER_ID>" \
  --expected-transport relay \
  --output "$HOME/distribllm-evidence/payload-relay/result.json" \
  2>&1 | tee "$HOME/distribllm-evidence/payload-relay/client.log"
```

The command reads the VPS bootstrap address from `DISTRIBLLM_INITIAL_PEERS` in the repository-root `.env`. If an explicit address is required, add one `--initial-peer "<VPS_BOOTSTRAP_MULTIADDR>"`; its final peer ID must be the VPS bootstrap peer, never the Device 2 worker peer. Replace the expected peer placeholder with the full Device 2 worker peer ID shown by `/nodes/local`.

For a version-two worker, the probe derives the expected normal UID from the selected peer as `distribllm-<DEVICE_2_WORKER_PEER_ID>.0.0.12.0` and requires matching `rpc_peer_id` ownership. Use `--expected-rpc-uid` only when intentionally testing a historical version-one worker such as the earlier `distribllm.0.12` evidence. The probe also requires layers `0-12`, the requested connection mode, and `transport_verified: true`. It checkpoints the private JSON file with mode `0600` before every remote forward, so a hung or externally interrupted call still leaves the last request ID, sequence length, stage, and byte measurements. It stops immediately after the first failure and never retries an ambiguous reset.

Preserve the result and client log, then collect Device 2 worker state and the VPS journal for the result's `captured_at`, per-case request times, and first failed case. Do not run the direct comparison until the relayed result has been saved.

For the direct control, configure Device 2 for the already documented direct-reachability topology, restart its backend through the EXE, restart the same full worker, and confirm that its metadata reports `connection_mode: direct` and `transport_verified: true`. Run the identical command into a new `payload-direct` directory with only these argument changes:

```bash
  --expected-transport direct \
  --output "$HOME/distribllm-evidence/payload-direct/result.json"
```

Use the exact same sequence-length list for relay and direct. Relay failure plus direct success isolates the circuit-relay path. Failure at the same case in both modes points to the shared cross-machine RPC or response lifecycle. If both one-position cases pass and a later case fails, the first failed byte count brackets a payload or duration threshold. If the second one-position case fails, investigate repeated-stream or connection reuse before payload size.

## 10. Evidence Required From The Next Physical Failure

Preserve all evidence before stopping either backend:

### Device 1

- `/generator/status`;
- `/runtime/snapshot`;
- the generator log lines for the failed request ID;
- the observer samples covering at least one lease before and after the failure;
- incentives mode and source commit identity;
- whether the failure stage is `expert_lookup`, `remote_forward`, `decode_response`, or `verify_receipt`.

### Device 2

- `/nodes/local` and `/runtime/snapshot`;
- the worker log search for the exact Device 1 request ID;
- RPC safety and accounting counters before and after one prompt;
- publication and recovery state;
- incentives mode and source commit identity.

### VPS

- bootstrap/relay service journal for the exact wall-clock window;
- p2p daemon restart identity and service uptime;
- relay resource-limit, reservation, connection, and stream-reset messages;
- host memory, file-descriptor, and connection-pressure evidence.

Do not compare logs until both Windows/WSL clocks and the VPS clock are synchronized.

## 11. Fix Decision Tree For The Next Agent

### If legacy and receipt modes both fail over relay

Investigate the common Hivemind/p2p data path. Reproduce with controlled payload sizes, capture daemon and relay logs, inspect connection-handler/resource limits, and compare direct versus relayed transport. Keep ambiguous retries disabled.

### If legacy succeeds and receipt fails only over relay

Inspect the receipt expert's exact serialization and two-output response. Candidate engineering changes must be tested against signed tensor commitments. Do not simply enable float-16 compression: changing the activation in transit breaks the current commitment unless the protocol is redesigned around a canonical transported representation.

A robust redesign may separate model output transport from durable receipt retrieval, keyed by an idempotent request ID. That requires a worker-side request ledger and a safe cached completion lookup before ambiguous retries can be enabled.

### If receipt fails in direct mode too

Build a two-machine receipt-only integration probe outside the Electron flow. Compare the installed Hivemind and p2p daemon versions, descriptors, output tuple, metadata size, and response verification. The local same-host integration is insufficient for this branch.

### If transport succeeds but verification fails

Fix the reported `decode_response` or `verify_receipt` stage directly. Preserve signature, route, tensor-commitment, peer-ownership, replay, and settlement invariants.

## 12. Changes That Must Not Be Used As Shortcuts

- Do not retry an ambiguous reset automatically.
- Do not silently fall back to legacy after a possibly executed receipt request.
- Do not award credit from worker accounting alone.
- Do not mark the generator ready solely because the legacy canary passes while shadow chat requires another RPC capability.
- Do not treat longer frontend timeouts as a transport repair.
- Do not weaken signed request, response commitment, route, or peer-presence validation.

## 13. Product And Diagnostic Follow-Up

After transport isolation, Sprint 25 should make generator readiness capability-aware. When shadow or credit mode is enabled, readiness should distinguish a legacy tensor canary from a receipt-capability canary without awarding synthetic work.

Sprint 27 should:

- remove stale `Waiting for generator route` output after readiness changes;
- label the active RPC/accounting mode;
- show the request ID and failure stage for a failed forward;
- convert Trace to an asynchronous diagnostic job with progress and a suitable deadline;
- either make Trace explicitly legacy-only or add a separately named receipt-path diagnostic.

## 14. Completion Criteria

The common sustained session-over-relay defect is complete for the tested incentives-off adjacent route. The following list is intentionally broader because it also covers the still-open incentives-shadow receipt and end-to-end settlement gate.

The relay stream defect is not complete until all of these are recorded from one artifact:

- independent leases remain healthy for the required soak duration;
- at least one real relayed chat prompt completes;
- the selected RPC mode and expert UID are visible in evidence;
- shadow mode produces a verified accepted receipt for the successful selected worker;
- no uncertain attempt is automatically replayed or credited;
- generator, route, WebSocket, conversation, and diagnostic UI states agree;
- the same request ID joins generator dispatch, worker completion, and settlement submission.
