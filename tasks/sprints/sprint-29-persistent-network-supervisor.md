# Sprint 29 - Persistent Network Supervisor

**Goal:** Give each backend one stable control-plane supervisor while preserving distinct worker and generator peer identities required by Hivemind.
**Start:** 2026-08-23
**End:** TBD
**Status:** Implemented locally; packaged two-device validation is pending.

---

## Problem Summary

A clean backend does not currently own a discovery client until it starts a worker or generator. A device can therefore be asked to choose layers before it can observe remote coverage. Worker publication, generator discovery, provider health, recovery, and frontend serving-plan refreshes also use overlapping lifecycle state, so transient DHT outcomes can be promoted into destructive transport recovery or authoritative empty coverage.

Hivemind 1.1.12 documents a false DHT store result as ambiguous: it can mean no response or that a newer value already exists. That result alone is not proof that a working RPC server or transport must be restarted.

## Dependencies and Boundaries

- Depend on Sprint 28's exact peer and expert-ownership protocol.
- Reuse Sprint 19's provider RPC health state machine and Sprint 25's application runtime snapshots.
- Publish structured network state for Sprint 27 to render; renderer polling must not own network correctness.
- Do not allocate layer ranges or create distributed sessions; Sprints 30 and 31 own those behaviors.
- Preserve stop, offline, resume, and unload as separate lifecycle actions.
- Preserve a distinct stable P2P identity for every serving worker and a separate generator/client identity. One supervisor owns orchestration and control-plane truth; it must not collapse co-located roles onto one self-dialing peer.

## Work Plan

- [x] Start one persistent control-plane discovery supervisor with the backend lifecycle.
- [x] Let the supervisor manage role-specific worker and generator peer runtimes, shared network configuration, and topology snapshots without sharing a P2P identity or self-dialing handle between roles.
- [x] Expose `disconnected`, `syncing`, `ready`, and `degraded` states with revision, timestamps, last success, and exact failure stage.
- [x] Preserve the last-good remote topology through transient refresh failures and expose its age honestly.
- [x] Assign one explicit owner to each publication lease and remove competing project-owned publishers.
- [x] Stop treating `DHT.store(False)` as sufficient evidence for destructive transport recovery.
- [x] Distinguish newer-record conflict, missing acknowledgement, timeout, validation failure, and independently verified connectivity loss.
- [x] Preserve loaded layers and peer identity during justified transport repair.
- [x] Emit bounded network lifecycle and publication events into the authoritative runtime diagnostics stream.
- [x] Give the control-plane DHT and every role-specific p2p, monitor, publication task, startup future, generation operation, and node lifecycle action one exact shutdown owner; retain and quarantine any handle that cannot be proven stopped within the bounded API deadline.

## Test Plan

- [ ] Physical: a clean generator-only and worker-only packaged backend discovers an existing remote provider before starting either role.
- [x] Automated: a newer-record DHT rejection leaves a healthy RPC server online and schedules bounded metadata repair.
- [x] Automated: an independently verified transport loss triggers one identity-preserving recovery without unloading layers.
- [x] Automated: a failed refresh retains the last-good topology as stale rather than replacing it with authoritative empty coverage.
- [x] Automated: mixed worker/generator start, stop, unload, restart, cancellation, and backend shutdown retain exact owners and do not replace live handles after an await.
- [ ] Physical: a co-located worker and generator keep different peer IDs, route through the worker normally, and never trigger Hivemind's self-dial rejection.
- [x] Automated: API snapshots remain compatible while new network fields are optional to older renderers.
- [ ] Physical: two packaged devices serve adjacent zero-to-six and six-to-twelve ranges, sustain real generation, and shut down without stale coverage or identity rotation.

## Acceptance Criteria

- [ ] Remote discovery is available before any worker or generator starts on both packaged physical devices.
- [x] A false store result caused by a newer record cannot stop a healthy RPC server.
- [x] Verified transport recovery preserves peer ID and loaded layers in the implemented and automated path.
- [x] Transient DHT failure cannot become authoritative empty coverage.
- [x] All network resources have one lifecycle owner; shutdown responses are bounded and unresolved exact handles remain retained or quarantined until quiescence is proven.
- [ ] Co-located workers and generators retain distinct stable peer identities under one supervisor.
- [x] UI polling is a passive consumer of authoritative network state.

---

## Session Log

