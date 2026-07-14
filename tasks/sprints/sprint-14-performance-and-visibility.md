# Sprint 14 - Performance and Visibility

**Goal:** Make distributed inference performance observable and fix the user-visible output path so live model behavior can be measured, compared, and improved with evidence.
**Start:** 2026-07-14
**End:** TBD

---

## Problem Summary

The live TinyLlama route now completes correctly, but the application does not expose enough runtime measurements to explain slow generation or resource pressure. Production chat requests also omit model-aware chat templates, and independently decoded stream tokens lose word spacing. Sprint 14 combines the immediate user-visible correctness fixes with the performance instrumentation needed for later route scoring and optimization.

## Product Decision

- Measure before optimizing: expose timestamped system, backend-process, GPU, route, and generation metrics.
- Keep unavailable measurements explicit instead of reporting invented zero values.
- Apply publisher tokenizer chat templates only to chat/instruct models; base models remain raw completion models.
- Stream context-aware text deltas whose concatenation matches full-sequence decoding.
- Keep route selection changes out of this sprint until latency evidence is reliable.

## In Progress

- [x] Register Sprint 14 and start its dedicated branch.
- [x] Add the first runtime resource metrics slice to the backend and Monitoring page.
- [ ] Add generation and route timing metrics.

## Todo

- [x] Report timestamped machine CPU/RAM and backend-process CPU/RSS/thread usage.
- [x] Distinguish PyTorch allocated and reserved VRAM.
- [x] Surface runtime resource measurements on Monitoring with unavailable states.
- [ ] Record generator startup/load duration, route validation duration, time to first token, total generation duration, and tokens per second.
- [ ] Record per-hop RPC latency without changing route selection behavior.
- [ ] Apply model-aware tokenizer chat templates in production chat/instruct requests.
- [ ] Make streamed text deltas reconstruct the tokenizer's context-aware full decode.
- [ ] Add regression tests for chat formatting, stream spacing, metric contracts, and unavailable hardware.
- [ ] Update architecture, flow, validation, and troubleshooting documentation after the contracts settle.
- [ ] Run a live TinyLlama performance smoke pass and record baseline measurements.

## Deferred To Later Sprint

- Latency-weighted route selection and automatic failover.
- Distributed key/value cache and stable session routing.
- Peak full-model CPU-memory reduction during layer-slice loading.
- Real rewards, receipts, anti-abuse checks, and settlement.

## Acceptance Criteria

- [ ] Monitoring displays fresh machine, backend-process, GPU, route, and generation measurements with clear units.
- [ ] The backend exposes time to first token, total duration, generated token count, and tokens per second for completed generation.
- [ ] Chat/instruct prompts use the selected model's tokenizer chat template exactly once.
- [ ] Concatenated stream fragments match full generated-sequence decoding, including spaces.
- [ ] Missing GPU or platform-specific metrics are reported as unavailable, not misleading zeros.
- [ ] Automated tests cover the performance contract and both live TinyLlama output findings.
- [ ] A live distributed TinyLlama pass records a reproducible performance baseline.

---

## Session Log

### 2026-07-14 - Start runtime performance visibility

- What changed: created Sprint 14, expanded backend runtime samples with timestamps, CPU topology/load, available RAM, backend-process CPU/RSS/thread usage, and reserved VRAM, and added resource cards to Monitoring with focused backend tests.
- Why: the live route works but performance bottlenecks and process resource pressure are not visible enough to guide optimization or explain slow inference.
- Status: the first resource visibility slice is verified with 104 backend tests, 19 subtests, Python compilation, and both frontend typechecks; generation timing, per-hop latency, chat-template application, and context-aware streaming remain open.

### 2026-07-14 - Prevent Monitoring black screen with mixed backend versions

- What changed: made newly added runtime-stat fields optional in the frontend contract, rendered legacy GPU payloads without dereferencing missing reserved-memory data, and added a page error boundary that preserves navigation and displays the render failure instead of a black window.
- Why: an Electron renderer updated for Sprint 14 could remain connected to an older backend process whose `/stats` response lacked `vram_reserved_gb`, causing `toFixed()` on `undefined` during Monitoring render.
- Status: frontend typechecks and the production Electron/Vite build pass, and all 104 backend tests plus 19 subtests remain green; frontend lint still reports one pre-existing unescaped-apostrophe error in Network plus existing formatting warnings.
