# Sprint 06 - Routing, Model Access, and Incentives

**Goal:** Define and implement the next backend/product layer after local validation and client workflow cleanup: multi-node serving strategy, runnable-model semantics, and simulated model-aware contribution accounting.
**Start:** 2026-07-06
**End:** TBD

---

## Problem Summary

The system needs explicit answers for three structural questions:

1. Can one backend process serve multiple local layer slices, or should local split testing run multiple backend processes?
2. Can users inference any registered model, or only models with complete compatible coverage in the current network?
3. Are future incentives global, or model/contribution specific?

Sprint 06 should not begin until Sprint 04 validates the local inference path and Sprint 05 gives users reliable controls/status. Real incentive/reward work before correctness would reward untrusted or useless compute, so Sprint 06 only defines and exposes simulated accounting fields. Real rewards and settlement are deferred to Sprint 08.

---

## In Progress

- [x] Define and expose runnable-model semantics.

## Todo

- [x] Decide backend serving model: node registry in one process or one process per participant.
- [x] Registry path explicitly deferred; no `/node/start`, `/node/stop`, `/status`, or `/nodes` registry redesign is needed while using one backend process per participant.
- [x] If using one process per participant, document local multi-node testing commands and UI limitations.
- [x] Add runnable-model status: supported by registry plus complete compatible route coverage.
- [x] Expose missing layer ranges and wrong-model route reasons before inference starts.
- [x] Define model-aware incentive accounting fields: model, layer range, tokens/requests served, latency, success/failure, hardware class, and identity.
- [x] Decide whether initial rewards are simulated accounting only before real token integration.
- [x] Add tests for model-runnable state and multi-node route readiness.
- [x] Defer real token incentives, balances, claims, payouts, and settlement to Sprint 08 until prerequisites are complete.

## Done

- [x] Current serving strategy is one backend process per serving participant; multi-node local split testing should use multiple backend processes until a node registry is designed.
- [x] `/models` now reports model availability separately from runnable route coverage.
- [x] The frontend model surfaces can explain runnable versus not runnable model state.
- [x] WebSocket streaming reports generator readiness errors instead of mock fallback responses.
- [x] Current Sprint 06 does not design a node registry because the chosen prototype strategy is one backend process per participant; registry design remains future work if the project needs multi-node-per-process support.
- [x] Serving nodes record simulated contribution metrics for model, layer range, device, request success/failure, token positions served, latency, and identity.
- [x] `/incentives/accounting` exposes simulated accounting only, with token UI and reward settlement disabled.
- [x] Real incentive rewards and settlement are deferred to Sprint 08.

---

## Acceptance Criteria

- [x] Multi-node local testing strategy is explicit and testable.
- [x] The app can explain why a model is or is not currently runnable.
- [x] Inference is gated by complete compatible route coverage, not only by model registry membership.
- [x] Future incentive semantics are model-aware and contribution-aware; Sprint 06 implements simulated accounting only.
- [x] No token UI is added before route correctness and accounting fields are validated.
- [x] Backend tests cover the chosen model/routing semantics.

---

## Session Log

### 2026-07-06 - Create routing, model access, and incentives sprint

- What changed: created Sprint 06 to hold structural routing, model access, and incentive decisions split out from Sprint 04.
- Why: these questions affect backend contracts and reward design and should not be mixed into the local validation or UI-control sprint.
- Status: sprint is planned but not started.

### 2026-07-06 - Add runnable-model route status

- What changed: chose one backend process per serving participant for the current prototype; added route-derived runnable status fields to `/models`; updated frontend model status displays; removed the WebSocket mock generation fallback; added tests for runnable and non-runnable model status.
- Why: users should not infer that every registered model is runnable. A model is runnable only when the active network has complete compatible layer coverage for that model.
- Status: focused backend readiness tests pass with 28 tests and frontend typecheck passes. Incentive accounting semantics remain open.

### 2026-07-06 - Add simulated contribution accounting

- What changed: added simulated serving contribution metrics to `InferenceHandler` and local node status; added `/incentives/accounting` with token UI and settlement disabled; documented the no-registry and simulated-accounting decisions; added tests for accounting success, failure, and endpoint contract.
- Why: Sprint 06 needs incentive semantics to be model-aware and contribution-aware without introducing real token rewards before route correctness, health checks, and anti-abuse validation.
- Status: focused backend readiness tests pass with 31 tests. Sprint 06 implementation criteria are satisfied pending user review or explicit sprint closure.

### 2026-07-06 - Defer real incentives to Sprint 08

- What changed: clarified that Sprint 06 implemented simulated accounting only and created Sprint 08 for real rewards, token UI, receipt/proof validation, anti-abuse checks, and settlement.
- Why: the source system is not ready to safely issue incentives until parity, multi-node validation, health checks, receipt/proof design, and anti-abuse prerequisites are complete.
- Status: real incentives are deferred; Sprint 06 remains complete as the accounting-contract sprint.
