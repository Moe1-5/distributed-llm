# Sprint 31 - Session-Aware KV-Cache Inference

**Goal:** Replace full-sequence retransmission per token with bounded prefill and decode sessions while preserving parity, cancellation, accounting, and safe recovery.
**Start:** 2026-08-23
**End:** TBD
**Status:** Implemented in source on `feature/distributed-runtime-architecture`; physical direct and relayed two-device acceptance remains open.

---

## Problem Summary

The current generator embeds and forwards the full accumulated sequence through every remote layer for each generated token. Payload size and computation therefore grow throughout generation, increasing relay traffic and exposure to the sustained stream failure that a small startup canary does not reproduce. Petals avoids this pattern with session-scoped remote key/value caches and sends only new positions during decode.

This is a protocol change, not a performance flag. Cache ownership, model semantics, cancellation, failover, memory admission, and useful-work accounting must remain explicit.

## Dependencies and Boundaries

- Require Sprint 22's direct-versus-relay stateless payload sweep as the baseline.
- Depend on Sprint 20 RPC safety, Sprint 21 bounded failover, and Sprint 28 exact peer dispatch.
- Start with OPT-125M and expand only through architecture-specific parity evidence.
- Preserve a versioned stateless protocol for compatibility and transport diagnosis.
- Do not claim cache transfer between peers; an alternate must rebuild from known history under defined safety rules.

## Work Plan

- [x] Define versioned `open`, `prefill`, `decode`, `close`, and `cancel` RPC operations.
- [x] Create stable session, route, request, and operation identities.
- [x] Add bounded per-provider cache ownership, admission, expiry, eviction, cleanup, and diagnostics.
- [x] Keep one exact peer-addressed route stable for a session.
- [x] Send full context only during prefill and only new positions during decode.
- [x] Add OPT adapter support for remote cached attention state with Hugging Face parity.
- [x] Define cancellation and ambiguous-outcome semantics for every session operation.
- [x] Recover one ambiguous transport reset through an exact operation identity and provider-retained result.
- [x] Rebuild state on a safe alternate from known token history without blind ambiguous replay.
- [x] Preserve useful-work idempotency by retaining stateless receipt RPC whenever incentives are enabled; signed session receipts remain a future protocol version.
- [x] Expose prefill/decode bytes, cache use, eviction, first-token time, and per-token timing.

## Test Plan

- Compare cached distributed logits and greedy output with stateless distributed and direct Hugging Face output.
- Prove decode payload remains bounded as total sequence length grows.
- Exercise cache admission, expiry, eviction, explicit close, cancellation, worker unload, and backend shutdown.
- Fail the selected provider before dispatch and rebuild once on an exact healthy alternate.
- Produce an ambiguous in-flight failure and prove an exact one-time replay returns the provider-retained result without duplicate accepted work.
- Run local real-Hivemind and physical relayed session generation with request-correlated evidence.

## Acceptance Criteria

- [x] Cached and stateless logits and greedy-token selection match the accepted tolerance in local OPT tests.
- [x] Decode traffic no longer grows with the full accumulated sequence.
- [x] Cache memory and concurrency are bounded and observable.
- [x] Cancellation, timeout, unload, and peer loss have bounded release through cancel, runtime expiry, and shutdown cleanup.
- [x] Safe alternate recovery rebuilds once; ambiguous execution never changes route or replays blindly. One reset may retry the exact fingerprint-bound operation once when the provider can return its retained completion.
- [ ] One real two-device relayed session completes with correlated prefill and decode evidence.

## Physical Test Gate

Run this only after both devices and the VPS use the same committed source and the matching freshly rebuilt executable.

1. Set `DISTRIBLLM_INCENTIVES_MODE=off` on both participants. Session protocol version one intentionally falls back to stateless receipt RPC in shadow or credit mode.
2. Start the VPS relay and placement coordinator using the Sprint 30 deployment configuration, then start the packaged application on both Windows devices.
3. On device one, use Custom to serve OPT-125M layers `0-6`. On device two, use Recommended and verify it reserves `6-12`; do not proceed if it proposes an overlapping range.
4. Wait until Monitoring reports complete coverage, both exact providers healthy, Generator ready, and Route ready.
5. Start inference on device one and generate at least eight tokens. Record the first-token result and keep both applications running.
6. In Monitoring, verify `Session Protocol v1`, one prefill, one-position decode activity, non-growing decode wire bytes, and provider session cache counters. Export diagnostics from both devices immediately after the request.
7. Repeat with device two as generator and device one still serving its half. Export diagnostics again.
8. For failure recovery, add a fully disjoint standby route, stop one active provider before a decode dispatch, and verify exactly one `rebuilt_from_known_history` event. For an ambiguous reset, verify at most one `exact_operation_result_replay` on the same peer, same range, same operation ID, and no duplicate provider token positions.
9. Save the device-one, device-two, and VPS logs under one test identifier. A pass requires a complete response in both generator directions, matching exact peers and ranges, any recovery to be exact-operation-only, and zero active provider sessions after close.

