# Sprint 09 - Node Lifecycle, Token Validation, and Trace Analysis

**Goal:** Make serving-node lifecycle semantics explicit and safe, support multiple local served nodes/models through a backend registry, validate HuggingFace access before gated model startup, and analyze saved generation traces for route/output discrepancies.
**Start:** 2026-07-07
**End:** TBD

---

## Problem Summary

Sprint 08 cleaned up the client workflow, exposed token-level generation traces, and added same-model local multi-node serving for non-overlapping layer slices, but the backend still has prototype lifecycle limits:

1. Duplicate local replicas for the same model/layer range are not yet supported or load-balanced.
2. Multi-model local serving and multi-model inference routing are not yet supported from one backend process.
3. HuggingFace token handling checks local format and presence, but it does not yet validate token permission/license access against the selected gated model before startup.
4. Trace JSON artifacts exist, but there is not yet a structured trace-analysis pass that compares prompt tokens, selected tokens, top candidates, decoded text, route shapes, and replacement-character flags across runs.
5. Hivemind RPC shutdown can exceed the current five-second bounded cleanup window, leaving Python to wait on background threads or multiprocessing children when the user presses Ctrl-C repeatedly during backend exit.

---

## In Progress

- [x] Start Sprint 09 as the current active sprint.

## Todo

- [x] Add duplicate-replica semantics for same-model/same-layer local nodes, including unique RPC UIDs and routing/load-balancing rules.
- [x] Add multi-model local serving and inference routing, or document the chosen multi-process strategy clearly in the UI.
- [x] Harden pause/offline, resume/online, and unload/delete lifecycle behavior for duplicate and multi-model nodes.
- [x] Make unload/delete explicitly release layers, RPC, DHT, and accounting state for the targeted local node.
- [x] Harden Hivemind RPC/DHT shutdown so backend Ctrl-C exits cleanly, or escalates to clearly bounded forced cleanup without lingering Python thread/process waits.
- [x] Add a regression test or shutdown harness that simulates a stuck RPC server/runtime and verifies the backend lifecycle returns promptly with explicit status.
- [x] Update `/status`, `/nodes`, `/node/start`, and node-management endpoints for duplicate replicas and multi-model local nodes.
- [x] Update frontend Nodes and Network pages to support duplicate replicas and multiple served models.
- [x] Add backend tests for duplicate-replica routing, multi-model serving, and lifecycle cleanup.
- [x] Add a HuggingFace token validation endpoint that verifies token identity/access without exposing the token.
- [x] Validate gated model access before node/generator startup and surface license/access failures clearly.
- [x] Replace free-form token warnings with a guided token modal workflow wherever gated access is needed.
- [x] Create a trace-analysis tool or endpoint for saved `backend/traces/*.json` artifacts.
- [x] Analyze existing OPT-1.3B trace artifacts for prompt-token, selected-token, top-candidate, decoded-output, replacement-character, and route-shape discrepancies.
- [x] Document findings and decide whether any discrepancy belongs to model quality, sampling config, decoding/rendering, or distributed route behavior.

## Done

- [x] Sprint 09 started after Sprint 08 was closed and archived.
- [x] Backend validates HuggingFace token identity and gated model access before node/generator startup.
- [x] Network token modal validates the entered token against the selected gated model before saving.
- [x] Backend summarizes saved generation trace artifacts and reports cross-run, replacement-character, top-candidate, decoded-output, and route-shape discrepancy categories.
- [x] Existing OPT-1.3B traces were analyzed: 3 artifacts parsed cleanly; no cross-run prompt/config groups were comparable; no replacement-character traces were found; one sampled trace selected a token outside the top five candidates, which is categorized as sampling behavior rather than a distributed-route discrepancy.
- [x] Backend can register duplicate same-model/same-layer local replicas with unique RPC UIDs and route exact replicas with round-robin selection.
- [x] Backend can register multiple local served models in one process while generator routing remains model-scoped.
- [x] Pause/offline now releases RPC and DHT serving handles without unloading layers, resume reuses loaded handlers, delete unloads and unregisters only the targeted node, and backend shutdown has bounded local-node and client-DHT cleanup statuses.

---

## Acceptance Criteria

- [x] Users can pause a local served node without unloading layers.
- [x] Users can resume a paused local served node without reloading model weights.
- [x] Users can explicitly unload/delete a local served node and release layers.
- [x] Backend shutdown after a served node does not hang indefinitely or require repeated Ctrl-C interrupts.
- [x] One backend process can manage duplicate replicas and multiple local served models, or the UI clearly exposes the chosen multi-process strategy without pretending otherwise.
- [x] Gated model startup validates HuggingFace token access before expensive model loading starts.
- [x] Token storage remains local, gitignored, and never exposed in API responses.
- [x] Saved trace files can be summarized into actionable discrepancy categories.
- [x] Trace analysis results are documented before real incentives or broader multi-node validation rely on generated-output quality.

---

## Session Log

### 2026-07-06 - Create lifecycle and trace-analysis sprint

- What changed: created Sprint 09 for non-destructive node pause/resume/delete lifecycle, multi-node local serving, HuggingFace token validation, and saved trace analysis.
- Why: the user clarified that stop must not unload layers, delete/unload must be separate, the client should serve multiple models/nodes, gated model access needs a proper token workflow, and trace artifacts should be analyzed outside the Sprint 08 UI pass.
- Status: sprint is planned but not started.

### 2026-07-06 - Add backend shutdown hardening issue

