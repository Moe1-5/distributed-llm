# Sprint 22 - Relay Tensor RPC Stability

**Goal:** Make expert tensor forwards complete predictably through the project VPS relay, with bounded attempts, actionable failure evidence, and no retry loop.
**Start:** 2026-08-13
**End:** TBD
**Status:** In progress; physical testing isolates the blocker to the common sustained tensor-over-relay path.

---

## Problem Summary

The live two-device run proves bootstrap discovery, relay reservations, route coverage, generator startup, and a small one-position tensor canary. Real tensor inference still does not complete reliably: the generator reports `stream reset` after dispatch. The client now uses bounded attempts, stable request identity, route reuse, failure classification, and accounting-safe ambiguous-outcome handling, but the physical transport reset remains unresolved.

The latest incentives-off baseline selected the normal legacy expert and failed on the first real prompt after its canary passed. Receipt RPC, settlement, DHT lease expiry, clock skew, and peer rotation are not the unique cause of this occurrence. The next isolation boundary is activation or response size, execution duration, stream lifecycle, or relay/p2p behavior shared by legacy and receipt generation.

This sprint owns the immediate inference blocker. It must distinguish worker execution failures, client cancellation, relay resets, receipt fallback, and slow requests instead of masking all of them as identical retries.

## Dependencies and Boundaries

- Build on Sprint 16's verified relay reservation and expert metadata RPC.
- Preserve Sprint 17's complete adjacent route semantics and Sprint 13's no-credit-on-failure rule.
- Treat relay log `131072` byte copies as buffering evidence, not proof of a payload limit until a controlled payload test confirms it.
- Keep one immutable route for one forward attempt.
- Do not implement automatic alternate-route failover; Sprint 21 owns that behavior.
- Do not claim two-device completion from local or simulated tests.
- Keep this sprint focused on the stateless tensor payload sweep, direct-versus-relay comparison, stream lifecycle, and correlated transport failure.
- Peer-addressed expert ownership, persistent DHT lifecycle, transactional placement, and session key/value caching belong to Sprints 28 through 31.
- Physical evidence produced after Sprint 28 must assert that the selected peer is the peer that actually executed every hop.

## Work Plan

- [x] Add typed RPC attempt and backoff configuration with validated environment overrides.
- [x] Replace module-level retry constants with one bounded request policy and terminal error contract.
- [x] Record request ID, hop, attempt, input bytes, sequence length, elapsed time, and classified failure without recording tensor values.
- [x] Prevent receipt fallback from multiplying legacy forward attempts or awarding uncertain work.
- [x] Cache one discovered route snapshot for a generation request and invalidate it at session boundaries.
- [x] Add worker-side request timing and exception evidence around actual model execution.
- [x] Add controlled real-Hivemind payload coverage above 128 KiB and compression boundary tests.
- [x] Add a checkpointed physical legacy payload-sweep CLI with exact worker and transport validation.
- [x] Update debugging guidance with correlated generator, worker, and VPS evidence.

## Test Plan

- Unit tests cover policy validation, retryable classification, bounded backoff, terminal errors, and cancellation.
- A failed hop performs no more than the configured total attempt count.
- Receipt-enabled fallback never causes an unbounded or hidden second retry tree.
- Route discovery happens once per generation request rather than once per generated token.
- Real local Hivemind peers forward representative OPT activations and payloads larger than 128 KiB.
- Existing coverage, receipt, local split, relay probe, and acceptance-evidence tests remain green.
- A live two-device relay run records one complete OPT-125M generation with no unexplained stream reset.

## Acceptance Criteria

- [x] Every tensor RPC attempt has correlated client and worker diagnostics.
- [x] Retry count and backoff are finite, validated, and visible in the terminal error.
- [x] Cancellation stops additional attempts promptly.
- [x] A representative activation larger than 128 KiB crosses a real Hivemind RPC locally.
- [x] Failed or uncertain attempts create no accepted useful-work settlement.
- [ ] Two physical devices complete inference through the VPS relay without a retry loop.

---

## Session Log

### 2026-08-13 - Create relay tensor RPC stability sprint

- What changed: created the focused sprint for correlated transport evidence, bounded retry policy, route reuse, payload coverage, and live relay acceptance.
- Why: live discovery and generator startup now pass, but tensor forwards repeatedly end in `stream reset` and retry without completing generation.
- Status: approved and in progress; source implementation and controlled transport tests are next.

### 2026-08-13 - Bound expert attempts and reduce ordinary activation traffic

