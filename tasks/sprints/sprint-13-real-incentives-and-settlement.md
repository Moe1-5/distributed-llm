# Sprint 13 - Real Incentives and Settlement

**Goal:** Turn simulated contribution accounting into real incentive/reward behavior only after inference correctness, route health, anti-abuse checks, proof/receipt prerequisites, and gated-model local import reliability are ready.
**Start:** 2026-08-12
**End:** TBD

---

## Problem Summary

Sprint 06 intentionally added simulated contribution accounting, not real token incentives. The source system can now record model-aware and contribution-aware serving metrics, but it is not ready to issue rewards, expose balances, or let users claim tokens.

Real incentives should wait until useful work can be measured reliably and abuse-resistant proof exists. Rewarding compute before route correctness, health checks, and receipt validation would risk paying for stale, failed, fake, or low-quality work.

Sprints 10 and 11 completed gated-model local import and instruction-ready model expansion. Sprint 17 now provides authoritative selected routes and standby classification. This sprint implements useful-work receipts and shadow settlement while credit mode remains gated behind protocol tests and live multi-device review.

---

## Prerequisites

- [x] Direct HuggingFace versus distributed parity is validated for the target demo/transport model.
- [x] Local multi-node split inference is proven with complete compatible layer coverage.
- [ ] Real multi-machine inference is proven across separate devices.
- [x] Gated-model local import is reliable enough that model access does not depend on pasted tokens.
- [x] Route health checks exist for selected nodes before inference starts.
- [x] Serving nodes produce signed request/response receipts or another verifiable proof-of-service record.
- [x] Accounting records include model, layer range, peer identity, accepted token positions, and route/session identifier without rewarding hardware or latency claims.
- [x] Anti-abuse rules exist for malformed work, stale signed presence, replayed receipts, duplicate request IDs/nonces, altered counters, incomplete routes, and self-dealing identities.
- [x] The approved reward formula accounts for model weight, layer count, and accepted useful positions while intentionally excluding manipulable hardware, reliability, and latency claims.

## In Progress

- [x] Add persistent Ed25519 application identities and signed p2p presence bindings.
- [x] Add optional receipt protocol version one without changing the existing inference RPC.
- [x] Commit to request and response tensors with BLAKE3 and countersign accepted work.
- [x] Add the VPS settlement service with an append-only SQLite WAL ledger and versioned policy.
- [x] Support off, shadow, and credit modes with credit disabled by default.
- [x] Add read-only incentives visibility without exposing private keys or payout controls.

## Todo

- [x] Design signed contribution receipts for served layer requests.
- [x] Add route/session IDs that connect generator requests to serving-node accounting records.
- [x] Define the initial integer reward as position count times served layer count times versioned model compute weight times reward scale; do not reward hardware claims or latency.
- [x] Define non-payment rules for failed, stale, malformed, standby, or unverifiable work; slashing remains out of scope for non-transferable credits.
- [x] Use a project-owned off-chain FastAPI and SQLite settlement service first, with no transfer, withdrawal, conversion, or model-access gate.
- [x] Add backend tests for receipt validation, accounting aggregation, and abuse cases.
- [x] Persist pending settlement submissions across backend restarts with bounded retention and expiry-safe terminal reasons.
- [x] Add a read-only rewards/accounting UI only after backend semantics are validated.
- [ ] Add claim/payout UI only in a later approved sprint after settlement mechanics are proven.

## Done

- [x] Local protocol, settlement, backward-compatibility, and frontend validation.
- [x] Locked VPS settlement service templates and shadow-to-credit rollout runbook.

---

## Acceptance Criteria

- [x] A real independent-peer Hivemind receipt RPC proves that a serving node's signed tensor result survives the wire contract and settles only after generator acceptance on a complete route.
- [x] Reward accounting is model-aware, layer-aware, route-aware, and based only on accepted useful positions; manipulable hardware and latency claims do not affect rewards.
- [x] Failed or unverifiable work cannot earn rewards in local protocol and settlement tests.
- [x] The UI exposes read-only accounting only after backend proof validation; no token, transfer, withdrawal, or claim controls exist.
- [x] Real settlement is behind an explicit off, shadow, or credit feature gate and defaults to off for clients and shadow for VPS installation.

