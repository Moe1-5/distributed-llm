# Sprint 04 - Local System Validation

**Goal:** Prove the core distributed inference flow works locally before adding real public bootstrap nodes, fault tolerance, monitoring, API keys, incentives, or distributed training/resource features.
**Start:** 2026-07-03
**End:** TBD

---

## Problem Summary

The backend regression suite now covers route validation, DHT metadata hardening, architecture-aware preprocessing checks, and optimized-Python validation. The next risk is system-level: the code still needs to prove that a local bootstrap node, serving node, generator, and frontend/API can work together end to end.

Sprint 04 should be a find-and-report validation sprint first. Do not add advanced product features until the local distributed inference path has been tested and any blocking failures are recorded.

---

## 2026-07-05 User Review Findings To Validate

The screenshots and review notes exposed workflow and product issues that should be documented before implementation continues:

- [x] Inference output is readable but unrelated to the prompt; validate whether this is an architecture/parity bug, a base-model limitation, prompt formatting, or stale/mock generator state.
- [x] The frontend can show a connected state while streaming reports `WebSocket not connected`; validate the WebSocket lifecycle before changing UI controls.
- [x] The backend/UI currently behaves as if only one serving node can run from the local backend; decide whether Sprint 04 must prove multiple local layer slices from one backend process or document that multiple processes are required for now.
- [x] Stop node control belongs in the primary node/client workflow, not hidden only inside a setup tab.
- [x] Bootstrap setup is infrastructure and should not be exposed as a normal client tab; the user workflow should show serving, inference, nodes, settings, and monitoring.
- [x] Inference needs a visible stop/cancel control for active generation.
- [x] Add a fourth main navigation page for monitoring the network graph/status view described in the product direction.
- [x] Decide and document model access semantics: users should be able to inference only models with complete compatible layer coverage in the network, not arbitrary unsupported models.
- [x] Decide incentive accounting semantics: rewards should be model-aware and contribution-aware because serving cost differs by model, layer range, hardware, reliability, and latency.

## User Review Finding Disposition

| Finding | Sprint 04 result | Owner |
| --- | --- | --- |
| Unrelated prompt output | Reproduced during local single-node inference; route executes, but answer quality is not trusted until direct-vs-distributed parity is checked. | Sprint 07 |
| WebSocket connected/send mismatch | Fixed in the frontend client by waiting for `onopen` and queueing sends while connecting. | Sprint 04 |
| One backend can serve only one layer slice | Recorded as a current limitation; local multi-node split validation is blocked until the serving strategy is chosen. | Sprint 06 |
| Stop local node control placement | Deferred after stop backend behavior was made bounded; primary Nodes-page placement belongs with client workflow cleanup. | Sprint 05 |
| Bootstrap exposed as client workflow | Deferred to client workflow cleanup; bootstrap remains infrastructure, not a normal user tab. | Sprint 05 |
| Active inference cancellation | Fixed with backend generator stop handling and an Inference page stop button. | Sprint 04 |
| Monitoring navigation page | Deferred to the monitoring/client workflow sprint. | Sprint 05 |
| Runnable model semantics | Deferred to routing/model-access work; model access should require supported registry entry plus complete compatible coverage. | Sprint 06 |
| Incentive accounting semantics | Deferred to incentive design; accounting should be model-aware and contribution-aware. | Sprint 06 |

## In Progress

- [x] Run the local one-machine system smoke test.

## Todo

- [x] Confirm the frontend/backend URL is correct for local testing or document the required override.
- [x] Start a local bootstrap node and record the printed multiaddress.
- [x] Start the backend API and verify `/status` and `/models`.
- [x] Start one local serving node for `facebook/opt-125m` with layers `0-12`.
- [x] Verify the node announces to the expected DHT prefix.
- [x] Verify `/nodes` shows the serving node.
- [x] Start the generator with the same model and DHT prefix.
- [x] Send a short prompt and record whether inference returns without crashing.
- [x] Record any failure in `ISSUES.md` with logs and reproduction steps.
- [x] If single-node full-layer inference works, attempt a local multi-node split route such as `0-4`, `4-8`, `8-12`.
- [x] Update `docs/VALIDATION_AND_TEST_PLAN.md` with pass/fail status for the tested phases.
- [x] Document whether the current UI should be changed now or after the smoke test for: hidden bootstrap, monitoring page, node stop placement, inference cancel, and multi-node serving.

## Done

- [x] Sprint 03 routing and DHT hardening was closed and archived.
- [x] Validation phase gates were documented in `docs/VALIDATION_AND_TEST_PLAN.md`.
- [x] Sprint 01 and Sprint 02 archive copies were confirmed identical to their duplicate `tasks/sprints/` copies before closure cleanup.
- [x] Sprint 05 and Sprint 06 were created to keep client workflow and incentive/model-access work out of the local validation sprint.
- [x] WebSocket first-send lifecycle was fixed so connecting sockets queue the prompt instead of reporting `WebSocket not connected`.
- [x] Active inference cancellation now has a backend stop signal and an Inference page stop button.
- [x] Backend regression tests pass after the Sprint 04 fixes.
- [x] Frontend typecheck passes after the Sprint 04 fixes.
- [x] Model output sanity check was run against local single-node distributed OPT-125M inference.
- [x] Node stop hang was mitigated with bounded RPC/DHT shutdown and live `/node/stop` verification.

