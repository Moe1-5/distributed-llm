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

### 2026-08-20 - Document the generator-only discovery deadlock from physical testing

- What changed: added `docs/CURRENT_TWO_DEVICE_LIVE_TEST_ISSUES.md` and routed it through both documentation indexes. The report consolidates the current two-device topology, confirmed Device 2 worker evidence, contradictory Monitoring state, the Device 1 generator-start deadlock, the API workaround, required permanent fixes, and remaining relay/incentive acceptance gates.
- Why: the worker-only Device 2 backend could see its own full `0-12` advertisement, while generator-only Device 1 showed no coverage and disabled generator start. Source review confirmed that the backend creates its discovery client during generator startup while the frontend requires a complete pre-start serving plan, creating a circular dependency.
- Status: diagnosis and operator workaround are documented. No runtime was restarted and no physical probe was run; the backend discovery lifecycle, frontend start gate, Monitoring snapshot consistency, and final two-device inference/receipt acceptance remain open.

### 2026-08-22 - Record the remotely invisible publication lease failure and production roadmap

- What changed: expanded `docs/CURRENT_TWO_DEVICE_LIVE_TEST_ISSUES.md` with the correlated two-device timeline, the successful discovery and relay tensor canary, the later expert and provider disappearance, the worker's contradictory fresh local publication status, a staged DHT/expert repair design, production HTTPS settlement architecture, managed-backend packaging requirements, and the remaining release phases. Corrected stale `/health` and `/providers` commands in the deployment guide and linked it to the incident report.
- Why: Device 1 reached generator ready at 18:07:53 UTC, then suspended at 18:13:11 when `distribllm.0.12` disappeared and provider metadata later became absent. Device 2 remained locally running and reported fresh error-free expert and node publication after Device 1 lost visibility, proving that local refresh success is not sufficient evidence of remote availability.
- Status: initial cross-device discovery, metadata RPC, relay tensor canary, generator load, and unsafe-route suspension passed. Sustained remote lease visibility, user generation, shadow receipt acceptance, automatic network recovery, self-contained packaging, and tunnel-free production settlement remain open. This session changed documentation only and did not repeat the physical test or implement the runtime fix.

### 2026-08-22 - Require remote leases and remove generator discovery deadlock

- Root-cause candidate: Hivemind DHT stores from a client-mode worker can include the worker's own local storage or cache. The failed-run code accepted that local success even though an independent generator could no longer retrieve the expert or provider record.
- Backend: supervised exact expert and receipt UID writes now exclude the worker itself whenever bootstrap peers are configured, use a lease horizon ahead of the built-in publisher, and reject every unacknowledged remote write. Provider metadata uses the same remote-only requirement. Isolated no-bootstrap local development retains local publication behavior.
- Membership and recovery: workers publish an independently expiring `{prefix}.members.v2` subkey for their own peer while keeping the old aggregate key only as a best-effort rolling-upgrade fallback. Discovery merges v2 and legacy records. After two consecutive remote lease failures, with a cooldown, the worker recreates DHT and RPC transport handles without unloading model layers and reports recovery state.
- Frontend: generator start is no longer disabled by a pre-generator local-only serving-plan snapshot; startup creates the DHT client and still enforces RPC-health, route, and tensor-canary checks. Monitoring derives percent and missing ranges from the same live serving set and labels worker-only state as a fresh lease that has not been probed.
- Acceptance observer: added `backend/lease_observer.py`, which joins with a separate DHT identity and emits JSON Lines evidence for v2 membership, provider metadata, normal and receipt expert ownership, and safe expiration horizons. Any failed sample makes the soak command exit nonzero.
- Verification: 155 generation-readiness, provider-health, coverage, and observer backend tests pass, including new remote-store, per-peer membership, eleven-expiry-window, independent-observer, and layer-preserving recovery cases. The serving-plan endpoint regression now waits for its documented stale-while-refresh result before asserting remote coverage. Electron node and renderer type checks pass. ESLint reports zero errors and existing formatting warnings. Python compilation passes. No physical relay probe was run by the assistant.
- Remaining: deploy equivalent always-on observation for production, build one clean artifact from the committed repair, then repeat the two-device relay test with the standalone observer for at least ten real lease windows and complete real prompts plus shadow receipt acceptance.

### 2026-08-22 - Publish and package the remote-lease repair

