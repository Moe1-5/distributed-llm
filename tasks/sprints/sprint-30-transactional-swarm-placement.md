# Sprint 30 - Transactional Swarm Placement

**Goal:** Replace snapshot-based Recommended placement with authoritative, expiring, atomic provider reservations.
**Start:** 2026-08-23
**End:** TBD
**Status:** Planned behind Sprints 28 and 29.

---

## Problem Summary

Sprint 17 can calculate a useful range from one topology snapshot, but a recommendation is not a reservation. Two devices can read the same revision and both start the same range before either advertisement becomes visible. Frontend freshness checks reduce misleading guidance but cannot make an eventually consistent DHT operation transactional.

For the current two-device product, placement correctness needs an explicit control-plane operation while Hivemind remains the tensor and reachability data plane.

## Dependencies and Boundaries

- Reuse Sprint 17's validated range and coverage algorithms as pure planning logic.
- Depend on Sprint 28 peer-addressed providers and Sprint 29 persistent network state.
- Keep the coordination service separate from settlement and useful-work accounting.
- Keep frontend presentation in Sprint 27; the renderer submits capacity and displays the backend decision.
- Do not add key/value-cache sessions or infrastructure redundancy; Sprints 31 and 32 own those concerns.

## Work Plan

- [ ] Add a separately deployed swarm coordination service with durable, revisioned lease state.
- [ ] Model provider placement as `RESERVED`, `JOINING`, `ONLINE`, `OFFLINE`, and `EXPIRED` states.
- [ ] Accept model revision and layer capacity and allocate a useful range atomically.
- [ ] Return a reservation token, topology revision, range, and expiry.
- [ ] Confirm `ONLINE` only after layers load and the exact peer-addressed expert passes readiness.
- [ ] Renew leases from authenticated backend heartbeats and expire abandoned starts.
- [ ] Route custom ranges through the same server-side conflict and revision validation.
- [ ] Cross-check coordinator ownership, Hivemind advertisement, and RPC readiness without using the UI as authority.
- [ ] Fail closed for new Recommended placement when coordination is unavailable while preserving documented behavior for existing online workers.
- [ ] Add signed or authenticated participant mutations, bounded clocks, idempotency, and audit-safe diagnostics.

## Test Plan

- Race two six-layer allocation requests against an empty twelve-layer model and prove they cannot both receive `0-6`.
- Prove sequential requests obtain complementary coverage and a completed route.
- Fail loading after reservation and prove the range becomes allocatable after release or expiry.
- Replay create, renew, online, and release mutations and prove idempotency.
- Present an old frontend revision and prove the backend rejects or replaces it safely.
- Restart participant and coordinator processes and prove lease ownership does not duplicate.
- Verify manual custom placement cannot bypass authoritative conflict validation.

## Acceptance Criteria

- [ ] Simultaneous capacity requests cannot receive conflicting exclusive reservations.
- [ ] Normal sequential requests produce complementary useful coverage.
- [ ] Failed or abandoned startup releases or expires its reservation.
- [ ] An old UI revision cannot commit stale placement.
- [ ] Coordinator, DHT advertisement, and RPC readiness are cross-validated.
- [ ] Restart recovery cannot create two active owners for one reservation.
- [ ] The frontend does not calculate placement correctness.

---

## Session Log

### 2026-08-23 - Create transactional placement sprint

- What changed: created the sprint for a separate authoritative coordinator, atomic range reservations, explicit provider lease states, authenticated heartbeats, and stale-revision protection.
- Why: physical testing proved that even a correctly labeled fresh recommendation remains a snapshot and cannot prevent concurrent devices from choosing the same missing range.
- Status: planning is complete and depends on Sprints 28 and 29; no service or runtime source changed in this session.
