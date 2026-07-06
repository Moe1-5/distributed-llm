# Sprint 08 - Client Refinements and Generation Diagnostics

**Goal:** Make the client workflow feel like a P2P inference product rather than a backend control panel, and add token-level generation diagnostics for larger-model output sanity checks.
**Start:** 2026-07-06
**End:** 2026-07-07

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
8. Nodes must not show false offline status when RPC/layers are active.
9. The current destructive local node action must not be labeled as a non-destructive stop.
10. Serve Layers should keep the setup form visible instead of replacing it with a running-node prompt.
11. Gated model token entry should happen through a focused modal workflow from the model/start flow.
12. Node cards need two distinct actions: one to turn off serving/RPC while keeping loaded layers in memory, and one to delete/unload the node entirely.
13. The backend should allow multiple local served nodes for non-overlapping layer slices of the same model, so turning one node off does not remove other local nodes from the network.

---

## In Progress

- [x] Create Sprint 08 for client refinements and generation diagnostics.
- [x] Implement UI refinements.
- [x] Add token-level generation trace diagnostics.
- [x] Run a live `facebook/opt-1.3b` trace when a complete route is available.
- [x] Expose trace diagnostics from the Inference page.
- [x] Add a generator stop button to the Run Inference workflow.
- [x] Add corrected node/token UI semantics from user review.

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
- [x] Add a frontend trace action for the current prompt.
- [x] Add a Run Inference generator stop control.
- [x] Fix false offline display for DHT-discovered running nodes.
- [x] Relabel current local node stop action as unload/delete behavior.
- [x] Keep Serve Layers form visible when a local node is running.
- [x] Add a Network-page HuggingFace token modal for gated model startup.
- [x] Split the node-card action into a non-destructive Turn Off control and a destructive Delete Node control.
- [x] Preserve loaded serving layers when a node is turned off so turning it back on does not reload layers from scratch.
- [x] Support multiple local served nodes for non-overlapping slices of the same model in one backend process.
- [x] Make Dashboard node lifecycle actions target one local node without turning off/deleting other local nodes.

## Done

- [x] Real incentives sprint moved to Sprint 10.
- [x] Inference action button now transitions between stream open, send, and stop states.
- [x] Network page no longer exposes bootstrap peer textareas or setup-form stop-node control.
- [x] Monitoring page now presents a conceptual P2P node map, route chain, coverage, and serving providers.
- [x] Backend exposes `/generator/trace` for token-level output diagnostics.
- [x] `/generator/trace` writes timestamped JSON trace artifacts under `backend/traces/` and returns the trace id/path.
- [x] Live OPT-1.3B trace ran through a full local route without replacement-character corruption.
- [x] Inference page can run a short greedy trace and show trace id, response, first token, replacement-character status, trace file, and route providers.
- [x] Trace results include first-step top candidates and backend trace errors surface their detail text in the client.
- [x] Network Run Inference panel can stop a ready generator.
- [x] Nodes page no longer labels the destructive unload path as a non-destructive stop.
- [x] Network page uses a token modal instead of inline gated-model warning panels.
- [x] Sprint 09 created for real pause/resume/delete lifecycle, multi-node local serving, token validation, and trace analysis.
- [x] Dashboard node cards now separate Turn Off/Turn On serving from destructive Delete Node.
- [x] Turning off a local node stops RPC serving while preserving the loaded handler/layers and DHT identity for fast resume.
- [x] Same-model, non-overlapping local layer slices can be served as separate local nodes.
- [x] Turning off one local served node leaves other local served nodes online and discoverable.

---

## Acceptance Criteria

- [x] Inference action button transitions from stream-open to send to stop without a separate header button.
- [x] Network setup no longer exposes bootstrap peers in normal user flow.
- [x] Network setup does not present stop-node as if multiple local nodes can be managed from that form.
- [x] Monitoring page presents a node-map style network view with route and layer coverage context.
- [x] Token-level diagnostics can show where generated output becomes corrupted or drifts.
- [x] Frontend typecheck passes.
- [x] Backend tests pass for any generation diagnostics added.
- [x] Node cards expose separate turn-off and delete actions with clear destructive/non-destructive semantics.
- [x] Turning off a node stops network serving without unloading its already-loaded layers.
- [x] Starting a second non-overlapping local node is allowed for the same model.
- [x] Duplicate layer replicas and multi-model local serving remain deferred to later sprint work.

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

### 2026-07-06 - Add Inference trace action

- What changed: added a typed frontend `/generator/trace` API method and a compact Inference-page `TRACE` action that runs a short greedy token trace for the current prompt and appends the trace id, response, first token, replacement-character status, trace file, and route providers to the conversation.
- Why: Sprint 08 diagnostics should be accessible from the client workflow, not only as a backend endpoint.
- Status: frontend typecheck passes.

### 2026-07-06 - Expand trace result details

