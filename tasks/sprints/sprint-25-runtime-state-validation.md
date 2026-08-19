# Sprint 25 - Runtime State Validation

**Goal:** Make generator, route, node, and Monitoring readiness derive from one authoritative runtime state, and reject inference until a complete RPC-healthy route is verified.
**Start:** 2026-08-14
**End:** TBD

---

## In Progress

- [x] Add coherent cached runtime snapshots with explicit generator and route states.
- [x] Reject generator startup before model loading when coverage or provider health is incomplete.
- [x] Revalidate route health after loading and clean failed candidates completely.
- [x] Reconcile local node turn-off/delete actions with dependent generator state.
- [x] Require confirmation for every additional exact local replica without imposing an arbitrary hard count cap.
- [x] Remove blocking DHT scans from ordinary renderer refresh paths.
- [x] Surface staged, correlated provider and route diagnostics.

## Acceptance Criteria

- [x] Missing, unhealthy, or stale layers cannot produce a generator-ready result.
- [x] `/status`, `/generator/status`, Network, Nodes, Inference, and Monitoring agree on readiness.
- [x] Deleting the last required local provider unloads its generator after confirmation.
- [x] Remote route loss suspends inference without unloading reusable local components.
- [x] A healthy alternate route preserves generator readiness.
- [x] Every additional exact local replica requires confirmation and remains subject to real resource limits.
- [x] Model catalog, serving-plan, status, and Monitoring refreshes remain deadline-bound.
- [ ] Backend, frontend, package, and two-device relay acceptance checks pass.

---

## Session Log

### 2026-08-14 - Start approved runtime validation hardening

- What changed: created Sprint 25 and began implementation on `feature/runtime-state-validation`.
- Why: live Windows testing exposed generator-ready state without a usable route, stale lifecycle state after node deletion, repeated exact replicas, and blocking DHT refresh requests.
- Status: implementation in progress; physical relay acceptance remains required.

### 2026-08-14 - Implement authoritative readiness and lifecycle validation

- What changed: added the runtime state store, strict RPC and tensor route startup gate, suspended route state, dependent-node delete confirmation, exact replica cap, bounded diagnostic history, and stale-while-refresh node and serving-plan snapshots.
- Frontend: generator readiness now follows `/generator/status`, model refreshes use the fast catalog, and dependent deletion and replica creation require confirmation.
- Verified: twelve focused backend lifecycle/runtime tests passed; frontend type checks and production build passed.
- Remaining: rebuild the Windows package and run the physical two-device relay acceptance matrix before closing the sprint.

### 2026-08-16 - Build the runtime-validation Windows acceptance artifact

- What changed: committed the previously verified managed-WSL script transport correction, then built the portable Windows application from clean source commit `e7350c227cec3f19cc9e0c4f38799bae50d759b4` with Sprint 25 and Sprint 26 included.
- Verification: twenty launcher tests, Electron node and renderer type checks, twenty-one focused backend access/runtime/lifecycle tests, the production build, and the Windows package audit pass. The ASAR contains 36 entries and zero forbidden entries, and direct inspection confirms the full source commit with a clean-source flag.
- Artifact: `DistribLLM-1.0.0-portable.exe` is 87,658,512 bytes with SHA-256 `41e7fef4a3efb9b5a62bc8ffc8ea51f1eb87d4291dcad4faee329d60c068b78c`.
- Status: this artifact supersedes previous executables for Sprint 25 testing. The remaining gate is the physical two-device relay, lifecycle, monitoring, and inference acceptance run.

### 2026-08-16 - Diagnose suspended-generator restart trap during Windows acceptance

- What changed: documented the physical acceptance finding and added a project lesson requiring correlated runtime evidence before prescribing recovery.
- Why: a full-range local node was online with its RPC server active, while a previously loaded generator was suspended; clicking Start returned `suspended` immediately, but the lifecycle job labeled it ready and the UI exposed neither the suspended state nor an unload action.
- Status: the state-machine and UI recovery defects are confirmed in code. The captured runtime snapshot also proves that startup, metadata RPC, and the tensor canary initially passed, then the worker advertisement stopped refreshing and expired while local lifecycle flags remained online. The permanent fix must bound and supervise announcement/probe work, expose live advertisement freshness, and recover a stale worker transport instead of presenting cached RPC state as active.

### 2026-08-16 - Fix expiring worker membership and suspended-state recovery

