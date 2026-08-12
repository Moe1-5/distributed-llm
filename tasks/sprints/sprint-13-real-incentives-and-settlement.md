# Sprint 13 - Real Incentives and Settlement

**Goal:** Turn simulated contribution counters into signed proof-of-useful-work receipts and a project-owned read-only credit ledger, with credit mode gated by live validation.
**Start:** 2026-08-12
**End:** TBD

---

## Problem Summary

Sprint 06 intentionally added simulated contribution accounting, not real token incentives. The source system can now record model-aware and contribution-aware serving metrics, but it is not ready to issue rewards, expose balances, or let users claim tokens.

Real incentives should wait until useful work can be measured reliably and abuse-resistant proof exists. Rewarding compute before route correctness, health checks, and receipt validation would risk paying for stale, failed, fake, or low-quality work.

Sprints 10 and 11 completed the gated-model local import flow and instruction-ready model expansion. Incentives remain important, but this sprint stays open because real multi-device inference, route-health evidence, signed contribution receipts, and anti-abuse rules are not yet proven.

---

## Prerequisites and Gates

- [ ] Direct HuggingFace versus distributed parity is validated for the target demo/transport model.
- [x] Local multi-node split inference is proven with complete compatible layer coverage.
- [ ] Real multi-machine inference is proven across separate devices.
- [x] Gated-model local import is reliable enough that model access does not depend on pasted tokens.
- [x] Route selection identifies the exact complete subset of serving nodes before inference.
- [x] Serving nodes can produce worker-signed receipts and generator-signed acceptance.
- [x] Receipts include model revision, layer range, peer and app identities, position count, commitments, route, session, request, nonce, and timestamps.
- [x] Replay, tampering, invalid route membership, unsupported model revisions, and self-dealing are rejected.
- [x] Reward weighting excludes spoofable hardware and latency claims and pays only accepted position-layer work.

## In Progress

- [ ] Run the receipt-capable RPC in shadow mode across two independent devices.
- [ ] Review live accepted/rejected receipt evidence before enabling credit mode.

## Todo

- [ ] Deploy the FastAPI settlement service behind VPS TLS and rate limiting.
- [ ] Exercise off, shadow, and credit configuration in the deployment topology.
- [ ] Add durable client-side retry storage if shadow testing shows receipt loss during outages.
- [ ] Keep transfers, claims, withdrawals, and model-access gating out of this sprint.

## Done

- [x] Added persistent Ed25519 app identities separate from p2p identities.
- [x] Added BLAKE3 tensor and route commitments with canonical signed JSON.
- [x] Added an optional receipt-capable expert while preserving the original RPC.
- [x] Added asynchronous paired-receipt submission and local outcome tracking.
- [x] Added a VPS-deployable FastAPI settlement app with SQLite WAL storage.
- [x] Added append-only receipt and credit records with policy-versioned integer rewards.
- [x] Added account, entries, receipt submission, and policy APIs.
- [x] Added an Incentives page for identity, mode, connection, credits, receipts, and useful work.
- [x] Added cryptographic, abuse, durability, concurrency, and pagination tests.

---

## Acceptance Criteria

- [x] The system verifies a worker receipt against a generator-accepted completed route.
- [x] Reward accounting is model-revision-aware, layer-aware, position-aware, and policy-versioned.
- [x] Failed, standby, duplicate, self-dealing, stale, malformed, or unverifiable work cannot earn credits.
- [x] The UI is read-only and exposes no claim, transfer, withdrawal, or model-access control.
- [x] Settlement is gated by off, shadow, and credit modes and defaults to off on clients.
- [ ] Shadow mode is verified through real receipt-capable RPC on two devices.
- [ ] Credit mode is explicitly approved after live evidence review.

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

### 2026-08-12 - Implement proof-of-useful-work receipts and settlement ledger

- What changed: added app identities, signed request/worker/acceptance receipts, an optional receipt RPC, asynchronous submission, the VPS FastAPI and SQLite WAL ledger, read-only accounting APIs and UI, explicit dependencies, environment configuration, documentation, and abuse/durability tests.
- Why: the approved design rewards accepted inference service without mining, hardware self-reporting, token-gated inference, or premature payout mechanics.
- Status: 149 backend tests plus 19 subtests, frontend type checks, lint, production build, and standalone shadow settlement startup pass; live two-device shadow validation and deployment review remain open before credit mode approval.

### 2026-08-12 - Isolate incentives work on its feature branch

- What changed: moved the intact coverage and incentives worktree to `feature/coverage-aware-incentives`, codified the purpose-matched `feature/` branch rule, and reran the complete automated verification suite.
- Why: active sprint work must remain independently reviewable while live reachability approval is pending.
- Status: 149 backend tests plus 19 subtests, frontend type checks, lint, and production build pass; the sprint remains open for two-device shadow-mode evidence and explicit credit-mode approval.
