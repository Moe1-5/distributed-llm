# Sprint 20 - RPC Resource Safety

**Goal:** Enforce explicit tensor, metadata, concurrency, timeout, and admission limits at the worker RPC boundary so public-swarm traffic cannot consume unbounded compute or memory.
**Start:** 2026-08-13
**End:** TBD
**Status:** Implemented and locally verified on `feature/fundamental-live-stability`; live relay compatibility remains part of packaged two-device acceptance.

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

## Implemented Design

- Add an `RPCSafetyConfig` covering sequence length, batch elements, tensor bytes, metadata bytes, concurrent forwards, bounded queue/admission behavior, and per-request execution timeout.
- Validate tensor rank, shape, hidden size, dtype, finite values, attention masks, position IDs, and cross-tensor consistency at the handler boundary.
- Validate receipt metadata as a bounded canonical byte payload before JSON parsing or signature work.
- Use a bounded semaphore or equivalent admission controller; reject overload with a stable actionable error rather than allowing an unbounded queue.
- Propagate cancellation and timeout through owned work, release permits in all paths, and avoid recording useful work for rejected or incomplete requests.
- Add counters for accepted, rejected-by-reason, timed-out, active, and peak-concurrent requests to node status and Monitoring.
- Keep transport-level VPS rate/bandwidth controls documented as a separate operations concern.

## Work Plan

- [x] Review and approve the initial limits, error contract, and compatibility policy.
- [x] Define typed RPC safety settings and derive Hivemind descriptors from them.
- [x] Implement pre-execution tensor and metadata validation.
- [x] Implement bounded concurrency, overload rejection, timeout, and cancellation cleanup.
- [x] Integrate useful-work non-payment semantics for rejected, timed-out, or cancelled calls.
- [x] Add safety counters and concise Monitoring visibility.
- [x] Document environment settings, expected errors, and VPS-level controls that remain external.

## Test Plan

- Reject oversized sequences, batches, tensors, and metadata before invoking a model layer.
- Reject malformed ranks, inconsistent dimensions, invalid masks/positions, unsupported dtypes, and non-finite activations.
- Saturation tests prove active work never exceeds the configured limit and permits are always returned.
- Timeout and cancellation tests prove no success accounting, receipt release, or lingering execution state.
- Legacy inference without receipt metadata and receipt-enabled inference both remain compatible within limits.
- Fuzz/property-style tests exercise boundary sizes and malformed metadata without crashing the worker.
- Backend regressions, real local Hivemind RPC coverage, frontend type checks, and production build pass when contracts change.

## Acceptance Criteria

- [x] Every public expert request is validated against one inspectable typed safety policy before model execution.
- [x] Oversized or malformed requests fail deterministically with no useful-work credit.
- [x] Concurrent model execution and waiting work remain bounded under load.
- [x] Timeout, queued cancellation, and handler exceptions release all owned admission and accounting state; in-flight tensor kernels use cooperative layer deadlines.
- [ ] Normal direct and relayed inference remains compatible at default limits. Direct real-Hivemind coverage passes; live relayed tensor acceptance remains open.
- [x] Monitoring exposes aggregate safety counters without leaking prompts, tensors, identities, or receipt contents.
- [x] Relay bandwidth quotas and authenticated API access remain explicitly deferred rather than partially implemented here.

---

## Session Log

### 2026-08-13 - Create proposal for RPC resource safety

- What changed: created a review-ready sprint plan for typed RPC limits, validation, admission control, timeout cleanup, accounting behavior, and visibility.
- Why: the expert RPC is the public compute boundary and needs explicit resource safety before health probing and failover can increase request pressure.
- Status: proposal only. No RPC schema, handler, or runtime setting changed; implementation awaits user approval.

### 2026-08-13 - Implement typed RPC validation and bounded admission

- What changed: added one validated `RPCSafetyConfig` for context, batch, combined tensor bytes, framed receipt payload bytes, active execution, task queue, queue wait, and cooperative layer deadline. Hivemind descriptors, task-pool limits, connection handlers, wrappers, and status all derive from this policy.
- Validation: rank, batch, sequence, hidden size, dtype, finite activations, masks, position IDs, tensor-byte totals, metadata dtype/shape, and the four-byte metadata frame length are checked before device transfer, JSON parsing, signatures, or model layers.
- Admission: Hivemind's existing task pools retain batching but use nonblocking bounded submission, producing stable `rpc_safety:batch` or `rpc_safety:overloaded` errors. A multiprocessing-safe controller bounds active/queued wrapper work and reports accepted, completed, failed, rejected-by-reason, timed-out, active, queued, and peak counters across the runtime-process boundary.
- Timeout and receipts: the handler checks a monotonic deadline before lock acquisition, between decoder layers, and after the final layer. Receipt accounting is deferred until the entire batch and signature output complete, so malformed, rejected, timed-out, or post-model receipt failures create no success accounting or settlement artifact. Hivemind removes cancelled queued futures before batching; already-running kernels cannot be force-killed and therefore rely on cooperative layer deadlines.
- Verification: 17 focused tests plus 28 adversarial subtests pass, including full-queue rejection, process-shared counters, permit recovery, malformed metadata frames, transactional receipt failure, and timeout accounting. A real local Hivemind receipt call above 128 KiB passes and its parent-visible safety counters show one accepted/completed request. Full backend passes 233 tests plus 54 subtests; frontend type checks and production build pass.
- Remaining: repeat normal tensor inference through the physical VPS relay using the rebuilt package. The sprint stays active until that acceptance evidence and explicit closure.
