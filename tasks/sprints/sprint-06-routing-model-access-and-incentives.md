# Sprint 06 - Routing, Model Access, and Incentives

**Goal:** Define and implement the next backend/product layer after local validation and client workflow cleanup: multi-node serving strategy, runnable-model semantics, and model-aware contribution accounting.
**Start:** TBD
**End:** TBD

---

## Problem Summary

The system needs explicit answers for three structural questions:

1. Can one backend process serve multiple local layer slices, or should local split testing run multiple backend processes?
2. Can users inference any registered model, or only models with complete compatible coverage in the current network?
3. Are incentives global, or model/contribution specific?

Sprint 06 should not begin until Sprint 04 validates the local inference path and Sprint 05 gives users reliable controls/status. Incentive work before correctness would reward untrusted or useless compute.

---

## In Progress

- [ ] Not started.

## Todo

- [ ] Decide backend serving model: node registry in one process or one process per participant.
- [ ] If using a registry, design `/node/start`, `/node/stop`, `/status`, and `/nodes` around multiple local nodes.
- [ ] If using one process per participant, document local multi-node testing commands and UI limitations.
- [ ] Add runnable-model status: supported by registry plus complete compatible route coverage.
- [ ] Expose missing layer ranges and wrong-model route reasons before inference starts.
- [ ] Define model-aware incentive accounting fields: model, layer range, tokens/requests served, latency, success/failure, hardware class, and identity.
- [ ] Decide whether initial rewards are simulated accounting only before real token integration.
- [ ] Add tests for model-runnable state and multi-node route readiness.

## Done

- [ ] None yet.

---

## Acceptance Criteria

- [ ] Multi-node local testing strategy is explicit and testable.
- [ ] The app can explain why a model is or is not currently runnable.
- [ ] Inference is gated by complete compatible route coverage, not only by model registry membership.
- [ ] Incentive semantics are model-aware and contribution-aware.
- [ ] No token UI is added before route correctness and accounting fields are validated.
- [ ] Backend tests cover the chosen model/routing semantics.

---

## Session Log

### 2026-07-06 - Create routing, model access, and incentives sprint

- What changed: created Sprint 06 to hold structural routing, model access, and incentive decisions split out from Sprint 04.
- Why: these questions affect backend contracts and reward design and should not be mixed into the local validation or UI-control sprint.
- Status: sprint is planned but not started.