---

## Session Log

### 2026-07-06 - Defer real incentives until prerequisites are ready

- What changed: created the future home for real token incentives, rewards, receipts, anti-abuse checks, and settlement mechanics. This was later moved from Sprint 08 to Sprint 10 so Sprint 08 can handle client refinements and generation diagnostics first.
- Why: Sprint 06 only added simulated contribution accounting; the system is not ready to issue rewards until route correctness, health checks, multi-node validation, proof/receipt design, and anti-abuse controls are done.
- Status: sprint is planned but blocked by prerequisites.

### 2026-07-09 - Move incentives after gated local import

- What changed: moved the real incentives and settlement plan from Sprint 10 to Sprint 11.
- Why: Sprint 10 now focuses on gated-model local import so model access is simple and reliable before incentives are revisited.
- Status: planned for a later sprint; not started.

### 2026-07-09 - Move incentives to Sprint 13

- What changed: renumbered the real incentives and settlement plan from Sprint 11 to Sprint 13.
- Why: the next future sprint slot is needed for model expansion before incentives, so rewards stay behind model access and model-quality work.
- Status: planned for a later sprint; not started.

### 2026-08-12 - Start approved proof-of-useful-work implementation

- What changed: refined Sprint 13 with the approved useful-work protocol, off-chain settlement, three rollout modes, and non-transferable accounting scope; started `feature/useful-work-incentives` from the pushed coverage-aware routing branch.
- Why: Sprint 17 now identifies the route that actually performs inference, so receipts can reward selected successful RPC work and exclude advertisements, standby providers, failures, and idle time.
- Status: protocol and settlement implementation started. Credit remains disabled by default, and live two-device receipt evidence remains an external acceptance gate.

### 2026-08-12 - Implement signed useful-work receipts and shadow settlement

- What changed: added persistent Ed25519 identities, signed p2p presence, canonical JSON byte tensors, BLAKE3 request/response commitments, a separate optional receipt RPC, generator validation and countersigning, complete-forward receipt release, asynchronous fail-open settlement submission, SQLite WAL policy and ledger APIs, read-only incentives UI, and locked VPS service templates and operations documentation.
- Why: reward only selected providers that return accepted inference tensors on a complete adjacent route while preserving the original inference contract for off mode, older nodes, and receipt failures.
- Status: 154 backend tests plus 19 subtests, 14 launcher tests, frontend typecheck/build, changed-file lint, Python compilation, shell syntax, and service-factory smoke checks pass. Shadow mode is ready for deployment review. Direct parity, live VPS shadow deployment, two-device signed receipt submission, standby non-payment evidence, and approval before credit mode remain open, so Sprint 13 is not closed.

### 2026-08-12 - Prove receipt settlement across independent Hivemind peers

- What changed: added a real local Hivemind integration test that starts the legacy and receipt experts, connects a second client-mode DHT peer, forwards a normal variable-length tensor through the receipt expert, verifies the worker signature and tensor commitment, countersigns generator acceptance, and records the pair in shadow settlement with explicit remote-transport cleanup.
- Why: wrapper-level tests could not prove that canonical byte metadata, signed receipts, output tensors, and Hivemind expert schemas survive the actual p2p serialization boundary.
- Status: the independent-peer receipt RPC and settlement path passes in the full suite of 154 tests plus 19 subtests. Live VPS shadow deployment and two-device relay inference remain external acceptance gates; credit mode remains unapproved.

### 2026-08-13 - Add reproducible two-device and standby evidence

- What changed: added a sanitized acceptance collector and validator for local route ownership, adjacent selected ranges, actual replica-aware hop timing, relay/direct transport, generation output, shadow receipt deltas, settlement drain, and optional standby before/after non-payment counters; added an operator runbook and regression coverage.
- Why: the remaining incentive gate must be reviewable from structured evidence produced by the real APIs instead of screenshots or cumulative counters that could belong to an earlier inference.
- Status: 165 backend tests plus 19 subtests pass, including 11 focused evidence tests; Python compilation and `git diff --check` pass. The tool is ready on `feature/two-device-acceptance-evidence`, but separate physical devices, live VPS shadow settlement/restart evidence, and approval before credit mode remain open.