---

## Session Log

### 2026-08-23 - Create session-aware inference sprint

- What changed: created the sprint for versioned prefill/decode RPCs, bounded remote key/value caches, exact route ownership, parity, cleanup, and accounting-safe recovery.
- Why: the architecture audit identified full-sequence retransmission and recomputation as a growing sustained-relay load that is absent from the successful one-position startup canary.
- Status: planning is complete and remains behind the stateless transport baseline plus Sprints 28 through 30; no runtime source changed in this session.

### 2026-08-23 - Implement version-one OPT sessions

- What changed: added fixed-frame lifecycle RPCs under peer-scoped role-two expert UIDs, stable request/session/route/operation identities, provider-owned Hugging Face dynamic caches, one full prefill followed by one-position decodes, explicit close/cancel, and stateless compatibility.
- Safety: cache count, total bytes, per-session bytes, positions, operation history, and time to live are bounded. Idle expiry runs inside the Hivemind runtime process; counters are multiprocessing-safe and visible to the parent API. Replayed tensor operations are rejected.
- Recovery: the client preflights every exact hop. It may rebuild once from complete known token history only after a pre-dispatch failure and only on a different complete route. In-flight ambiguity still stops and cancels without replay.
- Accounting: incentives-enabled generation remains on the existing stateless signed-receipt path, preventing duplicate accepted work while session receipts are undefined.
- Observability: correlated client/provider logs and Monitoring now show protocol version, prefill/decode bytes and duration, average decode latency, peak provider cache use, active sessions, evictions, admission rejections, and rebuild count. Acceptance artifacts whitelist these metrics.
- Verification: 432 backend tests pass, including OPT cached/stateless output parity, constant decode input size, limits, expiry/eviction, unload, exact route lifecycle, safe rebuild, ambiguous no-replay, generator integration, and a real local Hivemind 1.1.12 session RPC. Frontend type checks, 20 launcher tests, four renderer-flow tests, production build, and lint with zero errors also pass; 81 pre-existing formatting warnings remain. The packaged physical test is still open.
- Status: source implementation is complete; do not close this sprint until the two-device relayed gate above passes and the user explicitly requests closure.

### 2026-08-23 - Make physical session evidence machine-verifiable

- What changed: added an acceptance validator mode that requires a minimum generated-token count and preserves and validates session protocol version, prefill bytes and duration, decode bytes, duration, average latency and call count, peak provider cache bytes, and rebuild count; made the final architecture manifest require this session evidence in the hash-bound incentives-off relay report.
- Why: diagnostics captured the session fields, but the prior combined report discarded them and allowed the Sprint 31 topology gate to rely only on an operator-entered matrix hash.
- Status: twenty-two focused evidence and manifest tests and all 451 backend tests pass. The validator now rejects missing or invalid session metrics; a real two-device relayed session is still required before this sprint can close.

### 2026-08-24 - Add bounded idempotent session-operation recovery

- What changed: each provider retains a bounded CPU copy of a completed prefill or decode result together with its operation, route, position, byte count, and SHA-256 input fingerprint. After only an ambiguous transport reset, the generator creates one fresh exact-peer RPC client and retries the same operation identifier and same tensors once.
- Safety: the retry cannot select another provider, another layer range, a new operation identifier, a changed tensor, or an unbounded number of attempts. A provider returns the retained completion without running model layers again; mismatched identity, expired cache, oversized retained result, and any retry failure remain terminal and visible in diagnostics. Replay responses do not increment served-request or token-position accounting twice.
- Observability: session metrics now expose retained-result bytes/count, served retained results, retention rejection/eviction, per-hop recovery metadata, and whether a provider returned a retained operation.
- Verification: 33 focused session and coverage-serving tests pass, including completed-response loss followed by exact retained-result recovery, input-identity conflict rejection, cache limits, and no duplicate worker accounting. Two-device relayed acceptance remains the release gate.

### 2026-08-24 - Make session opening atomic with its topology decision

