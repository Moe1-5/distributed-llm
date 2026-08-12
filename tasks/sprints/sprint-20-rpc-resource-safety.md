# Sprint 20 - RPC Resource Safety

**Goal:** Enforce explicit tensor, metadata, concurrency, timeout, and admission limits at the worker RPC boundary so public-swarm traffic cannot consume unbounded compute or memory.
**Start:** 2026-08-13
**End:** TBD
**Status:** Proposed for user review; implementation has not started.

---

## Problem Summary

HTTP generation requests have bounded token fields, but remote expert RPC is the actual public compute boundary. Its tensor descriptors and handler counts currently imply limits without one typed policy that validates payload shape, dtype, values, metadata size, concurrent execution, queueing, and timeout behavior before model work begins.

Health probing and future failover will increase RPC activity, so the worker needs predictable admission behavior before retries are introduced.

## Dependencies and Boundaries

- Preserve the existing forward RPC and optional useful-work receipt protocol.
- Derive descriptor and runtime limits from one typed configuration source.
- Reject invalid work before moving tensors to the execution device or entering model layers.
- Keep defaults compatible with currently supported model context sizes and normal inference.
- Do not add user API keys, settlement penalties, relay bandwidth quotas, or broad peer reputation in this sprint.

## Proposed Design

- Add an `RPCSafetyConfig` covering sequence length, batch elements, tensor bytes, metadata bytes, concurrent forwards, bounded queue/admission behavior, and per-request execution timeout.
- Validate tensor rank, shape, hidden size, dtype, finite values, attention masks, position IDs, and cross-tensor consistency at the handler boundary.
- Validate receipt metadata as a bounded canonical byte payload before JSON parsing or signature work.
- Use a bounded semaphore or equivalent admission controller; reject overload with a stable actionable error rather than allowing an unbounded queue.
- Propagate cancellation and timeout through owned work, release permits in all paths, and avoid recording useful work for rejected or incomplete requests.
- Add counters for accepted, rejected-by-reason, timed-out, active, and peak-concurrent requests to node status and Monitoring.
- Keep transport-level VPS rate/bandwidth controls documented as a separate operations concern.

## Work Plan

- [ ] Review and approve the initial limits, error contract, and compatibility policy.
- [ ] Define typed RPC safety settings and derive Hivemind descriptors from them.
- [ ] Implement pre-execution tensor and metadata validation.
- [ ] Implement bounded concurrency, overload rejection, timeout, and cancellation cleanup.
- [ ] Integrate useful-work non-payment semantics for rejected, timed-out, or cancelled calls.
- [ ] Add safety counters and concise Monitoring visibility.
- [ ] Document environment settings, expected errors, and VPS-level controls that remain external.

## Test Plan

- Reject oversized sequences, batches, tensors, and metadata before invoking a model layer.
- Reject malformed ranks, inconsistent dimensions, invalid masks/positions, unsupported dtypes, and non-finite activations.
- Saturation tests prove active work never exceeds the configured limit and permits are always returned.
- Timeout and cancellation tests prove no success accounting, receipt release, or lingering execution state.
- Legacy inference without receipt metadata and receipt-enabled inference both remain compatible within limits.
- Fuzz/property-style tests exercise boundary sizes and malformed metadata without crashing the worker.
- Backend regressions, real local Hivemind RPC coverage, frontend type checks, and production build pass when contracts change.

## Acceptance Criteria

- [ ] Every public expert request is validated against one inspectable typed safety policy before model execution.
- [ ] Oversized or malformed requests fail deterministically with no useful-work credit.
- [ ] Concurrent model execution and waiting work remain bounded under load.
- [ ] Timeout, cancellation, and handler exceptions release all admission and accounting state.
- [ ] Normal direct and relayed inference remains compatible at default limits.
- [ ] Monitoring exposes aggregate safety counters without leaking prompts, tensors, identities, or receipt contents.
- [ ] Relay bandwidth quotas and authenticated API access remain explicitly deferred rather than partially implemented here.

---

## Session Log

### 2026-08-13 - Create proposal for RPC resource safety

- What changed: created a review-ready sprint plan for typed RPC limits, validation, admission control, timeout cleanup, accounting behavior, and visibility.
- Why: the expert RPC is the public compute boundary and needs explicit resource safety before health probing and failover can increase request pressure.
- Status: proposal only. No RPC schema, handler, or runtime setting changed; implementation awaits user approval.
