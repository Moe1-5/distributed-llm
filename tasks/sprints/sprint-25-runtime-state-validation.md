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
- [x] Cap exact local replicas at two and require confirmation for the second.
- [x] Remove blocking DHT scans from ordinary renderer refresh paths.
- [x] Surface staged, correlated provider and route diagnostics.

## Acceptance Criteria

- [x] Missing, unhealthy, or stale layers cannot produce a generator-ready result.
- [x] `/status`, `/generator/status`, Network, Nodes, Inference, and Monitoring agree on readiness.
- [x] Deleting the last required local provider unloads its generator after confirmation.
- [x] Remote route loss suspends inference without unloading reusable local components.
- [x] A healthy alternate route preserves generator readiness.
- [x] A second exact local replica requires confirmation and a third is rejected.
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
