# Sprint 04 - Local System Validation

**Goal:** Prove the core distributed inference flow works locally before adding real public bootstrap nodes, fault tolerance, monitoring, API keys, incentives, or distributed training/resource features.
**Start:** 2026-07-03
**End:** TBD

---

## Problem Summary

The backend regression suite now covers route validation, DHT metadata hardening, architecture-aware preprocessing checks, and optimized-Python validation. The next risk is system-level: the code still needs to prove that a local bootstrap node, serving node, generator, and frontend/API can work together end to end.

Sprint 04 should be a find-and-report validation sprint first. Do not add advanced product features until the local distributed inference path has been tested and any blocking failures are recorded.

---

## In Progress

- [ ] Run the local one-machine system smoke test.

## Todo

- [ ] Confirm the frontend/backend URL is correct for local testing or document the required override.
- [ ] Start a local bootstrap node and record the printed multiaddress.
- [ ] Start the backend API and verify `/status` and `/models`.
- [ ] Start one local serving node for `facebook/opt-125m` with layers `0-12`.
- [ ] Verify the node announces to the expected DHT prefix.
- [ ] Verify `/nodes` shows the serving node.
- [ ] Start the generator with the same model and DHT prefix.
- [ ] Send a short prompt and record whether inference returns without crashing.
- [ ] Record any failure in `ISSUES.md` with logs and reproduction steps.
- [ ] If single-node full-layer inference works, attempt a local multi-node split route such as `0-4`, `4-8`, `8-12`.
- [ ] Update `docs/VALIDATION_AND_TEST_PLAN.md` with pass/fail status for the tested phases.

## Done

- [x] Sprint 03 routing and DHT hardening was closed and archived.
- [x] Validation phase gates were documented in `docs/VALIDATION_AND_TEST_PLAN.md`.
- [x] Sprint 01 and Sprint 02 archive copies were confirmed identical to their duplicate `tasks/sprints/` copies before closure cleanup.

---

## Acceptance Criteria

- [ ] Backend regression tests still pass.
- [ ] Local bootstrap can start.
- [ ] Backend API can start and report status.
- [ ] A local serving node can load all OPT-125M layers.
- [ ] The generator can discover and validate the local full-layer route.
- [ ] A short inference request either succeeds or fails with a documented actionable issue.
- [ ] Validation documentation is updated with the result.
- [ ] Advanced features remain deferred until local system validation is understood.

---

## Session Log

### 2026-07-03 - Create local system validation sprint

- What changed: created Sprint 04 as the active sprint for local end-to-end validation and closed the duplicate Sprint 01/Sprint 02 active-sequence files in favor of their archive copies.
- Why: the project needs proof that local distributed inference works before moving to real bootstrap nodes, public-swarm behavior, incentives, API keys, monitoring, or distributed training resources.
- Status: sprint plan is ready; local system testing has not started.
