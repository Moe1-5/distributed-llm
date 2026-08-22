# Relay Receipt RPC Stream Reset Handoff

**Date:** 2026-08-22

**Owner:** Sprint 22, with runtime-state follow-up in Sprint 25 and UI follow-up in Sprint 27

**Status:** Root-cause boundary isolated; transport fix not yet proven on the physical relay

## 1. Executive Summary

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

Therefore, a green startup canary does not prove the RPC path that real shadow-mode chat uses. The strongest current hypothesis is a failure in the receipt-capable RPC or its interaction with the relay. This is not yet proof: the next controlled run must compare incentives `off` and `shadow` without changing the network topology.

No speculative transport change should be merged until that comparison is captured. The current code adds request and stage correlation so the next reset can be joined to the worker's exact signed-request log.

## 2. Physical Topology And Tested Artifact

```text
Device 1: generator and Electron UI
  -> project DHT bootstrap and circuit relay on VPS port 7001
  -> Device 2 worker through its relay circuit

Device 2: full OPT-125M worker, layers 0-12
  -> publishes provider metadata plus two expert UIDs
  -> normal UID:  distribllm.0.12
  -> receipt UID: distribllm.999999.0.12

Both devices:
  -> local FastAPI backend on 127.0.0.1:8000
  -> test-only SSH tunnel to settlement on 127.0.0.1:7101
```

The persistent-identity test used source commit `9fec4a8903162d34432bc2b7357b665fda8715c1` and the corresponding portable executable with SHA-256 `816893d26e6d12e6aae261a5cc15c574268e612cd1aadf4942a69b27d7a2dbba`.

This handoff adds diagnostics after that artifact. A new physical run must use a new commit and executable built from the same clean source revision on both devices.

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

## 4. RPC Path Matrix

| Operation | Starts useful-work session | Selected expert in shadow mode | Current physical result | What it proves |
|---|---:|---|---|---|
| Independent lease observer | No | Neither | Repeated `ok: true` | DHT records and both UID ownership remain visible |
| Metadata health probe | No | Normal UID metadata RPC | Passed before readiness | Normal expert metadata is reachable |
| Startup tensor canary | No | `distribllm.0.12` | Passed | Small legacy tensor request/response works |
| Real streamed chat | Yes | `distribllm.999999.0.12` | `stream reset` | Receipt-capable forward/response did not complete at the generator |
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

## 7. What Has Been Ruled Out For This Occurrence

Do not restart investigation from these already-closed explanations unless new evidence contradicts them:

- clock skew: Windows time was synchronized before the corrected run;
- literal observer placeholder: the corrected observer used the real peer ID;
- expired member/provider leases: the independent observer remained healthy;
- expert ownership mismatch: both UIDs resolved to the expected peer;
- peer rotation during this occurrence: Device 2 reported zero recovery and retained its peer;
- generator startup ordering: the generator reached ready;
- legacy tensor reachability: the startup canary passed;
- settlement connectivity as the cause of the reset: settlement submission occurs only after the receipt response is verified;
- safe automatic failover: replay is intentionally suppressed for ambiguous execution;
- a proven 128-KiB relay quota: the observed value can be a flow-control window and the project has no controlled evidence that it is a hard quota.

## 8. Remaining Unknowns And Ranked Hypotheses

### Highest priority: receipt RPC versus legacy RPC

The cleanest source difference between the passing canary and failing chat is the receipt expert. It adds exact activation serialization, signed request metadata, a second response tensor, and receipt verification.

This hypothesis is strong but unconfirmed because no otherwise-identical incentives-off relay run has been recorded.

### Receipt response lost after worker execution

A prior Device 2 capture showed completed worker accounting while Device 1 reported an ambiguous reset. The new shared request ID is required to prove whether that accounting belongs to the exact failed request.

If the worker completion line exists but the generator remains at `remote_forward`, inspect Hivemind response serialization, the two-output response, p2p daemon logs, and relay stream closure after the worker returns.

### General sustained tensor relay instability

The startup canary is one position while real prompts contain more positions and repeat forwards for token generation. The relay may tolerate the canary but reset a larger or longer-lived request independently of incentives.

An incentives-off prompt using the same route and prompt distinguishes this from the receipt-specific hypothesis.

### Client-side response decode or verification

If `_rpc_forward_with_receipt` reaches `decode_response` or `verify_receipt`, the relay delivered a response and the fault is local protocol handling rather than transport. The new stage field makes that distinction visible.

## 9. Required Controlled Test Matrix

Run these tests from one clean commit and one matching packaged artifact. Keep the same two devices, model, full `0-12` Device 2 worker, relay, prompt, and generation settings.

### Test A: relayed legacy baseline

Set `DISTRIBLLM_INCENTIVES_MODE=off` on both participant backends, restart both backends, start Device 2's full worker, start Device 1's generator, and send one short prompt.

- If this fails with a stream reset, the defect is a general sustained tensor relay problem.
- If this succeeds, the relay can carry a real prompt on the legacy expert and Test B becomes decisive.
- No useful-work receipt or credit is expected in this mode.

### Test B: relayed receipt path

Restore `DISTRIBLLM_INCENTIVES_MODE=shadow` on both backends, restart both, repeat the same topology and prompt, and capture the new request-correlated logs.

- If A passes and B fails, isolate the fix to the receipt RPC/relay interaction.
- Compare the generator's `request`, `rpc_mode`, `rpc_uid`, and `stage` with Device 2's receipt completion log.

### Test C: direct shadow path

When direct reachability is available, repeat Test B without the circuit relay.

- If direct shadow succeeds, the receipt protocol is valid across machines and the defect is relay-specific.
- If direct shadow also fails, inspect the receipt schema, serialization, and client verification before changing relay infrastructure.

### Test D: controlled receipt payload sweep

Add or use a dedicated probe that invokes the real receipt expert through the physical topology with sequence lengths such as 1, 8, 32, and 64. Record serialized request/response byte counts, completion stage, worker request ID, and relay logs. Do not use chat sampling as the payload harness.

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

The relay stream defect is not complete until all of these are recorded from one artifact:

- independent leases remain healthy for the required soak duration;
- at least one real relayed chat prompt completes;
- the selected RPC mode and expert UID are visible in evidence;
- shadow mode produces a verified accepted receipt for the successful selected worker;
- no uncertain attempt is automatically replayed or credited;
- generator, route, WebSocket, conversation, and diagnostic UI states agree;
- the same request ID joins generator dispatch, worker completion, and settlement submission.