- What changed: committed the remote-only DHT lease acknowledgements, per-peer membership records, layer-preserving transport recovery, generator-start workflow correction, Monitoring consistency fix, independent lease observer, tests, and incident documentation on `fix/remote-dht-lease-recovery`, then pushed the branch.
- Why: the next physical run must use one reviewable source revision and one executable identity rather than the stale build that accepted worker-local DHT storage as publication success.
- Verification: 155 focused backend tests from the repair session, Python compilation, Electron type checks, 20 launcher tests, two renderer-flow tests, the production build, and the Windows package audit pass. The packaged main process embeds source commit `8daf6e21fa542bdfdbf87d36e459f916682466ea` with the tracked-source dirty flag set to false.
- Artifact: `DistribLLM-1.0.0-portable.exe` is 87,656,445 bytes with SHA-256 `833ef6c17e757e75e16634890583172ed741eecb2ebdb35fcf43ae3c98dc8b58`; the ASAR contains 36 entries and zero forbidden entries.
- Remaining: install this exact executable and backend commit on both devices, run the independent observer for at least 1,000 seconds, complete real inference prompts, verify shadow receipt acceptance, and preserve the evidence before final approval.

### 2026-08-22 - Preserve worker identity across network recovery

- Physical finding: the independent observer and tensor canary passed, but real prompts ended in an ambiguous `stream reset`. Device 2 later recovered its DHT/RPC transport without unloading the handler, while its peer ID changed and invalidated Device 1's selected route.
- What changed: every local worker now receives a private persistent libp2p key, DHT restarts reuse that key, recovery requires the previous peer ID, and a mismatched identity is shut down and rejected before RPC publication. Local node diagnostics retain the triggering lease error, failure count, before/after peer IDs, and identity-preservation result without exposing the key path.
- Configuration: added `DISTRIBLLM_P2P_IDENTITY_DIR`; its empty/default value uses the WSL-local `~/.distribllm/p2p-identities` directory and remains separate from the useful-work application signing identity.
- Verification: 157 generation-readiness, provider-health, coverage, and observer tests pass; five focused identity/recovery regressions pass; a real local Hivemind DHT restart reproduced the same peer ID from one saved key; Python compilation, Electron type checks, 20 launcher tests, two renderer-flow tests, and diff checks pass.
- Status: the confirmed peer-rotation defect is repaired locally. The original real-forward stream reset is not claimed fixed and remains a separate physical transport gate. A clean committed build and two-device retest are next.

### 2026-08-22 - Package the identity-preserving recovery build

- What changed: committed and pushed the persistent worker-identity repair as `9fec4a8903162d34432bc2b7357b665fda8715c1`, then built the Windows portable application with that exact source commit and a clean tracked-source flag embedded in the packaged main process.
- Verification: Electron type checks, 20 launcher tests, two renderer-flow tests, the production build, direct generated-bundle inspection, packaged ASAR inspection, and the Windows package audit pass. The audit reports 36 ASAR entries and zero forbidden entries.
- Artifact: `DistribLLM-1.0.0-portable.exe` is 87,658,063 bytes with SHA-256 `816893d26e6d12e6aae261a5cc15c574268e612cd1aadf4942a69b27d7a2dbba`.
- Status: this supersedes the prior `8daf6e21` artifact. Physical acceptance must use this exact executable and backend source commit on both devices, verify peer-ID continuity through any recovery, complete real prompts, and preserve correlated evidence if the relay stream reset recurs.

### 2026-08-22 - Isolate the repeated real-forward stream failure

- Physical result: the persistent-identity build reached generator ready and passed its tensor canary, while an independent observer repeatedly retrieved the same Device 2 peer, provider record, normal expert, and receipt expert with healthy lease horizons.
- Failure: a real prompt still ended in `ambiguous_transport` at layers `0-12`; automatic failover was correctly suppressed because remote execution could not be ruled out.
- Boundary: Device 2 reported zero network recoveries, so neither expired discovery data nor peer rotation caused this occurrence. The remaining fault is in the full forward/response stream after readiness validation.
- Diagnostics: Trace uses the same generation path and separately exceeded the frontend's generic eight-second HTTP deadline. The UI also retained `Waiting for generator route` while showing Generator and Route ready; both presentation defects are routed to Sprint 27.
- Evidence limitation: an earlier observer run used a literal peer placeholder and is invalid. The corrected run used `QmRevwu67tzcBjWW21u11Q7oPuhbtEodmuDD87aoW9Yp6z` and returned `ok: true`.
- Status: stop repeated prompt attempts, preserve post-failure participant and relay evidence, and correlate the reset before implementing another transport change.
