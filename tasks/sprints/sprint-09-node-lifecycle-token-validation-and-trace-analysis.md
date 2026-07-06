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

- [ ] Add duplicate-replica semantics for same-model/same-layer local nodes, including unique RPC UIDs and routing/load-balancing rules.
- [ ] Add multi-model local serving and inference routing, or document the chosen multi-process strategy clearly in the UI.
- [ ] Harden pause/offline, resume/online, and unload/delete lifecycle behavior for duplicate and multi-model nodes.
- [ ] Make unload/delete explicitly release layers, RPC, DHT, and accounting state for the targeted local node.
- [ ] Harden Hivemind RPC/DHT shutdown so backend Ctrl-C exits cleanly, or escalates to clearly bounded forced cleanup without lingering Python thread/process waits.
- [ ] Add a regression test or shutdown harness that simulates a stuck RPC server/runtime and verifies the backend lifecycle returns promptly with explicit status.
- [ ] Update `/status`, `/nodes`, `/node/start`, and node-management endpoints for duplicate replicas and multi-model local nodes.
- [ ] Update frontend Nodes and Network pages to support duplicate replicas and multiple served models.
- [ ] Add backend tests for duplicate-replica routing, multi-model serving, and lifecycle cleanup.
- [ ] Add a HuggingFace token validation endpoint that verifies token identity/access without exposing the token.
- [ ] Validate gated model access before node/generator startup and surface license/access failures clearly.
- [ ] Replace free-form token warnings with a guided token modal workflow wherever gated access is needed.
- [ ] Create a trace-analysis tool or endpoint for saved `backend/traces/*.json` artifacts.
- [ ] Analyze existing OPT-1.3B trace artifacts for prompt-token, selected-token, top-candidate, decoded-output, replacement-character, and route-shape discrepancies.
- [ ] Document findings and decide whether any discrepancy belongs to model quality, sampling config, decoding/rendering, or distributed route behavior.

## Done

- [x] Sprint 09 started after Sprint 08 was closed and archived.

---

## Acceptance Criteria

- [ ] Users can pause a local served node without unloading layers.
- [ ] Users can resume a paused local served node without reloading model weights.
- [ ] Users can explicitly unload/delete a local served node and release layers.
- [ ] Backend shutdown after a served node does not hang indefinitely or require repeated Ctrl-C interrupts.
- [ ] One backend process can manage duplicate replicas and multiple local served models, or the UI clearly exposes the chosen multi-process strategy without pretending otherwise.
- [ ] Gated model startup validates HuggingFace token access before expensive model loading starts.
- [ ] Token storage remains local, gitignored, and never exposed in API responses.
- [ ] Saved trace files can be summarized into actionable discrepancy categories.
- [ ] Trace analysis results are documented before real incentives or broader multi-node validation rely on generated-output quality.

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
