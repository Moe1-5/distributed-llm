# Sprint 11 - Instruction-Ready Model Expansion

**Goal:** Add more supported models that can produce useful instruction-following responses out of the box, without trying to fine-tune or prompt-hack a baseline/base model into instruction behavior.
**Start:** 2026-07-12
**End:** TBD

---

## Problem Summary

The current supported model list includes small/open base models that are useful for pipeline testing, plus gated larger models. Base models can complete text, but they often do not follow chat or instruction-style prompts reliably without instruction tuning. Trying to compensate with generation settings or custom prompt wrappers is fragile and can make distributed-output validation harder.

Sprint 11 should expand the model registry toward models that are already instruction-tuned or chat-tuned by their publishers, while preserving the project rules around explicit architecture support, layer counts, local import validation, and distributed parity.

The goal is not to fine-tune the current baseline model. The goal is to support better already-trained models with clear runtime contracts.

---

## Product Decision

- Prefer instruction-tuned or chat-tuned models for user-facing chat workflows.
- Keep small base models only as smoke-test and routing-test models unless their behavior is explicitly labeled as base completion.
- Do not add a fine-tuning workflow in this sprint.
- Do not hide base-model limitations behind prompt templates that pretend to be instruction tuning.
- Every new model must have explicit registry metadata, generation defaults, architecture adapter coverage, local import validation expectations, and a test/validation plan.
- Gated models added here must use the Sprint 10 local-import flow as the primary access path.

## In Progress

- [x] Started with a narrow registry expansion for open chat smoke testing and the already accepted Llama 2 gated family.
- [ ] Live inference smoke test pending.

## Todo

- [x] Define selection criteria for new supported models: instruction/chat tuning, license/access, model size, hardware needs, architecture support, and local import compatibility.
- [x] Pick a small open instruction-ready model for local smoke testing.
- [x] Pick one or more higher-quality instruction-ready models for gated/local-import testing.
- [x] Add model registry entries with layer count, hidden size, gated flag, VRAM estimate, description, and generation defaults.
- [x] Verify or add architecture adapter support for each selected model family.
- [x] Extend local model import validation expectations where a selected architecture needs extra config/tokenizer rules.
- [x] Update frontend labels so base models and instruction-ready models are clearly distinguished.
- [x] Add backend tests for model registry validation and local import contracts for the selected models.
- [ ] Add parity or smoke-test notes for each selected model before marking it user-facing.
- [x] Document that these are already instruction-tuned/chat-tuned models, not fine-tuned DistribLLM baseline models.

## Deferred To Later Sprint

- Training or fine-tuning any baseline model.
- LoRA adapter management.
- Automatic model download or cache management.
- Broad model marketplace/library features.
- Real incentives and settlement, now planned for Sprint 13.

## Done

- [x] Added `TinyLlama/TinyLlama-1.1B-Chat-v1.0` as an open chat-tuned smoke-test model.
- [x] Added `meta-llama/Llama-2-7b-hf`, `meta-llama/Llama-2-7b-chat-hf`, and `meta-llama/Llama-2-13b-chat-hf` for the accepted Llama 2 gated family.
- [x] Added explicit model tuning metadata: `base`, `chat`, or `instruct`.

---

## Acceptance Criteria

- [ ] At least one new instruction-ready model is added to the supported model registry.
- [ ] The selected model can be served and used for generation without custom fine-tuning.
- [ ] Model labels make clear whether a model is base, instruction-tuned, or chat-tuned.
- [ ] New model metadata includes layer count, hidden size, gated status, VRAM estimate, and generation defaults.
- [ ] Local import validation works for any gated model added in this sprint.
- [ ] Backend tests cover the new model registry and validation contract.
- [ ] Documentation explains that Sprint 11 adds already instruction-tuned models rather than fine-tuning a baseline model.

---

## Session Log

### 2026-07-09 - Plan instruction-ready model expansion

- What changed: created Sprint 11 for adding more supported instruction-ready/chat-ready models and explicitly avoiding baseline-model fine-tuning as the solution.
- Why: the project needs better model quality for user-facing chat than base completion models can reliably provide, while keeping model support explicit and testable.
- Status: sprint is planned but not started.

### 2026-07-12 - Start model registry expansion

- What changed: added TinyLlama chat as an open smoke-test option, added Llama 2 base/chat gated options that match the user's accepted Hugging Face family, exposed model tuning metadata through `/models`, updated Network dropdown labels, and added backend tests for registry metadata plus Llama-style local-import validation.
- Why: Sprint 10 live testing is blocked on pending Llama 3.2 access, so Sprint 11 needs better selectable models that can validate inference without trying to fine-tune a base model into instruction behavior.
- Status: implementation slice is ready for tests; live inference and gated Llama 2 download/import validation remain open.

### 2026-07-12 - Handle large-model CUDA OOM clearly

- What changed: added backend CUDA out-of-memory detection for node start, node turn-on, and generator start so the API returns `cuda_out_of_memory` with guidance to reduce served layers, free existing components, switch to CPU, or use a smaller model.
- Why: testing larger Sprint 11 model options can exceed local GPU memory, and the raw CUDA runtime error is too noisy for the Network activity log.
- Status: backend tests pass with 89 tests, frontend typecheck passes, and Python syntax compile passes; remote GPU testing through a temporary Colab node remains a candidate smoke-test path.

### 2026-07-12 - Delete managed downloaded model files

- What changed: changed local model removal so `delete_files=true` deletes the exact managed Hugging Face cache revision when the imported path is a downloaded cache snapshot, keeps arbitrary manually imported folders safe, and updates the Network imported-model action to confirm and show whether disk space was freed.
- Why: downloaded gated models can remain on disk after the registry entry is removed, which is confusing and expensive for multi-gigabyte models.
- Status: backend tests pass with 91 tests, frontend typecheck passes, and Python syntax compile passes; manual folders are intentionally unregistered but not recursively deleted.

### 2026-07-12 - Configure a public bootstrap peer for remote model testing

- What changed: added runtime parsing for comma- or newline-separated `DISTRIBLLM_INITIAL_PEERS`, switched node, generator, and status defaults to resolve the environment dynamically, documented the public-peer requirement, and configured the local root environment to use the VPS bootstrap at `178.156.212.0:7001`.
- Why: laptop and Colab workers cannot use a VPS loopback multiaddress; they need the VPS public multiaddress and stable peer ID to discover the same private DistribLLM swarm.
- Status: 93 backend tests pass, Python syntax compilation passes, and frontend typecheck passes; a live Colab worker connection and remote inference smoke test remain open.

### 2026-07-12 - Add a headless Colab worker entry point

- What changed: added `backend/colab_worker.py` with explicit model, layer range, bootstrap peer, DHT prefix, device, and stored OAuth token support for running a serving node without the Electron interface.
- Why: Colab needs a stable command-line worker that joins the same VPS-backed swarm and serves the complementary Llama 2 layer range.
- Status: implementation is ready for branch publication and live Colab validation; Llama 2 still requires a high-RAM runtime because the current loader constructs the complete model before retaining assigned layers.