- What changed: added validated RPC attempt policy and failure classification, stopped ambiguous reset retries, restricted receipt fallback to pre-execution absence, reused one route per generation session, added request/worker timing evidence, restored dialing-only generator startup, and enabled float-sixteen wire compression only for legacy inference.
- Why: live relay calls reset after transferring data, while repeated blind attempts could duplicate completed work; route discovery and uncompressed activations also added avoidable latency and traffic.
- Status: 195 backend tests plus 22 subtests pass, including a real exact Hivemind receipt activation larger than 128 KiB and compression restoration coverage. A correlated physical two-device relay generation remains the acceptance gate; cancellable RPC execution timeout remains owned by Sprint 20 because Hivemind's synchronous expert call cannot be safely abandoned without leaving remote work running.

### 2026-08-13 - Make generation cancellation prompt and accounting-safe

- What changed: moved synchronous Hivemind route forwards off the FastAPI event loop and threaded one cancellation event through same-peer retries, retry backoff, route attempts, and hops.
- Semantics: an already-dispatched remote kernel is allowed to finish, but its result and pending receipts are discarded after cancellation. No additional retry, alternate route, or downstream hop starts.
- Evidence: controlled tests interrupt a five-second retry backoff after one call and keep the event loop responsive. The real local split probe completed 26.522 milliseconds after a stop request during active route work, generated no token, and did not call the second hop.
- Remaining gate: correlated generator and worker diagnostics plus complete inference still require the two-device VPS relay run.

### 2026-08-13 - Correlate legacy tensor attempts without changing the RPC schema

- What changed: successful and failed generator attempts now log request ID, hop, peer, RPC UID, attempt budget, range, tensor shape, byte count, and duration. Legacy workers log the matching RPC UID, range, shape, bytes, duration, and exception state around execution.
- Why: ordinary non-receipt inference cannot carry application metadata without changing the established expert tensor schema, but the shared RPC UID and non-secret tensor metadata provide a deterministic join across generator and worker logs.
- Verification: the full backend suite passes with 250 tests and 54 subtests. A physical relay generation remains the only transport acceptance gate.

### 2026-08-14 - Align relay health checks with observed metadata latency

- What changed: raised the default expert metadata health-probe deadline from three to fifteen seconds, extended the DHT staleness floor to thirty seconds to preserve the typed configuration invariant, documented the relay-latency rationale, made Monitoring use the fast model catalog instead of a full route scan on every refresh, and separated DHT advertisement labels from RPC health with visible failure reasons.
- Why: two relayed layer providers were shown as DHT-online but RPC-offline, while the generator rejected the complete route and Monitoring reported request timeouts. The verified relay path can take several seconds for expert metadata setup even when reservation succeeds quickly.
- Status: local implementation is ready for backend/frontend verification; physical relayed metadata and tensor forwarding remain open acceptance gates.

### 2026-08-22 - Isolate and correlate the receipt-RPC failure boundary

- What changed: traced startup canary, streamed chat, Trace, normal expert, and receipt expert selection end to end; passed the outer route request ID into the signed receipt request; added legacy/receipt mode, effective expert UID, and receipt-stage logging; added regression coverage; created `docs/RELAY_RECEIPT_RPC_STREAM_RESET_HANDOFF.md`; and corrected the incident, runtime, frontend, index, context, and lesson records that had treated Trace as equivalent to shadow chat.
- Why: the persistent-identity physical run kept Device 2 independently visible and passed the startup tensor canary, yet real shadow chat still reset. Source review proved that the passing canary and Trace use `distribllm.0.12`, while chat starts a useful-work session and selects `distribllm.999999.0.12`.
- Verification: all 19 useful-work incentive tests pass, including the real local Hivemind receipt RPC integration; five focused receipt, retry, and ambiguous-reset tests pass; Python compilation and diff checks pass. The physical relay was not available in this repository session.
- Status: no speculative transport or unsafe retry change was made. The next decisive run is the same relayed prompt with incentives off, followed by shadow mode with request-correlated generator, worker, and VPS logs. Direct shadow mode then separates a relay-specific fault from a receipt-protocol fault.

### 2026-08-22 - Stop relay-test preflight on missing pytest runner

- What changed: no source or dependency changes were made; checked the pushed `7c69382` revision, confirmed the local packaged executable is still the older `9fec4a8` artifact, and attempted the focused useful-work, failover, and generation-readiness regression command.
- Why: the controlled physical relay matrix requires one validated source revision and a matching executable containing the new request-correlation diagnostics.
- Status: preflight failed before test collection because `pytest` is not installed in the current locked backend environment. The physical legacy-versus-shadow pass was not started, and a new executable for `7c69382` has not yet been built.

### 2026-08-22 - Pass local/package gates and stop before legacy dispatch

