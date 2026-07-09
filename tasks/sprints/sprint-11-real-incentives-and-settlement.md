# Sprint 11 - Real Incentives and Settlement

**Goal:** Turn simulated contribution accounting into real incentive/reward behavior only after inference correctness, route health, anti-abuse checks, proof/receipt prerequisites, and gated-model local import reliability are ready.
**Start:** TBD
**End:** TBD

---

## Problem Summary

Sprint 06 intentionally added simulated contribution accounting, not real token incentives. The source system can now record model-aware and contribution-aware serving metrics, but it is not ready to issue rewards, expose balances, or let users claim tokens.

Real incentives should wait until useful work can be measured reliably and abuse-resistant proof exists. Rewarding compute before route correctness, health checks, and receipt validation would risk paying for stale, failed, fake, or low-quality work.

Sprint 10 is now reserved for the simpler gated-model local import flow. Incentives remain important, but they should not compete with model-access reliability.

---

## Prerequisites

- [ ] Direct HuggingFace versus distributed parity is validated for the target demo/transport model.
- [ ] Local multi-node split inference is proven with complete compatible layer coverage.
- [ ] Real multi-machine inference is proven across separate devices.
- [ ] Gated-model local import is reliable enough that model access does not depend on pasted tokens.
- [ ] Route health checks exist for selected nodes before inference starts.
- [ ] Serving nodes produce signed request/response receipts or another verifiable proof-of-service record.
- [ ] Accounting records include model, layer range, device/hardware class, peer identity, request success/failure, latency, token positions served, and route/session identifier.
- [ ] Anti-abuse rules exist for fake work, repeated failed requests, stale DHT metadata, duplicate identities, and self-dealing routes.
- [ ] Reward formulas are reviewed against model size, layer count, hardware cost, reliability, latency, and successful completed work.

## In Progress

- [ ] Not started.

## Todo

- [ ] Design signed contribution receipts for served layer requests.
- [ ] Add route/session IDs that connect generator requests to serving-node accounting records.
- [ ] Define reward weighting by model, layer range, hardware/device class, latency, reliability, and success/failure.
- [ ] Define slashing or non-payment rules for failed, stale, malformed, or unverifiable work.
- [ ] Decide whether rewards settle on-chain, off-chain, or through a simulated ledger first.
- [ ] Add backend tests for receipt validation, accounting aggregation, and abuse cases.
- [ ] Add a read-only rewards/accounting UI only after backend semantics are validated.
- [ ] Add claim/payout UI only after settlement mechanics are proven.

## Done

- [ ] None yet.

---

## Acceptance Criteria

- [ ] The system can verify that a serving node actually contributed to a completed route.
- [ ] Reward accounting is model-aware, layer-aware, hardware-aware, reliability-aware, and latency-aware.
- [ ] Failed or unverifiable work cannot earn rewards.
- [ ] Token/reward UI is not exposed before backend accounting and proof validation pass tests.
- [ ] Real settlement is behind an explicit feature gate.

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
