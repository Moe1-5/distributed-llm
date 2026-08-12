# Sprint 21 - Health-Aware Route Failover

**Goal:** Select healthy complete routes, retain bounded alternates, and recover from provider failure without duplicate execution, duplicate rewards, or unstable route switching.
**Start:** 2026-08-13
**End:** TBD
**Status:** Implemented and locally verified; physical relay failure injection remains open.

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

- [x] Review and approve route ranking, retry boundaries, and receipt semantics.
- [x] Extend route planning to produce health-aware active and alternate complete routes.
- [x] Add revisioned route caching and invalidation.
- [x] Add bounded generation-attempt orchestration and deterministic backoff.
- [x] Integrate request/session/route IDs and useful-work receipt cleanup across attempts.
- [x] Expose failover state and reasons in API responses, traces, and Monitoring.
- [x] Add local multi-peer failure injection and two-device acceptance procedures.
- [x] Document limitations before distributed session and key/value cache support.

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

### Two-device relay failure injection

1. Run the managed VPS bootstrap relay and configure both devices with the same bootstrap address, trusted relay, DHT prefix, model revision, and `DISTRIBLLM_NETWORK_MODE=relay`.
2. On each device, start one full-range provider for the same small acceptance model. Wait until Monitoring shows one active complete route and one complete alternate, both with verified relay transport and healthy RPC state.
3. Start the generator on device one and request enough output to keep multiple forward steps active. Record `/generator/status` before injection.
4. Identify the device hosting the active provider from Monitoring. On that device, call `POST /node/turn-off?node_id=<ACTIVE_NODE_ID>` while generation is active. Do not stop the alternate.
5. The current forward may either complete on the active route or, only after a pre-execution failure, restart on the alternate. An ambiguous in-flight reset must stop without replay. Start one more generation if the shutdown landed after the prior forward completed.
6. Capture `/generator/status`, the completed stream payload, both backend logs, and the VPS relay log. Passing evidence shows `failed_over=true`, at most the configured route-attempt count, an accepted alternate trace, no failed-attempt settlement, and no unbounded retry loop.
7. Turn the provider back on, wait for the configured recovery successes, and confirm it moves from offline or degraded to alternate/eligible without restarting the generator.

## Acceptance Criteria

- [x] Routing returns a healthy complete active route and deterministic ordered alternates when they exist.
- [x] A provider failure can move a later safe attempt to an alternate without duplicate layer execution inside one attempt.
- [x] Retry count, timeout, and backoff are strictly bounded by configuration.
- [x] Only providers in the accepted complete attempt can produce settled useful-work entries.
- [x] Route and health revisions make stale client state detectable.
- [x] The UI distinguishes active, standby, alternate, degraded, failed-over, and unavailable providers.
- [ ] Two-device evidence proves one controlled provider failure and successful alternate completion through the project VPS relay.

---

## Session Log

### 2026-08-13 - Create proposal for health-aware failover

- What changed: created a review-ready sprint plan for health-aware route ranking, complete alternates, bounded retries, receipt safety, visibility, and failure injection.
- Why: automatic failover must build on trustworthy health and bounded RPC behavior without paying for uncertain or duplicate work.
- Status: proposal only. No planner or generation retry behavior changed; implementation awaits user approval.

### 2026-08-13 - Implement bounded health-aware route failover

- What changed: added a bounded dynamic-programming route planner that ranks healthy direct, relay, hop count, latency, and deterministic identity; retained complete alternates; added health and coverage route revisions; and made session route reuse revision-aware.
- Failure semantics: a known pre-execution transport failure quarantines the provider and restarts the full forward from the original activation tensor with a fresh request ID. Ambiguous or post-dispatch outcomes stop without failover. No route is changed inside a hop chain.
- Incentive safety: pending receipts from failed attempts are discarded and only the accepted complete attempt is submitted.
- Visibility: generator readiness and Monitoring expose active, alternate, standby, degraded, transport, revision, attempt-count, and failover-reason state.
- Verification: focused planner, health, useful-work, and failure-injection suites pass. The physical two-device relay failure-injection criterion remains open and this sprint stays active until the user explicitly closes it.

### 2026-08-13 - Prove failover with real local Hivemind processes

- What changed: added `local_failover_probe.py`, which starts two independent full-range expert processes, caches both routes in one generation session, turns off the selected expert, and requires the replacement forward to restart from the original activation through the complete replica.
- Result: the OPT-125M CPU pass completed in 4.858 seconds. The stopped provider failed with a classified pre-execution dial error, route attempt two selected the other peer, and the output tensor matched exactly.
- Accounting and cleanup: each worker recorded one successful four-position request and zero failed requests; both nodes, the client DHT, cached remote-expert P2P client, and bootstrap stopped successfully.
- Remaining gate: this proves process-level failover over loopback. The physical two-device failure injection through the project VPS relay remains required.