- Root cause: a restarted worker whose peer ID already existed in the shared members index refreshed only its node metadata. The old members record could therefore expire shortly after a successful startup canary, removing every provider from generator discovery and suspending the route.
- Backend: every worker heartbeat now refreshes both its node metadata and the deduplicated members index using DHT time and bounded operations. Local node readiness includes heartbeat-thread and announcement freshness. A stuck metadata probe records one timeout and transitions explicitly offline after its bounded stall window instead of accumulating hundreds of synthetic failures.
- Runtime and frontend: non-ready lifecycle results such as `suspended` now fail their start job instead of reporting ready. Provider-specific health reasons are returned, suspended loaded generators can be unloaded from Network, and local node cards distinguish an active RPC process from a fresh DHT heartbeat.
- Verified: 24 focused backend health, lifecycle, and runtime tests passed; two focused node announcement/resume tests passed; Electron node and renderer type checks, two renderer flow tests, the production build, and Windows portable packaging passed.
- Artifact: `DistribLLM-1.0.0-portable.exe` is 87,655,914 bytes with SHA-256 `fe0ff75f329779d358500005f9f7a0ac77f8899a7f91b40a707446eb906d07df`; the package audit reports 36 ASAR entries and zero forbidden entries.
- Remaining: restart both managed backends from this source, serve the split route, verify DHT heartbeat freshness beyond 90 seconds, and complete real generation through the VPS relay before closing Sprint 25.

### 2026-08-17 - Bind Hivemind RPC futures and preserve relay diagnostics

- What changed: expert task admission now binds every Hivemind `MPFuture` to the active RPC event loop and rejects loopless admission with a stable `rpc_transport:event_loop_unavailable` error. Provider probes now report whether failure occurred during expert lookup or metadata RPC, include peer and RPC identity, preserve timeout semantics, and expose active probe age/thread/timeout state in runtime snapshots. Electron records generator state and health-revision transitions in the Activity Log and shows compact health context while suspended.
- Why: physical Windows testing showed a complete full-model advertisement alternating between ready and suspended with `Can't await: MPFuture was created with no event loop`. Direct inspection confirmed Hivemind 1.1.12 can create an unbound future under Python 3.12, while the previous UI lost the transition evidence after recovery.
- Verification: 44 focused backend runtime, RPC safety, provider-health, and lifecycle tests pass; the real Hivemind receipt RPC integration passes; a real local two-provider OPT-125M split generated matching text with zero failed worker requests; Electron node and renderer type checks, 20 launcher tests, two renderer-flow tests, production build, and package audit pass.
- Artifact: `DistribLLM-1.0.0-portable.exe` is 87,659,838 bytes with SHA-256 `621fc2275c3bf457469c155762b3fada61d02421253bc08d8fb6d5d21a97f4f3`; the audit reports 36 ASAR entries and zero forbidden entries.
- Status: the local defect path is hardened and diagnostic evidence is retained. Final proof still requires running this backend and executable on both physical devices through the VPS relay for longer than the DHT expiry window, then completing real generation.

### 2026-08-19 - Restore synchronous expert lookup for provider health probes

- What changed: the continuous provider-health probe now resolves experts synchronously, matching the proven Sprint 14 route path, while retaining staged diagnostics, bounded monitor concurrency, stalled-probe detection, and the cancellable metadata RPC deadline. The focused regression test runs the probe in a worker thread without an event loop and verifies that `get_experts` is called without `return_future=True`.
- Why: physical testing proved that the asynchronous lookup created a Hivemind 1.1.12 `MPFuture` in a Python 3.12 background thread with no event loop, then failed when Hivemind awaited it from the remote-expert loop.
- Verification: eleven focused provider-health state, monitor, and integration tests passed. The user will perform the remaining live generator and physical relay validation; no further live probe was run in this session.

### 2026-08-19 - Supervise expert publication from the node heartbeat

- Root cause: Hivemind expert UIDs and DistribLLM provider metadata use separate DHT leases. The custom node heartbeat could remain fresh while Hivemind's unsupervised publisher thread had stopped, so expert lookup first reported `Expert ... was not found` and provider metadata disappeared later when its longer lease expired.
- Backend: RPC servers now align Hivemind's publication interval and expiry with the project heartbeat, expose server, runtime, built-in publisher, UID, freshness, failure-count, and last-error diagnostics, and refresh every active expert UID from the bounded node heartbeat. A dead built-in publisher is tolerated while the supervised refresh remains healthy.
- Validation: local node readiness now requires a fresh expert publication, and DHT node metadata includes `rpc_publication`. If expert refresh fails, provider metadata is still updated with the failure state so generators reject the provider for an observable reason instead of relying on stale online flags.
- Regression coverage: added focused cases for primary and receipt UID refresh, newer-record handling, publication freshness, heartbeat refresh timing, and publication diagnostics in advertised metadata. Automated tests and physical relay validation were intentionally left to the user for this handoff.

### 2026-08-19 - Restore confirmed local replica flexibility

- What changed: removed the hard limit of two exact local replicas while retaining a confirmation gate for every additional identical model and layer range. The confirmation now reports how many loaded replicas already exist and explains that turned-off replicas retain layers and consume memory.
- Why: exact replicas are valid route alternates and useful for round-robin and failover testing; an arbitrary count cap is not a correctness requirement and prevented intentional replacement or stress-test topologies when retained offline replicas already occupied the limit.
- Status: different models, adjacent same-model ranges, and confirmed exact replicas remain supported; non-exact overlapping same-model ranges remain rejected. Regression expectations were updated, while automated and physical testing remain with the user.
