# Sprint 18 - Memory-Efficient Selective Layer Loading

**Goal:** Load only the transformer layers and worker-side components needed for a served range so partial providers do not need enough CPU memory to construct the complete causal language model.
**Start:** 2026-08-13
**End:** TBD
**Status:** Proposed for user review; implementation has not started.

---

## Problem Summary

The worker loader currently calls `AutoModelForCausalLM.from_pretrained(...)` for the complete model and only then retains the requested layer slice. A participant serving `0-6` can therefore hit the same peak CPU-memory requirement as a participant loading every layer. This prevents otherwise capable devices from joining larger-model routes and is the clearest local engineering task that does not depend on the outstanding two-device relay acceptance run.

The generator still needs embeddings, architecture-specific input preparation, final normalization, projection, and the language-model head. This sprint changes worker loading only and must preserve the existing generator path.

## Dependencies and Boundaries

- Build on the existing model registry, local-import validation, and OPT/Llama-family architecture adapters.
- Use structured weight indexes and model configuration rather than guessing shard filenames.
- Prefer safetensors for the selective path; define explicit behavior for indexed PyTorch binary checkpoints.
- Preserve gated-model authorization and validated offline local-import behavior.
- Keep a controlled full-model fallback only for explicitly unsupported architectures or formats, and report when it is used.
- Do not add quantization, disk/CPU offloading, distributed training, or generator-side component changes in this sprint.

## Proposed Design

- Add a typed selective-load plan describing model revision, architecture, weight format, requested half-open range, required tensors, and source shards.
- Map architecture component names through the existing adapter boundary for OPT, Llama, and Mistral families.
- Read the safetensors index once, load only tensors for the requested decoder blocks and required worker-local state, and release transient shard buffers promptly.
- Validate missing, duplicate, mismatched, and out-of-range tensors before exposing an RPC handler.
- Expose diagnostics including loading strategy, selected range, loaded parameter count, estimated loaded bytes, fallback reason, and peak resident memory where measurable.
- Make failed starts and normal stops release modules, mapped files, temporary buffers, and CUDA allocations owned by the worker.

## Work Plan

- [ ] Review and approve the selective-loading contract before source changes.
- [ ] Record current full-loader startup time and peak resident memory for representative cached models.
- [ ] Implement config/index inspection and architecture-aware tensor-to-layer mapping.
- [ ] Implement selective safetensors loading for supported OPT and Llama/Mistral checkpoints.
- [ ] Define and test indexed PyTorch binary behavior or reject it with an actionable compatibility error.
- [ ] Integrate the selective path into node startup without changing generator loading semantics.
- [ ] Add loading diagnostics to node status and Monitoring without exposing local paths or secrets.
- [ ] Add deterministic cleanup for success, failure, cancellation, and node stop.
- [ ] Document supported formats, fallback behavior, memory interpretation, and troubleshooting.

## Test Plan

- Synthetic tiny sharded checkpoints prove that tensors outside the requested range are never materialized by the selective path.
- OPT and Llama-family layer outputs match the existing full-loader path within dtype-appropriate tolerance.
- First, middle, final, one-layer, and full-range boundaries load the exact expected blocks.
- Missing index entries, malformed tensor shapes, unsupported architectures, and unsupported formats fail before RPC advertisement.
- Gated local imports and offline cached startup retain their current authorization behavior.
- Repeated start/stop and failed-start tests detect retained worker references and unexpected memory growth.
- Backend regression tests, changed-file compilation, frontend type checks, and production build pass if status/UI contracts change.

## Acceptance Criteria

- [ ] A supported partial worker does not instantiate the complete `AutoModelForCausalLM` object.
- [ ] The loaded module set exactly matches the requested half-open range and architecture contract.
- [ ] Selective and legacy layer outputs are equivalent for supported representative checkpoints.
- [ ] Peak worker startup memory is materially below the full-loader baseline for a partial range, with measurements recorded.
- [ ] Generator embeddings and output-head behavior remain unchanged.
- [ ] Unsupported checkpoints either use an explicitly reported fallback or fail with a precise compatibility message.
- [ ] Node status makes the active loading strategy and memory evidence observable.

---

## Session Log

### 2026-08-13 - Create proposal for selective worker loading

- What changed: created a review-ready sprint plan for architecture-aware layer-slice loading, memory diagnostics, cleanup, parity, and format compatibility.
- Why: the current worker constructs the complete model before slicing, so partial serving does not reduce peak CPU-memory requirements.
- Status: proposal only. No loader source or runtime behavior changed; implementation awaits user approval.
