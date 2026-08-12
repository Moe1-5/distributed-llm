# Sprint 21 - Health-Aware Route Failover

**Goal:** Select healthy complete routes, retain bounded alternates, and recover from provider failure without duplicate execution, duplicate rewards, or unstable route switching.
**Start:** 2026-08-13
**End:** TBD
**Status:** Proposed for user review; implementation has not started.

---

## Problem Summary

Sprint 17 can find a complete adjacent route and classify standby providers, but generation currently has no automatic replacement when a selected provider disappears. Blind retry is unsafe: a request may have executed remotely even when the client did not receive a response, and repeating it could create duplicate useful-work claims or inconsistent partial execution.

This sprint consumes trustworthy provider health from Sprint 19 and bounded RPC behavior from Sprint 20. It does not introduce remote key/value cache recovery; each generation attempt continues to use the current stateless full-sequence forward behavior.

## Dependencies and Boundaries

- Sprint 17 coverage-aware route planning is complete locally.
- Sprint 19 provider health state and health revisions are accepted.
- Sprint 20 RPC admission, timeout, and cancellation behavior is accepted.
- Prefer healthy verified-direct transport, then healthy relay transport, while preserving complete-route correctness.
- Keep retries bounded and define explicit response-certainty and receipt rules.
- Do not add distributed key/value cache, session migration, token billing, or cross-request reputation in this sprint.

## Proposed Design

- Extend the pure route planner to consume an immutable provider-health snapshot and return one active complete route plus ordered complete alternates.
- Rank routes by health eligibility, verified direct transport, fewer relayed hops, fewer total hops, measured latency, then deterministic range ordering.
- Keep a route stable for one forward attempt; never change providers in the middle of a hop chain.
- Retry only at an explicitly safe attempt boundary, with a new attempt/request identity where required and a strict retry/backoff budget.
- Discard pending receipt material from failed attempts and settle only the accepted complete attempt's selected providers.
- Invalidate cached route choices when coverage or health revision changes.
- Expose active route, alternates, attempt count, transport choice, and failover reason in readiness, trace, and Monitoring views.
- When no healthy complete alternate exists, stop cleanly and report the exact failed and missing spans.

## Work Plan

- [ ] Review and approve route ranking, retry boundaries, and receipt semantics.
- [ ] Extend route planning to produce health-aware active and alternate complete routes.
- [ ] Add revisioned route caching and invalidation.
- [ ] Add bounded generation-attempt orchestration and deterministic backoff.
- [ ] Integrate request/session/route IDs and useful-work receipt cleanup across attempts.
- [ ] Expose failover state and reasons in API responses, traces, and Monitoring.
- [ ] Add local multi-peer failure injection and two-device acceptance procedures.
- [ ] Document limitations before distributed session and key/value cache support.

## Test Plan

- The selected provider dies before a request and an exact-range healthy replica takes over.
- A complete alternate route replaces a failed multi-hop route without mixing attempts.
- A healthy direct route is preferred to an otherwise equivalent relayed route.
- A degraded route is used only according to the approved eligibility policy.
- No alternate route produces one actionable terminal error with the exact unavailable span.
- Retry limits and backoff prevent loops and request storms during correlated failures.
- Failed, uncertain, cancelled, and standby providers receive no settled useful-work credit.
- Coverage and health revision changes invalidate cached choices while stable snapshots remain deterministic.
- Real local Hivemind tests and a two-device relay failure-injection run validate transport behavior.

## Acceptance Criteria

- [ ] Routing returns a healthy complete active route and deterministic ordered alternates when they exist.
- [ ] A provider failure can move a later safe attempt to an alternate without duplicate layer execution inside one attempt.
- [ ] Retry count, timeout, and backoff are strictly bounded by configuration.
- [ ] Only providers in the accepted complete attempt can produce settled useful-work entries.
- [ ] Route and health revisions make stale client state detectable.
- [ ] The UI distinguishes active, standby, alternate, degraded, failed-over, and unavailable providers.
- [ ] Two-device evidence proves one controlled provider failure and successful alternate completion through the project VPS relay.

---

## Session Log

### 2026-08-13 - Create proposal for health-aware failover

- What changed: created a review-ready sprint plan for health-aware route ranking, complete alternates, bounded retries, receipt safety, visibility, and failure injection.
- Why: automatic failover must build on trustworthy health and bounded RPC behavior without paying for uncertain or duplicate work.
- Status: proposal only. No planner or generation retry behavior changed; implementation awaits user approval.
