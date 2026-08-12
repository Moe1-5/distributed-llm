# Sprint 18 - Memory-Efficient Selective Layer Loading

**Goal:** Load only the transformer layers and worker-side components needed for a served range so partial providers do not need enough CPU memory to construct the complete causal language model.
**Start:** 2026-08-13
**End:** TBD
**Status:** Implemented and locally verified on `feature/fundamental-live-stability`; retained as active until explicit sprint closure.

---

## Problem Summary

The previous worker loader called `AutoModelForCausalLM.from_pretrained(...)` for the complete model and only then retained the requested layer slice. A participant serving `0-6` could therefore hit the same peak CPU-memory requirement as a participant loading every layer.

The generator still needs embeddings, architecture-specific input preparation, final normalization, projection, and the language-model head. This sprint changes worker loading only and must preserve the existing generator path.

## Dependencies and Boundaries

- Build on the existing model registry, local-import validation, and OPT/Llama-family architecture adapters.
- Use structured weight indexes and model configuration rather than guessing shard filenames.
- Prefer safetensors for the selective path; define explicit behavior for indexed PyTorch binary checkpoints.
- Preserve gated-model authorization and validated offline local-import behavior.
- Keep a controlled full-model fallback only for explicitly unsupported architectures or formats, and report when it is used.
- Do not add quantization, disk/CPU offloading, distributed training, or generator-side component changes in this sprint.

## Implemented Design

- Add a typed selective-load plan describing model revision, architecture, weight format, requested half-open range, required tensors, and source shards.
- Map architecture component names through the existing adapter boundary for OPT, Llama, and Mistral families.
- Read the safetensors index once, load only tensors for the requested decoder blocks and required worker-local state, and release transient shard buffers promptly.
- Validate missing, duplicate, mismatched, and out-of-range tensors before exposing an RPC handler.
- Expose diagnostics including loading strategy, selected range, loaded parameter count, estimated loaded bytes, fallback reason, and peak resident memory where measurable.
- Make failed starts and normal stops release modules, mapped files, temporary buffers, and CUDA allocations owned by the worker.

## Work Plan

- [x] Review and approve the selective-loading contract before source changes.
- [x] Record current full-loader startup time and peak resident memory for representative cached models.
- [x] Implement config/index inspection and architecture-aware tensor-to-layer mapping.
- [x] Implement selective safetensors loading for supported OPT and Llama/Mistral checkpoints.
- [x] Define and test indexed PyTorch binary behavior or reject it with an actionable compatibility error.
- [x] Integrate the selective path into node startup without changing generator loading semantics.
- [x] Add loading diagnostics to node status and Monitoring without exposing local paths or secrets.
- [x] Add deterministic cleanup for success, failure, cancellation, and node stop.
- [x] Document supported formats, fallback behavior, memory interpretation, and troubleshooting.

## Test Plan

- Synthetic tiny sharded checkpoints prove that tensors outside the requested range are never materialized by the selective path.
- OPT and Llama-family layer outputs match the existing full-loader path within dtype-appropriate tolerance.
- First, middle, final, one-layer, and full-range boundaries load the exact expected blocks.
- Missing index entries, malformed tensor shapes, unsupported architectures, and unsupported formats fail before RPC advertisement.
- Gated local imports and offline cached startup retain their current authorization behavior.
- Repeated start/stop and failed-start tests detect retained worker references and unexpected memory growth.
- Backend regression tests, changed-file compilation, frontend type checks, and production build pass if status/UI contracts change.

## Acceptance Criteria

- [x] A supported partial worker does not instantiate the complete `AutoModelForCausalLM` object.
- [x] The loaded module set exactly matches the requested half-open range and architecture contract.
- [x] Selective and legacy layer outputs are equivalent for supported representative checkpoints.
- [x] Peak worker startup memory is materially below the full-loader baseline for a partial range, with measurements recorded.
- [x] Generator embeddings and output-head behavior remain unchanged.
- [x] Unsupported checkpoints either use an explicitly reported fallback or fail with a precise compatibility message.
- [x] Node status makes the active loading strategy and memory evidence observable.

---

## Session Log

### 2026-08-13 - Create proposal for selective worker loading

- What changed: created a review-ready sprint plan for architecture-aware layer-slice loading, memory diagnostics, cleanup, parity, and format compatibility.
- Why: the current worker constructs the complete model before slicing, so partial serving does not reduce peak CPU-memory requirements.
- Status: proposal only. No loader source or runtime behavior changed; implementation awaits user approval.

### 2026-08-13 - Implement and verify selective worker loading

- What changed: replaced complete worker model construction with typed OPT/Llama/Mistral safetensors plans, meta-device decoder blocks, selected-key materialization, strict index/shape checks, measured diagnostics, and deterministic unload behavior. Node status and DHT metadata expose sanitized loading evidence; Dashboard and Monitoring show selective versus fallback strategy.
- Compatibility: checkpoints with only PyTorch binary weights use a visible full-model fallback by default so existing OPT deployments continue to start. `DISTRIBLLM_ALLOW_FULL_MODEL_FALLBACK=false` converts that fallback into an actionable pre-start failure.
- Real evidence: cached TinyLlama layer `0-1` loaded as float32 in 0.28 seconds with 230,490,112 bytes of measured peak RSS growth. The prior full-model baseline took 4.38 seconds and grew by 4,734,320,640 bytes, about 20.5 times more memory.
- Verification: selective loading suite passes 9 tests plus 4 boundary subtests; real TinyLlama handler forward returned finite shape `[1, 4, 2048]`; cached OPT-125M binary fallback starts offline and reports its strategy; full backend passes 204 tests plus 26 subtests; frontend type checks and production build pass.
- Remaining: physical Windows/two-device acceptance belongs to the fundamental package run. This sprint remains active until the user explicitly requests closure.
