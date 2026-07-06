# Sprint 08 - Client Refinements and Generation Diagnostics

**Goal:** Make the client workflow feel like a P2P inference product rather than a backend control panel, and add token-level generation diagnostics for larger-model output sanity checks.
**Start:** 2026-07-06
**End:** TBD

---

## Problem Summary

Sprint 05 cleaned up the first client workflow pass, and Sprint 07 showed that single-node distributed parity can be checked with deterministic logits. The current Electron UI still exposes too much infrastructure and hides some controls in unintuitive places.

The user review on 2026-07-06 identified these refinements:

1. Opening the stream should happen from the message action area, then the send control should replace it.
2. Stopping active inference should be obvious in the same message action area.
3. Stop-node controls do not belong on the serving setup form while the prototype supports only one local node per backend process.
4. Bootstrap peer textareas should not be shown in normal client workflows.
5. Model dropdown options should not duplicate runnable/not-runnable labels when the status panel already explains route readiness.
6. Monitoring should show the P2P network shape and route coverage, not primarily backend health cards.
7. Larger model output sanity checks need token-level tracing so replacement characters and drift can be localized.

---

## In Progress

- [x] Create Sprint 08 for client refinements and generation diagnostics.
- [x] Implement UI refinements.
- [x] Add token-level generation trace diagnostics.
- [x] Run a live `facebook/opt-1.3b` trace when a complete route is available.

## Todo

- [x] Move stream-open control into the Inference input action button.
- [x] Make the active stop-inference control obvious.
- [x] Remove stop-node action from the Network serving setup form.
- [x] Hide bootstrap peer textareas from normal serving and generator setup.
- [x] Remove runnable/not-runnable text from model dropdown option labels.
- [x] Redesign Monitoring around a network map, route chain, coverage, and discovered serving nodes.
- [x] Add a backend generation trace endpoint that records prompt tokens, generated token ids/text, decoded output so far, and route trace.
- [x] Persist trace endpoint output to a JSON file for later inspection.
- [x] Use the trace endpoint to sanity-check `facebook/opt-1.3b` when a complete local route is available.

## Done

- [x] Real incentives sprint moved to Sprint 10.
- [x] Inference action button now transitions between stream open, send, and stop states.
- [x] Network page no longer exposes bootstrap peer textareas or setup-form stop-node control.
- [x] Monitoring page now presents a conceptual P2P node map, route chain, coverage, and serving providers.
- [x] Backend exposes `/generator/trace` for token-level output diagnostics.
- [x] `/generator/trace` writes timestamped JSON trace artifacts under `backend/traces/` and returns the trace id/path.
- [x] Live OPT-1.3B trace ran through a full local route without replacement-character corruption.

---

## Acceptance Criteria

- [x] Inference action button transitions from stream-open to send to stop without a separate header button.
- [x] Network setup no longer exposes bootstrap peers in normal user flow.
- [x] Network setup does not present stop-node as if multiple local nodes can be managed from that form.
- [x] Monitoring page presents a node-map style network view with route and layer coverage context.
- [x] Token-level diagnostics can show where generated output becomes corrupted or drifts.
- [x] Frontend typecheck passes.
- [x] Backend tests pass for any generation diagnostics added.

---

## Session Log

### 2026-07-06 - Create client refinements and diagnostics sprint

- What changed: created Sprint 08 for the user-requested client workflow refinements, monitoring redesign, and token-level generation diagnostics; moved real incentives and settlement to Sprint 10.
- Why: these refinements should be handled before real incentive work because the client workflow and output diagnostics are prerequisites for trusting useful work.
- Status: sprint is started; implementation is in progress.

### 2026-07-06 - Implement client refinements and trace diagnostics

- What changed: moved Inference stream-open/send/stop into one action button; removed bootstrap peer textareas and setup-form stop-node control from Network; simplified model dropdown labels; redesigned Monitoring as a P2P network map with coverage and route chain; added `/generator/trace` with prompt tokens, generated tokens, top candidates, decoded output so far, and route trace.
- Why: the client should not expose bootstrap infrastructure as normal workflow, and 1.3B output sanity needs token-level diagnostics to separate base-model continuation, token decoding, and route issues.
- Status: focused backend tests pass with 35 tests and frontend typecheck passes. Live `facebook/opt-1.3b` tracing remains open because checking/loading the model requires host cache/model access outside the current sandbox approval path.

### 2026-07-06 - Persist trace files and run OPT-1.3B trace

- What changed: expanded trace steps with tensor shapes, selected-token checks, and replacement-character flags; made `/generator/trace` write timestamped JSON files to `backend/traces/`; ignored runtime trace artifacts in git; documented trace interpretation and the live OPT-1.3B result.
- Why: bad generated text needs a durable input-to-output artifact so token selection, decoding, and rendering can be inspected after the run.
- Status: focused backend tests pass with 35 tests and backend compileall passes. A full-layer CUDA OPT-1.3B trace wrote trace id `facab212a2ef`; selected/decoded tokens had no replacement-character corruption. Normal sampled `/chat` can still produce poor text, so exact output parity remains a Sprint 07 generation-controls follow-up.

### 2026-07-06 - Compare bad-output diagnostics with Petals

- What changed: updated `docs/PETALS_COMPARISON.md` with a direct answer on whether Petals tackled the bad-output issue, separating distributed correctness, token decoding/rendering, and base-model sampled-output quality.
- Why: the user asked whether Petals had already solved the same problem after the OPT-1.3B trace showed route parity but sampled poor prose.
- Status: documented that Petals tackles local-equivalent distributed execution through mature routing, sessions, and cache recovery, but does not make weak/base sampled completions factual; the next DistribLLM follow-up remains exact generation controls and broader parity tests.

### 2026-07-06 - Document model quality policy

- What changed: added a model-quality policy to `docs/VALIDATION_AND_TEST_PLAN.md` explaining that better instruction-tuned models should be used for demos, while OPT-style base models remain smoke-test targets.
- Why: changing models can improve answer quality, but it cannot replace parity checks and trace diagnostics that prove the distributed path is correct.
- Status: documentation updated; no code or test run was needed for this doc-only clarification.

### 2026-07-06 - Start Sprint 08 as current sprint

- What changed: made Sprint 08 the lowest active sprint after closing and archiving Sprint 07.
- Why: the user asked to close Sprint 07 and start with Sprint 08.
- Status: Sprint 08 is now the current active sprint for any follow-up work.