### 2026-08-23 - Create persistent network supervisor sprint

- What changed: created the sprint for an always-on control-plane supervisor, explicit network states, last-good topology, role-specific peer runtimes, publication ownership, evidence-based recovery, and deterministic cleanup.
- Why: architecture analysis confirmed a discovery lifecycle deadlock and showed that ambiguous Hivemind store results are currently used as stronger transport evidence than the dependency contract permits.
- Status: planning is complete and depends on Sprint 28; no runtime source changed in this session.

### 2026-08-23 - Publish explicit offline metadata before deleting a provider

- What changed: node stop and pause now share an offline-publication step that writes `running=false` and `rpc_running=false` before RPC or DHT teardown; local-node unregister wakes the persistent supervisor; focused regressions verify teardown ordering, non-serving discovery, and refresh signaling.
- Why: deleting an active worker previously left its last running lease eligible for retained serving coverage until both the DHT lease and supervisor disappearance grace expired.
- Status: four focused node/delete lifecycle tests pass, production and test modules compile, and the working-tree diff passes whitespace validation.

### 2026-08-23 - Implement and harden the persistent network control plane

- What changed: added the backend-lifespan control-plane supervisor, cache-disabled last-good discovery, role-specific stable identities, typed publication classification, authoritative local-worker overlays, evidence-gated identity-preserving recovery, exact DHT/RPC/monitor ownership, transactional node and generator startup, active generation and node-operation admission, cancellation-safe cleanup, passive network snapshots, frontend network contracts, and regression coverage for lifecycle races and terminal failures.
- Why: the packaged devices could report fresh-but-empty coverage, duplicate the same recommended range, rotate worker identity during recovery, and validate one runtime handle before executing or shutting down another. Hivemind 1.1.12 also makes a false store result ambiguous, so it cannot justify destructive recovery by itself.
- Status: the earlier 363-test checkpoint passed. Source implementation remained under lifecycle/concurrency audit, while clean packaged discovery, co-located identity, adjacent split generation, and shutdown evidence remained physical acceptance gates.

### 2026-08-23 - Complete lifecycle ownership hardening and full verification

- What changed: serialized every worker DHT command-pipe borrower; added coherent node snapshots, partial-cleanup retry, process-death verification, generator single-operation admission, transactional node/generator lifecycle ownership, passive status reads, exact local role overlays, supervisor operation quarantine, immutable last-good scan commits, and frontend resource contracts. Updated tests cover late cancellation, shutdown/restart races, identity reuse, unsafe lease horizons, and exact-handle retention.
- Why: the final audit found races where unload or cancellation could release admission before the owned runtime stopped, concurrent generator requests could share mutable session state, and a timed-out DHT future or process could be mistaken for safe identity reuse.
- Status: all 388 backend tests pass, including real peer-addressed Hivemind integration. Frontend type checking, 20 launcher tests, four renderer-flow tests, production build, and lint with zero errors pass; 78 pre-existing formatting warnings remain. The serial topology scan deliberately retains exclusive ownership if it exceeds the stop deadline because Hivemind 1.1.12 does not expose a safe cancellation boundary. Source work is ready for an audited Windows build and the three physical acceptance gates remain open.

### 2026-08-23 - Close final request and transport teardown races

- What changed: rejected concurrent OpenAI streams now release only their own access reservation and cannot cancel the active generator owner; all API generation consumers and their inner owned streams close deterministically on error or disconnect; Turn Off atomically recognizes failed-recovery remnants; reachability stop requests survive delayed P2P replication and retain the exact startup owner until exit; forked Hivemind connection handlers replicate from a parent-captured daemon address without touching the worker DHT pipe or its parent-thread lock.
- Why: the final concurrency review found four paths where a rejected request, failed recovery, delayed reachability startup, or force-terminated child process could affect a different owner or leave a shared synchronization primitive permanently unusable.
- Status: 214 affected-path tests pass together, including real peer-addressed two-server Hivemind RPC, and the full backend suite passes all 400 tests. Frontend type checking, 20 launcher tests, four renderer-flow tests, production build, Python compilation, and lint with zero errors pass; 78 existing formatting warnings remain. Hivemind 1.1.12 daemon-address capture is synchronous and private-API-coupled, so a wedged parent DHT can still delay RPC startup. The audited Windows build and three physical acceptance gates remain open.