- What changed: generation now prepares, exact-peer-preflights, and opens a session from one opaque topology snapshot instead of checking session availability and then independently discovering a second route. The preparation outcome is prompt-free and preserved in terminal runtime diagnostics with discovery, capability-filter, and exact-peer-preflight reasons.
- Why: Device 1 completed a real sixteen-token relayed `0-6 -> 6-12` session generation, but its next request failed before tensor dispatch with no healthy session-v1 route while the same bundle showed fresh discovery, both providers healthy, and the identical route ready. The old two-read path could produce exactly that contradiction during transient DHT inconsistency.
- Safety: a missing or failed pre-dispatch session preparation falls back to the existing stateless-compatible route; it never retries a dispatched session operation, changes a selected session route, or relaxes exact-operation replay limits.
- Verification: 197 focused session, coverage, and generation-readiness tests pass, including a regression that fails if opening a prepared session performs a second topology lookup and a generator integration test that proves it passes the prepared route through. Physical relayed repetition remains open.

### 2026-08-24 - Preserve rejected session-route capability evidence

- What changed: session preparation now records every complete candidate route it evaluated, each hop's protocol version, session-endpoint presence, hidden-size contract, and explicit rejection reason. Route fingerprints are also safe for incomplete DHT advertisements, so the diagnostic path cannot hide a missing session endpoint behind a secondary key error.
- Why: Device one repeatedly rejected the session route with an empty compatible-candidate list while both device diagnostics showed a fresh, healthy `0-6 -> 6-12` relay chain with session protocol version one. Replaying that exact captured topology through the source filter yields one valid candidate, so the missing evidence is the topology observed at generation time rather than a proven worker-side defect.
- Status: focused session and generation-readiness verification passes. The next two-device package must capture the per-hop candidate evaluation before changing route-selection policy; physical session acceptance remains open.

### 2026-08-24 - Recover a sole route after confirmed session cleanup

- Evidence: the instrumented physical run evaluated one complete `0-6 -> 6-12` session route. Both hops advertised protocol version one, a session RPC endpoint, hidden size 768, and no rejection reasons. Provider counters showed one prefill, thirty-one decodes, one clean cancellation, and one retained-result replay, proving that the session had executed before the terminal preparation message.
- Root cause: a later pre-dispatch exact-peer lookup failure entered the rebuild path. Rebuild correctly preferred a different route, but it unconditionally excluded the previous route even after both providers confirmed cancellation. With only one complete route, the compatible candidate was therefore filtered out and surfaced as `no_compatible_complete_session_route` despite being capability-eligible.
- Change: rebuild still tries a different complete route first. If no alternate passes and cancellation completed on every hop, it may create fresh exact-peer clients, reopen the same route, and prefill from the known token history once. If any cleanup is incomplete, the old route and peers remain excluded and recovery stops.
- Verification: 477 backend tests pass. Frontend type checks, 29 launcher tests, the backend-runtime payload test, and seven renderer-flow tests also pass. The next physical package remains the acceptance gate.

### 2026-08-25 - Retain exact-peer handles for the session lifetime

- Evidence: one physical `e5c2582` run opened two sessions, cancelled two sessions, completed two prefills and fifty-seven decodes, then failed before a later decode dispatch while resolving the already-selected `6-12` session expert with `P2PDaemonError: stream reset`. A subsequent run on the same earlier build completed generation through the intended `0-6 -> 6-12` peers, confirming an intermittent control-stream failure rather than a deterministic model or topology defect. This proves same-route cleanup and reopen worked once, and locates the failed run in repeated exact-peer control resolution rather than tensor execution. The two submitted diagnostic attachments are byte-identical, so they count as one unique device report.
- What changed: the client now retains the exact `RemoteExpert` handles that passed preparation and opened each provider session. Prefill, every decode, close, and cancel reuse those handles instead of repeating expert discovery and `rpc_info` lookup for every operation. Ambiguous-operation recovery remains the only path that creates a fresh exact-peer handle, and a successful recovery replaces that hop's retained handle.
- Safety: every retained handle is created by exact-peer preparation for its immutable route position and is cleared on start, end, close, cancel, and rebuild. Dispatch stops if the retained handle count no longer matches the selected route. No alternate provider or changed operation identity is introduced after dispatch.
- Verification: all 487 backend tests pass, including a new regression proving that one open, one prefill, five decodes, and close resolve exactly two experts total. Frontend type checks, 29 launcher tests, the packaged-backend payload test, and seven renderer-flow tests also pass. A newly packaged two-device relayed run is still required for physical acceptance.
- Package: commit `81a768a60f42c0a951e4c7ef78b7085d8a16a1bd` was built natively on Windows and audited with 65 manifest-bound backend files, zero forbidden application or backend entries, and embedded manifest SHA-256 `6d33ac0fa756ff71f0714e0bcb2ec2aea94722527392e1044cfa9b34c6683c7b`. The distributed portable artifact SHA-256 is `67eccff5aae01905c57f2df1832ace2a623a13258fcacda3d0ece3c31c4f3c91`.
