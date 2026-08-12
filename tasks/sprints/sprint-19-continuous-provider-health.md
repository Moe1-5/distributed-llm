# Sprint 19 - Continuous Provider Health

**Goal:** Maintain trustworthy, continuously refreshed provider health and invalidate stale route readiness before a generation request reaches a dead or degraded peer.
**Start:** 2026-08-13
**End:** TBD
**Status:** Implemented and locally verified on `feature/fundamental-live-stability`; retained as active until explicit sprint closure.

---

## Problem Summary

The generator currently validates its selected experts when a route is created, while DHT advertisements expire and refresh independently. Local serving counters record request successes and failures, but there is no generator-side health lifecycle that says whether an advertised provider remains usable after initial selection. A route can therefore look complete after one of its providers has become unreachable.

This sprint creates health truth and readiness invalidation. Automatic route switching remains in Sprint 21 so health semantics can be tested independently before they control retries.

## Dependencies and Boundaries

- Build on Sprint 17's provider identity, selected route, standby classification, and coverage revision.
- Reuse the existing expert metadata probe where possible; do not run model inference merely to prove liveness.
- Treat DHT expiry, protocol compatibility, transport verification, and RPC probe results as distinct signals.
- Keep probe intervals, timeouts, thresholds, and recovery behavior configurable through typed settings.
- Do not add route failover, distributed key/value cache, API keys, or peer reputation in this sprint.

## Implemented Design

- Add a provider health registry keyed by peer ID, RPC UID, model/revision, and served range.
- Track `checking`, `healthy`, `degraded`, and `offline` states plus consecutive outcomes, last probe/success/failure, latency, reason, and next probe time.
- Start a background monitor with the generator/client lifecycle and shut it down before closing the DHT or cached P2P client.
- Probe selected providers more frequently than standbys while applying jitter and bounded concurrency.
- Mark readiness stale only after a configurable failure threshold; recover after a configurable number of successful probes.
- Publish a health revision whenever route-relevant state changes so API clients can distinguish fresh and stale route views.
- Extend readiness, route, and Monitoring responses with optional health fields to preserve older clients.

## Work Plan

- [x] Review and approve health state transitions, thresholds, and API fields.
- [x] Define typed health configuration and safe defaults in the existing env/constants system.
- [x] Implement the pure provider health state machine and revision calculation.
- [x] Add lifecycle-owned background probing with bounded concurrency and jitter.
- [x] Connect health state to route readiness invalidation without selecting a replacement route.
- [x] Expose selected and standby provider health, latency, timestamps, and failure reasons.
- [x] Add Monitoring states that clearly separate DHT presence, transport verification, and RPC health.
- [x] Document tuning, lifecycle, and troubleshooting behavior.

## Test Plan

- State-machine tests cover initial checks, intermittent failure, threshold degradation, offline state, and recovery.
- Fake-clock tests prove expiry and probe timing without slow sleeps.
- Lifecycle tests prove monitor tasks stop before DHT/P2P teardown and do not leak across generator resets.
- Readiness becomes unavailable after the configured threshold and returns only after recovery criteria pass.
- Malformed or protocol-incompatible advertisements remain distinct from unreachable providers.
- Selected and standby probe scheduling respects priority, concurrency, and jitter bounds.
- Backend API regressions and frontend type/build checks cover backward-compatible optional fields.

## Acceptance Criteria

- [x] Every provider used in a route has a current, inspectable generator-side health state.
- [x] A dead selected provider invalidates readiness within the configured detection window.
- [x] A transient single failure does not cause route flapping under the default threshold.
- [x] Recovery is automatic and requires the configured successful-probe evidence.
- [x] DHT presence is never presented as equivalent to successful expert RPC health.
- [x] Generator reset and backend shutdown leave no health-monitor task or cached transport client running.
- [x] No automatic failover occurs yet; Sprint 21 remains the sole owner of route switching.

---

## Session Log

### 2026-08-13 - Create proposal for continuous provider health

- What changed: created a review-ready sprint plan for provider health states, lifecycle-owned probes, route-readiness invalidation, and Monitoring visibility.
- Why: one-time route validation cannot show when an advertised provider becomes unreachable after selection.
- Status: proposal only. No health monitor or API behavior changed; implementation awaits user approval.

### 2026-08-13 - Implement continuous health and stale-readiness invalidation

- What changed: added a thread-safe provider registry keyed by peer, RPC UID, model revision, and half-open range; typed checking/healthy/degraded/offline transitions; DHT/protocol/transport signals; stable health revisions; and a lifecycle-owned daemon monitor using bounded metadata probes.
- Scheduling: selected providers default to five-second probes, standbys to fifteen seconds, DHT discovery to two seconds, and concurrency to four. Jitter, timeouts, thresholds, staleness, recovery, scheduler cadence, and discovery cadence are validated environment settings.
- Readiness: generator status now consumes the cached health snapshot instead of blocking on a new metadata RPC each poll. One failure preserves healthy state, two failures degrade, four mark offline, and recovery requires two successes. A degraded selected route is rejected even when a healthy standby exists; Sprint 21 still owns replacement selection.
- Lifecycle and UX: Network now has a real generator unload path, while Chat retains request cancellation. Unload stops the health monitor before cached P2P/DHT shutdown. Monitoring shows DHT presence, transport verification, RPC state, latency, and separate selective-loading diagnostics.
- Verification: 12 focused health tests pass, including cancellation of timed-out Hivemind lookup and RPC-info futures; full backend passes 216 tests plus 26 subtests; Python compilation, frontend type checks, and production build pass.
- Remaining: live two-device timing will validate the default detection window under the VPS relay. The sprint remains active until explicit closure.