---

## Acceptance Criteria

- [x] Backend regression tests still pass.
- [x] Local bootstrap can start.
- [x] Backend API can start and report status.
- [x] A local serving node can load all OPT-125M layers.
- [x] The generator can discover and validate the local full-layer route.
- [x] A short inference request either succeeds or fails with a documented actionable issue.
- [x] Validation documentation is updated with the result.
- [x] User-review workflow findings are either fixed or recorded as deferred issues with owners.
- [x] Advanced features remain deferred until local system validation is understood.

---

## Session Log

### 2026-07-03 - Create local system validation sprint

- What changed: created Sprint 04 as the active sprint for local end-to-end validation and closed the duplicate Sprint 01/Sprint 02 active-sequence files in favor of their archive copies.
- Why: the project needs proof that local distributed inference works before moving to real bootstrap nodes, public-swarm behavior, incentives, API keys, monitoring, or distributed training resources.
- Status: sprint plan is ready; local system testing has not started.

### 2026-07-05 - Document user review findings before implementation

- What changed: added the screenshot/user-review findings for unrelated inference output, WebSocket connection state, single-node local serving limits, stop controls, hidden bootstrap infrastructure, inference cancellation, monitoring navigation, model access semantics, and incentive accounting semantics.
- Why: the system feedback pointed to workflow and architecture decisions that need to be planned before more code changes are made.
- Status: planning is updated; no validation pass has been run yet and implementation is intentionally paused pending the plan.

### 2026-07-06 - Split follow-on sprints and fix stream control basics

- What changed: created Sprint 05 for client workflow controls and Sprint 06 for routing/model-access/incentive semantics; updated active sprint routing, project index, and current-state summary; added generator status and stop endpoints; added generator stop handling; fixed frontend WebSocket first-send lifecycle; added an Inference page stop button; added backend tests for generator status and cancellation.
- Why: Sprint 04 was overloaded with validation, UI workflow, monitoring, and incentive work. The immediate validation blocker was the misleading `WebSocket not connected` behavior and lack of active inference cancellation.
- Status: backend regression tests pass with 24 tests; frontend typecheck passes. Local bootstrap/node/generator smoke testing is still open.

### 2026-07-06 - Run local one-machine smoke test

- What changed: ran Sprint 04 static validation and local smoke validation; recorded results in `docs/VALIDATION_AND_TEST_PLAN.md` and `ISSUES.md`.
- Why: Sprint 04 needs proof that local bootstrap, API, serving node, generator, route readiness, and inference can work together before moving to advanced features.
- Status: static validation passed; local single-node full-layer OPT-125M inference passed through route `12D3KooW… (layers 0→12)`. Multi-node split testing is blocked by the one-node-per-backend limitation, and node/API shutdown can hang after serving starts.

### 2026-07-06 - Run model output sanity check

- What changed: ran several short `/chat` prompts through the local single-node distributed OPT-125M route and recorded the outputs in `ISSUES.md` and `docs/VALIDATION_AND_TEST_PLAN.md`.
- Why: the user asked to sanity-check whether the model output is related to the prompt after the route was proven to execute.
- Status: execution still works, but factual/arithmetic output quality fails sanity checks. Next correctness gate is a local HuggingFace-vs-distributed parity check rather than relying on generated text quality.

### 2026-07-06 - Create output parity follow-on sprint

- What changed: created Sprint 07 for HuggingFace-direct versus distributed-output parity and added the baseline prompt set for user testing.
- Why: the user is going to run `facebook/opt-125m` directly through HuggingFace so the distributed output can be compared against a direct baseline.
- Status: Sprint 07 is planned; Sprint 04 remains the current active sprint.

### 2026-07-06 - Mitigate node stop shutdown hang

- What changed: added bounded shutdown wrappers for Hivemind RPC server and DHT shutdown; updated `Node.stop()` to use bounded cleanup; added tests for stuck RPC and DHT shutdown.
- Why: Sprint 04 live validation found `/node/stop` and API shutdown could hang after a serving node starts.
- Status: backend tests pass with 26 tests and backend compileall passes. Live `/node/stop` now returns `{"status":"stopped"}` in about five seconds, but Hivemind worker processes can still remain after shutdown and are recorded as a follow-up issue.

### 2026-07-06 - Assign user-review findings to owner sprints

- What changed: added a Sprint 04 disposition table for each user-review workflow finding and marked the remaining validation acceptance criteria complete by documenting fixed items or explicit follow-on owners.
- Why: Sprint 04 should end with local system validation understood, while UI workflow, multi-node strategy, model-access semantics, incentives, and output parity stay out of the validation sprint.
- Status: Sprint 04 is ready for user review or explicit closure. Follow-on work is owned by Sprint 05, Sprint 06, and Sprint 07.