- What changed: used an ephemeral pytest runner without changing project dependencies; ran the focused useful-work, failover, and generation-readiness suites; passed frontend type checking plus launcher and renderer-flow tests; built and audited a new portable executable; verified a live VPS circuit reservation; and independently observed Device 2's stable full worker plus both expert UIDs.
- Why: Test A needs a validated generator checkout, a healthy relay, and the same remotely visible worker before it can distinguish general sustained relay instability from a receipt-only failure.
- Verification: 161 tests and 19 subtests passed; 20 launcher tests and 2 renderer-flow tests passed; the package contains 36 ASAR entries and zero forbidden entries; the portable executable is 87,654,628 bytes with SHA-256 `b7a8aeaf10587a0037849ffe67841720470ec03f0c7fec8200e42acf2c4b517b`; the relay probe returned `ok: true`; and the one-shot independent observer returned `ok: true` for Device 2 peer `QmRevwu67tzcBjWW21u11Q7oPuhbtEodmuDD87aoW9Yp6z` with both `distribllm.0.12` and `distribllm.999999.0.12`.
- Status: the relayed legacy prompt was not dispatched. Starting the controlled incentives-off backend failed because `127.0.0.1:8000` was already occupied by another process whose source revision and incentives mode were not established. The existing process was not stopped or reused, preserving the validity of the test matrix.

### 2026-08-22 - Correct manual-backend interpretation for Test A

- What changed: reviewed the Electron launcher, renderer API client, manual backend command, relay-reset handoff, and current incident report; recorded the distinction between managed launcher ownership and backend API health in the active lessons.
- Why: the user confirmed that Uvicorn had successfully started the controlled incentives-off backend manually. Electron then correctly refused to start a second managed process on port 8000, but the renderer remains able to use the existing API directly.
- Verification: the live manual process returned `status: online` from `/status` and `mode: off` with settlement disabled from `/incentives/accounting`.
- Status: `Backend port is already in use` in Settings is not a Test A transport failure in this workflow. Leave the manual backend terminal running, do not press the managed Start button, verify `/status` and `/incentives/accounting`, and continue the legacy relay test from the Nodes and Network pages.

### 2026-08-22 - Restore the packaged EXE as the physical backend lifecycle

- What changed: corrected the operator instructions after the user confirmed that Device 2's backend can only be started successfully through the packaged Electron managed launcher; added an active lesson preserving that proven physical lifecycle.
- Why: the launcher injects network and relay variables but does not inject `DISTRIBLLM_INCENTIVES_MODE`, so the EXE-only Test A workflow must set incentives to `off` in the repository-root `.env` and then restart through Settings.
- Status: the prior manual-start instruction is withdrawn for Device 2. Test A may continue after both EXE-managed backends report incentives mode `off`; Test B requires restoring `shadow` in both root `.env` files and restarting both managed backends.

### 2026-08-22 - Record failed incentives-off legacy relay baseline

- What changed: captured Device 1 incentives, generator, and runtime state after the first Test A prompt; reviewed the earlier attached shadow-run status separately; corrected the observer command requirements; and updated the incident report, relay handoff, current context, and active lessons.
- Why: the physical prompt failed after the generator and route reached ready, while the initial observer process had failed to start because its bootstrap address used the worker peer ID and required a receipt expert in incentives-off mode.
- Verification: Device 1 reported incentives `off`, relay-verified peer `QmQXdUAw4SNPoA6gPMTAY6xPaCS7Em1EutF8FQpE9RWGMh`, only legacy UID `distribllm.0.12`, a passing 2.26-second startup canary, and request `cbdcb4fa-0647-4c26-ba64-a5c2f9cca00b` failing after one dispatched legacy attempt with `ambiguous_transport: stream reset`. The provider snapshot reported eleven accepted and completed RPCs with zero failures, rejections, or timeouts.
- Status: Test A failed and Test B should not be run for receipt isolation. A corrected post-failure observer returned `ok: true` for the current normal expert, member lease, metadata horizon, and remote publication; Device 2 local state and VPS evidence for the failure window remain to be collected before shutdown.

### 2026-08-22 - Document the complete physical test chronology and revised boundary

- What changed: expanded `docs/RELAY_RECEIPT_RPC_STREAM_RESET_HANDOFF.md` with the VPS, SSH tunnel, artifact, clock synchronization, packaged-backend, worker, observer, generator, prompt, Trace, and evidence-capture steps actually performed; added a passed, failed, invalid, and incomplete outcome ledger; corrected stale receipt-only hypotheses and follow-up tests; updated the incident header to identify the latest `7c69382` incentives-off artifact and mode; and refreshed both documentation indexes for the handoff's broader scope.
- Why: the physical investigation spans multiple devices and shells, and Test A changed the root-cause boundary after earlier documentation had ranked receipt RPC as the primary suspect.
- Status: the latest blocker is documented as a common sustained tensor-over-relay reset: the small legacy canary passes, but the first real legacy forward resets. The next controlled work is a legacy payload sweep plus direct-versus-relayed comparison; correlated Device 2 and VPS evidence for the Test A request remains missing.

