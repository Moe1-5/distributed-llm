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

- [ ] Direct HuggingFace versus distributed parity is validated for the target demo/transport model.
- [ ] Local multi-node split inference is proven with complete compatible layer coverage.
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