### 2026-08-13 - Prove real local two-peer split inference and parity

- What changed: added a reusable isolated local split probe; started two real Hivemind serving peers for OPT-125M ranges `0-6` and `6-12`, a separate generator peer, deterministic Hugging Face parity checks, worker accounting evidence, private JSON output, and bounded explicit cleanup.
- Why: Sprint 13 still carried an obsolete local multi-node blocker from the former single-node backend even though later lifecycle work added multiple local peers. Incentive rollout needs a current real RPC proof that selected adjacent providers jointly execute one correct model route.
- Verification: the CPU pass completed in 10.404 seconds with two distinct peer IDs, exact adjacent coverage, direct/distributed next-token ID `5`, zero max and mean logit difference, exact two-token greedy output ` the most`, three successful requests and 19 positions per worker, zero worker failures, completed explicit shutdown calls, and no remaining `p2pd` process. The full backend suite passes with 169 tests and 22 subtests.
- Status: direct parity and local multi-node split prerequisites are now proven. Hivemind still emitted late remote-control destructor warnings and one pending task after successful cleanup; this is documented without a same-pass fix or rerun. Real separate-device inference, live VPS shadow review/restart, and approval before credit mode remain open.

### 2026-08-13 - Close cached remote-expert P2P clients cleanly

- What changed: added explicit ownership for Hivemind's DHT-cached replicated P2P control client, closed and cleared it once before client DHT shutdown, integrated the helper into API and probe teardown, and added lifecycle regression coverage.
- Why: successful split and TinyLlama runs left a `ControlClient` write task for destructor-time cleanup because Hivemind 1.1.12 does not close its cached replica in `DHT.shutdown()`.
- Verification: 175 backend tests plus 22 subtests pass. A fresh real OPT-125M `0-6 -> 6-12` pass preserved exact logits and greedy text, served three requests and 19 positions on each peer with zero failures, reported every cleanup flag true, left no `p2pd` process, and emitted no destructor traceback or pending-task message.
- Status: the local remote-expert cleanup finding is resolved on `feature/remote-expert-p2p-cleanup`. Sprint 13 remains open only for separate-device inference, live VPS shadow/restart review, and explicit approval before credit mode.

### 2026-08-13 - Register dependency-ordered future sprint proposals

- What changed: added proposal-only Sprints 18 through 21 for selective worker loading, continuous provider health, expert RPC resource safety, and health-aware route failover; updated active sprint routing, indexes, current state, and implementation roadmap.
- Why: these engineering tracks can be reviewed and implemented on feature branches while Sprint 13 and Sprints 14 through 17 wait for the physical two-device acceptance run.
- Status: planning documents are ready for user review on `feature/future-sprint-plans`. No runtime implementation started, and no existing sprint was closed.

### 2026-08-19 - Document VPS shadow settlement deployment

- What changed: hardened settlement installation with a service-owned virtual environment under `/var/lib/distribllm`, then expanded the deployment and live-testing runbook with systemd and policy validation, loopback-only port checks, per-device WSL SSH tunnels, participant shadow-mode environment, identity isolation, and expected Incentives page results.
- Why: the Incentives page correctly remained inert in off mode, but operators needed an exact distinction between the public bootstrap relay and the separate settlement service plus a safe path to exercise receipts without exposing an unauthenticated HTTP ledger endpoint.
- Status: the documented flow keeps settlement in shadow mode and developer API access off. VPS installation and physical two-device receipt evidence remain user-run acceptance work before any credit approval.

### 2026-08-19 - Audit the system and harden settlement outage handling

