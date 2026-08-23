# Sprint 30 - Transactional Swarm Placement

**Goal:** Replace snapshot-based Recommended placement with authoritative, expiring, atomic provider reservations.
**Start:** 2026-08-23
**End:** TBD
**Status:** Implemented in source; physical two-device coordinator rollout and acceptance remain open.

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

- [x] Add a separately deployed swarm coordination service with durable, revisioned lease state.
- [x] Model provider placement as `RESERVED`, `JOINING`, `ONLINE`, `OFFLINE`, and `EXPIRED` states.
- [x] Accept model revision and layer capacity and allocate a useful range atomically.
- [x] Return a reservation token, topology revision, range, and expiry.
- [x] Confirm `ONLINE` only after layers load and the exact peer-addressed expert passes readiness.
- [x] Renew leases from authenticated backend heartbeats and expire abandoned starts.
- [x] Route custom ranges through the same server-side conflict and revision validation.
- [x] Cross-check coordinator ownership, Hivemind advertisement, and RPC readiness without using the UI as authority.
- [x] Fail closed for new Recommended placement when coordination is unavailable while preserving documented behavior for existing online workers.
- [x] Add signed or authenticated participant mutations, bounded clocks, idempotency, and audit-safe diagnostics.

## Test Plan

- Race two six-layer allocation requests against an empty twelve-layer model and prove they cannot both receive `0-6`.
- Prove sequential requests obtain complementary coverage and a completed route.
- Fail loading after reservation and prove the range becomes allocatable after release or expiry.
- Replay create, renew, online, and release mutations and prove idempotency.
- Present an old frontend revision and prove the backend rejects or replaces it safely.
- Restart participant and coordinator processes and prove lease ownership does not duplicate.
- Verify manual custom placement cannot bypass authoritative conflict validation.

## Acceptance Criteria

- [x] Simultaneous capacity requests cannot receive conflicting exclusive reservations.
- [x] Normal sequential requests produce complementary useful coverage.
- [x] Failed or abandoned startup releases or expires its reservation.
- [x] An old UI revision cannot commit stale placement.
- [x] Coordinator, DHT advertisement, and RPC readiness are cross-validated.
- [x] Restart recovery cannot create two active owners for one reservation.
- [x] The frontend does not calculate placement correctness.

Source acceptance is complete. Physical acceptance still requires deploying the
coordinator, configuring the same pinned placement revision on both packaged
participants, racing two six-layer starts, and observing heartbeat expiry and
coordinator restart behavior on the real relay topology.

---

## Session Log

### 2026-08-23 - Create transactional placement sprint

- What changed: created the sprint for a separate authoritative coordinator, atomic range reservations, explicit provider lease states, authenticated heartbeats, and stale-revision protection.
- Why: physical testing proved that even a correctly labeled fresh recommendation remains a snapshot and cannot prevent concurrent devices from choosing the same missing range.
- Status: planning is complete and depends on Sprints 28 and 29; no service or runtime source changed in this session.

### 2026-08-23 - Implement and verify authoritative placement

- What changed: added the authenticated SQLite placement service and VPS unit; atomic Recommended and Custom reservations; explicit lease states, tokens, revisions, expiry, idempotency, and safe audit output; backend reserve, joining, online, heartbeat, release, and fail-closed lifecycle integration; exact DHT and RPC readiness attestation; generator placement-revision filtering; renderer authority/capacity handling; deployment documentation; and source regressions for concurrency, complementarity, stale revisions, failed starts, replay, restart, authentication, outages, and secret redaction.
- Why: eventually consistent DHT snapshots cannot prevent two participants from selecting the same missing range, and a worker must stop before an expired lease can be reassigned after a coordinator outage.
- Status: all source acceptance criteria pass. The complete backend suite passes 419 tests; the focused placement suite passes 33 tests; renderer flow passes 4 tests; launcher passes 20 tests; frontend lint, typecheck/build, deployment shell syntax, and diff checks pass. Physical two-device deployment, allocation race, expiry, and restart evidence remain open, so the sprint stays active and is not archived.
