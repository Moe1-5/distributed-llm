# Sprint 29 - Persistent Network Supervisor

**Goal:** Give each backend one stable control-plane supervisor while preserving distinct worker and generator peer identities required by Hivemind.
**Start:** 2026-08-23
**End:** TBD
**Status:** Planned behind Sprint 28.

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

- [ ] Start one persistent control-plane discovery supervisor with the backend lifecycle.
- [ ] Let the supervisor manage role-specific worker and generator peer runtimes, shared network configuration, and topology snapshots without sharing a P2P identity or self-dialing handle between roles.
- [ ] Expose `disconnected`, `syncing`, `ready`, and `degraded` states with revision, timestamps, last success, and exact failure stage.
- [ ] Preserve the last-good remote topology through transient refresh failures and expose its age honestly.
- [ ] Assign one explicit owner to each publication lease and remove competing project-owned publishers.
- [ ] Stop treating `DHT.store(False)` as sufficient evidence for destructive transport recovery.
- [ ] Distinguish newer-record conflict, missing acknowledgement, timeout, validation failure, and independently verified connectivity loss.
- [ ] Preserve loaded layers and peer identity during justified transport repair.
- [ ] Emit bounded network lifecycle and publication events into the authoritative runtime diagnostics stream.
- [ ] Close the control-plane DHT and every supervisor-managed role-specific p2p, monitor, and publication task exactly once during backend shutdown.

## Test Plan

- A clean generator-only and worker-only backend discovers an existing remote provider before starting either role.
- A newer-record DHT rejection leaves a healthy RPC server online and schedules bounded metadata repair.
- An independently verified transport loss triggers one identity-preserving recovery without unloading layers.
- A failed refresh retains the last-good topology as stale rather than replacing it with authoritative empty coverage.
- Mixed worker/generator start, stop, unload, restart, and backend shutdown sequences leak no supervisor task or p2p process.
- A co-located worker and generator keep different peer IDs, route through the worker normally, and never trigger Hivemind's self-dial rejection.
- API snapshots remain compatible while new network fields are optional to older renderers.

## Acceptance Criteria

- [ ] Remote discovery is available before any worker or generator starts.
- [ ] A false store result caused by a newer record cannot stop a healthy RPC server.
- [ ] Verified transport recovery preserves peer ID and loaded layers.
- [ ] Transient DHT failure cannot become authoritative empty coverage.
- [ ] All network resources have one lifecycle owner and deterministic cleanup.
- [ ] Co-located workers and generators retain distinct stable peer identities under one supervisor.
- [ ] UI polling is a passive consumer of authoritative network state.

---

## Session Log

### 2026-08-23 - Create persistent network supervisor sprint

- What changed: created the sprint for an always-on control-plane supervisor, explicit network states, last-good topology, role-specific peer runtimes, publication ownership, evidence-based recovery, and deterministic cleanup.
- Why: architecture analysis confirmed a discovery lifecycle deadlock and showed that ambiguous Hivemind store results are currently used as stronger transport evidence than the dependency contract permits.
- Status: planning is complete and depends on Sprint 28; no runtime source changed in this session.
