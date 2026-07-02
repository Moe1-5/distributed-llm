# Sprint 02 - Architecture Adapter and Parity

**Goal:** Make the distributed inference path architecture-aware and compare it against Hugging Face parity before trusting generation quality.
**Start:** 2026-07-02  
**End:** 2026-07-02

---

## In Progress

- [x] Initialize generation position IDs before the first hidden-state preparation step.
- [x] Add broader architecture-aware preprocessing coverage for OPT and Llama-style models.
- [x] Add parity-focused validation against Hugging Face-style hidden-state behavior.
- [x] Strengthen runtime validation around generation readiness and adapter selection.

## Todo

- [ ] Extend the adapter abstraction to cover additional model families and preprocessing edge cases.
- [x] Add a parity regression test that compares local preprocessing output to a reference path.
- [x] Surface clearer adapter and parity diagnostics in the generation stack.

## Done

- [x] Confirm the first sprint 2 blocker: generation no longer fails when position IDs are first introduced.
- [x] Add a regression test for first-step generation with architecture-aware input preparation.
- [x] Add regression coverage for node-side causal masks and Llama/Mistral rotary embeddings.

---

## Session Log

### 2026-07-02 - Initialize generation position IDs for the sprint 2 path

- What changed: initialized the generation loop’s position IDs before the first hidden-state call and added a regression test covering that path.
- Why: sprint 2 parity work exposed a first-step generation failure caused by referencing position IDs before assignment.
- Status: the initial blocker is fixed and verified by backend regression tests.

### 2026-07-02 - Refresh position IDs across multi-step decoding

- What changed: recomputed position IDs on every generation step so the hidden-state preparation path stays aligned with the expanding prompt and generated context.
- Why: the parity-focused regression test showed that the second decoding step still used stale position IDs for the longer sequence.
- Status: multi-step decoding now uses the correct sequence length and the regression test passes.

### 2026-07-02 - Prepare layer-ready masks and rotary embeddings

- What changed: normalized node-side token masks into causal decoder masks, added Llama/Mistral rotary position embedding preparation for raw decoder-layer calls, and added regression coverage for both behaviors.
- Why: sprint 2 review found that raw transformer layers need architecture-specific inputs beyond simple hidden states; OPT-style layers expect a four-dimensional causal mask and Llama-style layers need rotary embeddings.
- Status: backend generation readiness tests pass with expanded coverage.

### 2026-07-02 - Add OPT preprocessing parity regression

- What changed: updated the architecture adapter API to accept attention masks, corrected OPT preprocessing to use the real Hugging Face decoder embedding path, and added a tiny local OPT parity regression test.
- Why: the previous OPT adapter could pass dummy tests while calling `embed_positions` with the wrong argument shape/order for real Transformers OPT modules.
- Status: parity coverage now catches the OPT embedding path and backend generation readiness tests pass.

### 2026-07-02 - Strengthen adapter readiness validation

- What changed: made unsupported adapter selection fail with an explicit architecture error, logged the selected adapter during generator load, added generator readiness checks for missing local components and hidden-size mismatches, and covered those paths with regression tests.
- Why: generation startup should fail before streaming begins when architecture support or local model components are not ready.
- Status: runtime adapter diagnostics are clearer and backend generation readiness tests pass.