- What changed: added a Sprint 09 lifecycle issue for Hivemind RPC shutdown exceeding the bounded five-second cleanup window and leaving Python waiting on background threads or multiprocessing children during repeated Ctrl-C exit.
- Why: the shutdown log shows `rpc-server-shutdown` timing out, followed by Python interpreter shutdown being interrupted while joining threading/multiprocessing state; this belongs with node lifecycle cleanup rather than Sprint 08 frontend refinements.
- Status: documented as planned Sprint 09 work; no code change or test run was made for this issue triage.

### 2026-07-06 - Refocus future node serving scope

- What changed: updated Sprint 09 to treat duplicate local replicas and multi-model local serving/routing as future work, now that Sprint 08 owns same-model non-overlapping local nodes.
- Why: the user asked to complete the same-model multi-node turn-off flow now while leaving duplicate model hosting and multi-model inference for later.
- Status: Sprint 09 remains planned for replica routing, multi-model serving, token validation, trace analysis, and shutdown hardening.

### 2026-07-07 - Start Sprint 09

- What changed: made Sprint 09 the current active sprint after archiving Sprint 08.
- Why: Sprint 08 had no open checklist items, and the user asked to move on to Sprint 09.
- Status: Sprint 09 is active; implementation work has not begun yet.

### 2026-07-07 - Add HuggingFace token access validation

- What changed: added `/settings/token/validate` for non-secret HuggingFace identity/model access checks; gated node and generator startup now validates token access before constructing loaders; the Network token modal validates entered tokens against the selected gated model before saving; backend tests cover missing-token, validated-access, and startup-denial cases.
- Why: gated model startup should fail quickly with clear license/access errors instead of beginning expensive model loading and failing later.
- Status: backend tests pass with 54 tests and frontend typecheck passes. Duplicate replicas, multi-model serving, shutdown hardening, and trace analysis remain open.

### 2026-07-09 - Add saved trace artifact analysis

- What changed: added backend trace-analysis helpers and `GET /generator/traces/analysis` for saved trace JSON artifacts; added frontend API typing for the analysis response; added backend regression tests for grouping, filtering, replacement-character flags, selected-token/top-candidate differences, decoded-output differences, and route-shape differences.
- Why: Sprint 09 needs trace artifacts to produce actionable discrepancy categories before generated-output quality is used for broader validation or incentives.
- Status: backend tests pass with 56 tests and frontend typecheck passes. Existing OPT-1.3B trace analysis found 3 cleanly parsed artifacts, no comparable cross-run groups, no replacement-character traces, and one sampled trace with a selected token outside the top five candidates. Duplicate replicas, multi-model serving, lifecycle cleanup, and shutdown hardening remain open.

### 2026-07-09 - Add duplicate replica and multi-model local serving registry

- What changed: added optional numeric RPC UID suffixes for duplicate local replicas; updated route planning to collapse exact same-layer replicas into one hop with round-robin selection; allowed `/node/start` to create same-model same-range replicas and same-prefix multi-model local nodes; kept partial same-model layer overlaps rejected; updated `/nodes` discovery to show all models instead of filtering to one active model; added backend regression tests for duplicate route load balancing, replica UID uniqueness, duplicate local startup, and multi-model local startup.
- Why: Sprint 09 requires one backend process to manage duplicate replicas and multiple local served models without pretending a duplicate range is a single already-running node or letting unsafe partial overlaps into a route.
- Status: backend tests pass with 57 tests and frontend typecheck passes. Pause/resume/delete cleanup hardening and backend shutdown hardening remain open.

### 2026-07-09 - Complete lifecycle and shutdown acceptance criteria

- What changed: hardened node pause so it announces offline, stops RPC, shuts down DHT with a timeout, and keeps loaded layers; verified resume reuses the loaded handler without reloading weights; moved turn-off/delete endpoint work into an executor; added bounded backend cleanup helpers for local nodes and generator client DHT; added regression tests for pause preservation, resume reuse, targeted delete/unregister behavior, stuck local-node shutdown, and stuck client-DHT shutdown.
- Why: the Sprint 09 acceptance checklist still needed proof that pause/resume/delete and backend shutdown semantics were safe for duplicate and multi-model local nodes.
- Status: backend tests pass with 61 tests and frontend typecheck passes. Sprint 09 acceptance criteria are now checked; the sprint can be reviewed for closure when the user is ready.

### 2026-07-09 - Audit duplicate replica completion

- What changed: reviewed Sprint 09 completion for checklist-only risk; fixed duplicate replica RPC UID suffix allocation so a later same-range replica does not reuse a live suffix after another replica was deleted; added a regression test for non-contiguous live replica suffixes; added an active lesson to audit completed checklist items for edge cases.
- Why: the user asked whether the sprint was genuinely complete or merely checked off, and the review found a real suffix-collision edge case.
- Status: backend tests pass with 62 tests and frontend typecheck passes. Sprint 09 still has no unchecked todo or acceptance items, and the found audit issue is fixed.

### 2026-07-09 - Reframe Sprint 10 gated model access plan

- What changed: changed Sprint 10 from real incentives to gated-model local import after external Hugging Face approval; moved real incentives and settlement to Sprint 11; updated active sprint routing, project index, current context, and lessons.
- Why: the token-first gated-repo path is not the desired product flow when approved users can provide local model files and DistribLLM can avoid owning Hugging Face credentials.
- Status: planning update complete. Sprint 09 remains checklist-complete; Sprint 10 is now planned around reliable local gated-model import.
