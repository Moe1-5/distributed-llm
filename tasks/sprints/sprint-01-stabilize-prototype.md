# Sprint 01 - Stabilize Prototype

**Goal:** Stabilize the current distributed inference prototype so future implementation work has clear routing, readiness, and validation targets.  
**Start:** 2026-06-30  
**End:** 2026-06-30

---

## In Progress

- [x] Adapt the Project-Starter workflow system to this repository.

## Todo

- [x] Add generator readiness validation before inference.
- [x] Add route planning that selects contiguous, non-overlapping layer spans.
- [ ] Add model architecture adapter path for supported models.
- [x] Add local split-path parity tests before trusting P2P inference.
- [ ] Add frontend readiness/error states around generator startup and streaming.

## Done

- [x] Document current architecture, runtime flows, implementation plan, Petals comparison, error/debugging notes, and validation plan.
- [x] Add generation-time logging and assertions around tokenization, hidden-state shapes, and sampling.
- [x] Add route validation and contiguous-span planning to avoid broken distributed routes.
- [x] Add OPT-style positional embedding preparation before remote layer execution.
- [x] Add regression tests covering route validation and hidden-state preparation.

---

## Session Log

> Append one dated entry per implementation session.
> Newest entries at the bottom.

### 2026-06-30 - Install project operating system

- What changed: added root assistant/workflow files, current-state context, task tracking, sprint log hooks, docs routing, ADR log, and environment template.
- Why: the Project-Starter system needed to be copied into this project and adapted to the existing DistribLLM folder structure and documentation.
- Status: workflow system is installed; inference stabilization tasks remain open.

### 2026-06-30 - Add generation readiness checks and route validation

- What changed: added regression tests for route validation, implemented contiguous-route planning and explicit validation in the remote sequential client, and added generation-time logging/assertions around tokenization, hidden-state shapes, and sampled token outputs.
- Why: the prototype needed stronger guardrails to catch incorrect routing, incomplete coverage, and shape mismatches before they silently produced gibberish output.
- Status: route validation and debug assertions are now in place; next step is to compare them against a known-good Hugging Face reference run.

### 2026-06-30 - Record OPTModel attribute investigation

- What changed: captured an investigation note about the OPTModel attribute error encountered while inspecting the Hugging Face OPT architecture, specifically around the model's embedding and decoder access patterns.
- Why: this issue is relevant to the distributed path because the current local/remote split must mirror the real OPT decoder structure more closely.
- Status: noted for follow-up in Sprint 2; currently out of scope for the immediate guardrail work but important for the parity fix.

### 2026-07-01 - Start Sprint 2 architecture adapter work

- What changed: added a reusable architecture adapter module with OPT and Llama-style preprocessing paths, wired it into the generation stack, and added a regression test covering OPT-style positional embedding preparation.
- Why: sprint 2 requires architecture-aware preprocessing before distributed inference can be trusted to match Hugging Face behavior.
- Status: adapter abstraction is in place and verified by backend tests; parity validation and deeper adapter work remain open.

### 2026-07-02 - Initialize generation position IDs for sprint 2 parity path

- What changed: initialized the generation loop’s position IDs before the first hidden-state preparation call and added a regression test covering that first-step path.
- Why: the sprint 2 architecture-aware preprocessing flow was failing on the first generation step because position IDs were referenced before assignment.
- Status: the first sprint 2 blocker is fixed and verified by backend regression tests.

---

When done: move this file to `tasks/archive/sprint-01-stabilize-prototype.md`, remove it from `tasks/active.md`, and update `.context/current.md`.