- What changed: added a full codebase and finalization report; documented the Incentives connection-refused incident; changed temporary settlement failures from immediate permanent rejection to bounded retry; added a stable BLAKE3 idempotency key and exact duplicate handling that cannot add a second ledger entry; exposed retry state and counters in Electron; and documented the memory-only outbox limitation.
- Why: the participant backend used its own loopback address while the WSL-to-VPS tunnel was absent, and the previous runtime irreversibly discarded each refused receipt. The wider audit was needed to distinguish implemented source, locally verified behavior, unproven physical acceptance, and work required for a backend-bundled final installer.
- Status: source implementation and smoke validation are complete. The three focused retry/idempotency/rollout tests pass; all 17 incentives tests pass in one run, including the real Hivemind receipt RPC; and frontend type checking passes. The API tests use HTTPX ASGI transport because Starlette's threaded test client deadlocked in this Python/AnyIO environment. Both participant backends and the VPS settlement service must still be updated for live recovery validation. Previously discarded receipts cannot be recovered, the queue is not restart-durable, settlement remains in shadow mode, and Sprint 13 remains open for physical two-device receipt and restart evidence.

### 2026-08-19 - Prove settlement retry recovery over real HTTP

- What changed: increased the Electron launcher's and renderer's composite `/status` deadline from 1.5 to 5 seconds; declared the tracked Bun lockfile as the frontend package-manager authority; and refreshed the deployment runbook with the retry-capable artifact identity and tunnel interpretation.
- Why: the composite status endpoint can legitimately exceed 1.5 seconds while collecting generator, node, GPU, token, and model state. Electron Builder also needs one unambiguous dependency manager when producing the Windows package.
- Verification: a real Uvicorn TCP smoke test accepted a signed shadow receipt, returned the same result for an idempotent replay, and left the ledger unchanged; a second real TCP test queued a receipt while settlement was absent, reported retrying, then drained it successfully after settlement started. The 186-test focused backend regression set passes, including all 17 incentives tests and the real Hivemind receipt RPC. Frontend type checking, 20 launcher tests, 2 renderer-flow tests, the portable Windows build, and the package audit also pass. The artifact is 87,658,394 bytes with SHA-256 `4bf954e4325f12e9e3227bd63d483c39d44c05e1cd1a8e8a1b06f6d1b02480e1`; the audit found 36 ASAR entries and zero forbidden entries.
- Status: the source retry and idempotency issue is fixed and locally proven. `RETRYING` with connection refused now specifically means the participant's local port 7101 tunnel is absent; pending receipts recover when that tunnel becomes reachable without restarting the backend. Physical two-device receipt evidence, restart-durable queue storage, authenticated public settlement transport, and approval before credit mode remain open, so Sprint 13 is not closed.

### 2026-08-23 - Make participant settlement submissions restart-durable

- What changed: added a private SQLite participant outbox that commits canonical signed submissions before network delivery, binds the database to the application identity, recovers pending and retrying rows after restart, preserves stable idempotency keys and attempt counts, rejects work before the freshness window closes, records structured terminal reasons, caps active rows, and prunes terminal history to a configured bound; exposed sanitized outbox counters and added recovery, expiry, permission, and retention regressions.
- Why: the bounded in-memory retry queue still lost accepted useful-work receipts whenever a participant backend restarted during a settlement outage, making restart evidence and eventual shadow accounting unreliable.
- Status: the focused incentives suite passes all twenty-three tests, including real Hivemind receipt RPC and durable restart recovery, and the complete backend suite passes all 457 tests. Physical two-device outage/restart evidence, authenticated public settlement transport, and approval before credit mode remain open.

### 2026-08-23 - Bind rewards to the actually loaded checkpoint

- What changed: receipt RPC startup now derives the advertised and signed model revision from the layer loader's resolved checkpoint commit, rejects missing or mutable revisions and mismatched optional assertions, restricts settlement to an explicit reviewed OPT-125M commit allowlist, and activates the change as append-only reward policy version two on existing databases.
- Why: policy version one trusted the mutable label `main`, so the same signed revision could refer to different weights over time and weaken reward reproducibility.
- Status: all twenty-seven focused incentives tests and all four hundred sixty-one backend tests pass, including immutable-policy upgrade, mutable and mismatch rejection, loader-bound metadata, and a real independent-peer Hivemind receipt settlement. Package verification remains to be rerun from the resulting commit; physical two-device shadow and restart evidence remains open.
