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
- [x] Add fixed serving P2P ports, reachable announce-address configuration, direct probing, and relay fallback for real multi-device routes.
- [ ] Deploy the relay-capable bootstrap and validate direct and relayed two-device inference.
- [ ] Separate local node lifecycle cards from all-peer Monitoring visibility.
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

### 2026-07-14 - Record two-device WSL RPC reachability defect

- What changed: documented the confirmed two-device route failure, including successful DHT discovery, the `0-6` plus `6-12` OPT-125M split, private WSL advertised addresses, random P2P ports, bootstrap-versus-relay semantics, and the required fixed-port/announce-address direction.
- Why: both devices could discover complete model coverage through the public VPS bootstrap, but the generator could not dial the remote expert because separate WSL-private addresses are not mutually routable and the bootstrap does not relay RPC traffic.
- Status: issue 14 is confirmed and registered; no runtime fix was implemented in this documentation session.

### 2026-07-22 - Document Petals-style production reachability design

- What changed: added a review document covering the confirmed WSL transport defect, hotspot and overlay limitations, Petals' direct-reachability and AutoRelay behavior, proposed VPS bootstrap/relay infrastructure, worker lifecycle, configuration, metadata, security, rollout phases, validation matrix, and acceptance criteria; registered the document in both repository indexes.
- Why: the multi-device route needs a reviewed production transport direction that distinguishes DHT discovery from expert RPC reachability and follows the proven Petals direct-or-relay approach.
- Status: the architecture proposal is ready for review; no runtime relay implementation or live transport test was performed in this documentation session.

### 2026-07-22 - Clarify Petals ports and circuit-relay operation

- What changed: expanded the network review with source-level findings from Petals' server, server CLI, and DHT CLI; added a direct answer that public router port forwarding is not required on every device; distinguished local listening, host firewall, and router forwarding; explained the relay reservation, multiaddress, RPC traffic flow, security boundary, bandwidth cost, and bootstrap-versus-relay roles; and labeled the direct-or-relay design as the recommended but not yet implemented solution.
- Why: review feedback showed that the original proposal assumed familiarity with circuit relays and did not make the per-device port requirements or operational data path concrete enough.
- Status: all three review comments are addressed in the document; the remaining proof is a live Hivemind `1.1.12` relay reservation and expert RPC test through the VPS.

### 2026-07-23 - Implement direct reachability and relay fallback

- What changed: added typed direct/relay environment settings, fixed host and announce addresses, a Petals-derived independent reachability protocol, automatic direct-versus-relay node startup, relay-aware generator dialing, relay-capable bootstrap behavior, transport metadata and UI badges, Windows/WSL deployment guidance, and regression tests.
- Why: private WSL `172.x` addresses are not routable from a second Windows host, while requiring every desktop user to forward a public router port is not a production-ready participation model.
- Status: all 107 backend tests, targeted Python compilation, and frontend typechecking pass. The updated bootstrap still needs deployment to the VPS, followed by live direct-LAN and circuit-relay expert RPC and inference tests on two devices.

### 2026-08-11 - Document VPS relay deployment and validation order

- What changed: expanded the network reachability review with a VPS relay deployment runbook, participant environment configuration, minimum relay validation checks, recommended direct-versus-auto test order, and a two-device inference validation checklist.
- Why: the next open risk is live proof on real Windows/WSL devices, and the test plan needs to make clear that relay is the production fallback while direct LAN remains a separate performance/dev path.
- Status: documentation is ready for the VPS relay deployment and two-device inference test; no live VPS deployment or inference run was performed in this documentation session.