### 2026-08-22 - Add the controlled physical legacy tensor sweep

- What changed: added `backend/tensor_payload_probe.py` and focused tests; the probe starts a dialing-only client beside the EXE backend, requires one exact full-model worker and verified relay or direct metadata, forces legacy mode with one attempt, repeats the one-position canary before increasing through sequence length 128, records logical and serialized tensor sizes plus timing and failure class, checkpoints private evidence before every dispatch, and stops at the first failure. Updated the incident, handoff runbook, current context, and file indexes.
- Why: the incentives-off physical baseline proved that receipt and settlement are not the unique cause, but chat sampling could not distinguish payload size from repeated-stream lifecycle or topology.
- Verification: Python compilation, CLI help, and diff checks pass. Twenty-one focused pytest tests pass across the new probe, local failover probe, and route failover behavior. A separate broad generation-readiness unittest invocation emitted fifteen passing test markers but was stopped after several minutes without further output; no failure was reported and no production source used by that suite changed in this session.
- Status: the probe implementation is ready. The physical relay sweep and same-matrix direct comparison still require Device 2's EXE-managed worker and the live VPS, so no transport threshold or fix is claimed yet.

### 2026-08-22 - Pass the whole-system automated baseline

- What changed: no application source was changed during this test gate; established a linear whole-system checklist covering isolated local inference on each device, reciprocal full-worker relay inference, adjacent split serving in both ownership directions, and final shadow-accounting and UI lifecycle checks.
- Why: component-level success and the existing one-direction relay failure do not establish which parts of the complete two-device product currently work.
- Verification: all 291 backend tests and 54 subtests passed; frontend type checking, 20 managed-launcher tests, and 2 independent renderer-refresh tests passed; the Windows package audit reported 36 ASAR entries and zero forbidden entries. The portable executable remains 87,654,628 bytes with SHA-256 `b7a8aeaf10587a0037849ffe67841720470ec03f0c7fec8200e42acf2c4b517b`, matching commit `7c69382` evidence.
- Status: automated and artifact gates pass. Physical Gate 1 is now Device 1 isolated local full-layer serving and one user-visible generation; per the repository system-test protocol, the pass stops and reports immediately on the first failure rather than fixing or skipping ahead.

### 2026-08-22 - Stop the mislabeled local gate on route-ownership evidence

- What changed: no application source was changed; inspected the submitted Network, Monitoring, and Inference screenshots and corrected the whole-system matrix to require exact peer and layer ownership for every route.
- Why: Device 1 started local peer `QmTWGvf1...` for layers `0-12`, but its generator selected existing peer `QmQXdUAw...` as the active relayed full-model route, classified `QmTWGvf1...` as an alternate, and tagged the completed response with `QmQXdUAw...`.
- Verification: the screenshots consistently show the same active/alternate ownership across route preview, Monitoring, and response trace. A local API read from the assistant sandbox could not reach the user's EXE backend and is therefore unverified rather than a product failure.
- Status: the attempted local gate is invalid, not failed. It provisionally demonstrates one completed Device 1 generator to remote full-worker inference, while local execution and the required `0-6` plus `6-12` split remain untested. The current pass stops here before any topology is changed.

### 2026-08-22 - Localize the latest split failure and export correlated evidence

- What changed: recorded the submitted adjacent split result; added prompt-free backend failure events that retain the last route request ID, failure class, peer, layer range, route revision, and coverage revision; attached those details to the WebSocket error; and included the bounded backend snapshot in the Settings diagnostic export.
- Why: the `0-6` plus `6-12` route became ready and streamed model output before a later forward failed with uncertain execution at layers `0-6`, so discovery and coverage were no longer the terminal boundary but the application did not preserve enough evidence in one place.
- Verification: all 293 backend tests and 54 subtests pass, including a regression for the request-correlated failed-hop event; the frontend type check, four renderer-flow tests, 20 launcher tests, lint with zero errors, and production build pass. The audited clean-identity portable EXE embeds commit `232acb1bdcba5c7347a8f58aba056881115a8308`, contains 36 ASAR entries and zero forbidden entries, and has SHA-256 `0b6121de080fb5f16df53d9f47df95d39a5ff07cd99892eef1f0e2af23de89ee`.
- Status: the transport defect is not fixed. The latest evidence localizes the terminal attempt to the first split hop while showing that earlier token forwards traversed the complete route. The next physical isolation remains the controlled relay payload sweep followed by the identical direct comparison, with ambiguous automatic replay still disabled.