- What changed: extended the frontend trace result type with first-step top candidates, included traced step count and candidate logits in the Inference trace message, disabled prompt edits while tracing, and surfaced backend POST error details in the API client.
- Why: token-level diagnostics are more useful when the user can see whether the selected token was among plausible candidates and why a trace request failed.
- Status: frontend typecheck passes.

### 2026-07-06 - Add generator stop button

- What changed: added a Stop Generator button to the Network page's Run Inference ready state and wired it to `/generator/stop`, with activity-log feedback and local ready-state reset.
- Why: Sprint 08 is complete, but the user requested an explicit generator stop control in the client workflow.
- Status: frontend typecheck passes.

### 2026-07-06 - Correct node and token workflow semantics

- What changed: fixed DHT-discovered node metadata so running nodes with loaded layers and active RPC do not show false offline state; relabeled the current local node action as unload behavior; kept the Serve Layers form visible while a node is running; added a Network-page HuggingFace token modal; explicitly ignored local `.hf_token` files; created Sprint 09 for real pause/resume/delete lifecycle, multi-node local serving, token validation, and trace analysis.
- Why: the user clarified that stop should make a node offline without unloading layers, delete/unload must be separate, the client should eventually support multiple models/nodes, token entry should be modal, and trace analysis belongs in a later sprint.
- Status: focused backend tests pass with 40 tests and frontend typecheck passes.

### 2026-07-06 - Add node turn-off versus delete issue

- What changed: added an explicit Sprint 08 issue for two node-card actions: non-destructive Turn Off that stops serving/RPC but preserves loaded layers, and destructive Delete Node that unloads/removes the node entirely.
- Why: the local node card currently presents one unload-style action, but the desired workflow needs fast resume without reloading model layers and a separate whole-node delete path.
- Status: documented as an open Sprint 08 todo and acceptance criterion; implementation remains open.

### 2026-07-06 - Implement node turn-off and delete split

- What changed: added non-destructive node turn-off/turn-on lifecycle support that keeps loaded layers and DHT identity, added a destructive delete endpoint for full unload/removal, made route validation ignore offline nodes while still showing them in network status, and updated Dashboard node cards with Turn Off/Turn On plus Delete Node controls.
- Why: node operators need to temporarily stop serving without paying the model layer reload cost, while destructive deletion must remain a separate explicit action.
- Status: backend focused tests pass with 45 tests and frontend typecheck passes.

### 2026-07-06 - Verify Sprint 08 completion state

- What changed: reviewed Sprint 08 todos and acceptance criteria, then reran the focused backend lifecycle/generation diagnostics tests and frontend typecheck.
- Why: the user asked to continue Sprint 08 after the remaining node lifecycle UI item had been completed, so the sprint needed a completion-readiness verification pass rather than new scope.
- Status: all Sprint 08 todos and acceptance criteria are checked; backend focused tests pass with 45 tests and frontend typecheck passes. Sprint 08 is ready to close when the user explicitly asks to close it.

### 2026-07-06 - Fix turn-off closed DHT handle regression

- What changed: made local node turn-off stop re-announcing before shutdown, write explicit offline metadata, release closed RPC/DHT handles after Hivemind shutdown, keep loaded layers and cached peer/address status for `/status`, and let `/nodes` return the local loaded offline node when no active DHT remains.
- Why: the user hit `OSError: handle is closed` after Turn Off because Hivemind RPC shutdown closes the shared DHT, then the backend tried to announce and read status through that closed handle.
- Status: backend focused tests pass with 47 tests, frontend typecheck passes, and `git diff --check` passes.

### 2026-07-06 - Add same-model local multi-node scope

- What changed: added Sprint 08 work for serving multiple local same-model, non-overlapping layer slices as separate nodes, with node-card lifecycle actions targeting one local node at a time.
- Why: the user expects turning off one served node to remove only that node from serving while other local served nodes keep working; duplicate replicas and multi-model inference are different routing problems and remain later work.
- Status: scope added; implementation in progress.

### 2026-07-06 - Implement same-model local multi-node serving

- What changed: replaced the single local node slot with a local node registry, added stable node ids to local node status/metadata, allowed additional same-model non-overlapping layer slices to start as separate local nodes, kept overlapping duplicate replicas and multi-model local serving blocked with explicit errors, and made Dashboard lifecycle controls target the selected local node id.
- Why: turning off one served node should remove only that node from serving while other local nodes keep their RPC/layers online.
- Status: backend focused tests pass with 50 tests, frontend typecheck passes, and `git diff --check` passes.

### 2026-07-07 - Close Sprint 08

- What changed: archived Sprint 08 after confirming every todo and acceptance criterion was checked.
- Why: the user asked to close Sprint 08 if no checklist items remained and move on to Sprint 09.
- Status: Sprint 08 is closed and Sprint 09 is now the current active sprint.
