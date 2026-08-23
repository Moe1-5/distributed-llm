# Sprint 32 - Infrastructure Redundancy And Architecture Acceptance

**Goal:** Remove the combined bootstrap, DHT-storage, and relay failure domain, then prove the revised architecture under controlled failures.
**Start:** 2026-08-23
**End:** TBD
**Status:** Source implementation complete; independent-host deployment and physical failure acceptance remain open.

---

## Problem Summary

Relay-mode participants run as DHT clients, while the current VPS combines bootstrap, full DHT storage, and circuit relay roles. It is therefore both the discovery storage dependency and the tensor-transport fallback. Process separation improves diagnosability, but host-level resilience additionally requires independent infrastructure peers. Provider redundancy must also be real: two complementary half-model workers prove distribution but cannot survive either worker failing.

## Dependencies and Boundaries

- Keep Sprint 16 responsible for the current single-relay baseline and operations runbook.
- Consume Sprints 29 and 30 for participant network and coordinator services and Sprint 31 for session inference.
- Reuse Sprint 21's bounded provider failover and Sprint 24's artifact identity evidence.
- Do not describe multiple processes on one VPS as host-level redundancy.
- Re-enable shadow incentives only after the base inference matrix passes with incentives off.

## Work Plan

- [ ] Deploy bootstrap/DHT, relay, coordinator, and settlement as separate service units with separate identities, ports, permissions, health, and logs.
- [ ] Add a second full DHT/bootstrap peer in an independent failure domain.
- [ ] Add an alternate relay before claiming relay fault tolerance.
- [ ] Configure participants with ordered, validated infrastructure peers and explicit degraded states.
- [x] Extend artifact and acceptance evidence to bind participant, coordinator, DHT, relay, and protocol revisions.
- [x] Add controlled coordinator, DHT, relay, worker, and generator failure-injection procedures.
- [ ] Test complementary split execution separately from a redundant three-provider topology.
- [x] Define recovery objectives for existing leases, new placement, in-flight work, and later requests.
- [ ] Complete incentives-off acceptance before shadow receipt and settlement evidence.

## Test Plan

- Restart each infrastructure service independently and verify other services retain correct state.
- Remove one DHT/bootstrap peer and verify provider discovery remains available through the other.
- Stop the active relay during one controlled request, classify the in-flight result safely, and complete a later request through the alternate.
- Stop the coordinator and verify existing leases follow the documented grace behavior while unsafe new placement fails closed.
- Run three providers with one complete alternate and inject a selected-worker failure.
- Complete direct, relay, complementary split, redundant failover, session, package, and shadow-accounting evidence in dependency order.

## Acceptance Criteria

- [ ] Restarting one infrastructure service cannot corrupt another service's state.
- [ ] Losing one DHT/bootstrap peer does not erase provider discovery.
- [ ] Relay loss stops ambiguous in-flight work safely and a later request can use an alternate relay.
- [ ] Coordinator outage preserves documented existing-lease behavior and rejects unsafe new placement.
- [ ] Three providers prove actual route redundancy and bounded failover.
- [ ] Bound direct, relay, split, redundant, session, package, and shadow evidence passes the final architecture matrix.

---

## Session Log

### 2026-08-23 - Create infrastructure and architecture acceptance sprint

- What changed: created the integration sprint for separated infrastructure roles, independent DHT and relay redundancy, failure injection, artifact binding, and the final dependency-ordered physical matrix.
- Why: the current VPS is simultaneously the only full DHT storage peer and relay fallback, while a two-worker complementary split has no provider redundancy.
- Status: planning is complete and depends on the preceding architecture sprints; no deployment or runtime source changed in this session.

### 2026-08-23 - Implement separated infrastructure and bound failure evidence

- What changed: split the pinned Hivemind runtime into full-DHT, non-storage relay, and legacy combined roles; added role-specific hardened systemd units, users, state, identities, environment templates, installers, restart/flag validation, and failure-domain status; isolated placement and settlement service accounts/state and added component/protocol/deployment health; exposed ordered DHT/relay redundancy configuration in the participant supervisor; added the architecture failure-matrix validator and bound it optionally into final Windows/VPS acceptance; documented migration, outage injection, complementary versus truly redundant provider tests, recovery objectives, evidence hashing, and incentives-off-before-shadow ordering.
- Why: the old public VPS stored the only DHT records and forwarded every relayed tensor stream in one process and host, while the previous two-provider split had no route capable of surviving either worker loss. Process roles, identities, revisions, and physical proof needed separate contracts before resilience could be claimed.
- Status: local source and validation tooling are implemented against Hivemind 1.1.12. All 448 backend tests pass, including real local Hivemind expert, session, and receipt RPC tests; shell syntax, Python compilation, JSON validation, diff checks, 20 launcher tests, 4 renderer-flow tests, frontend typechecking/build, and lint with zero errors also pass. The first three deployment/configuration items and every acceptance checkbox remain open until two independent DHT hosts, two independent relay hosts, two physical participants, and a three-provider redundant route execute the documented failure matrix.

### 2026-08-23 - Correct packaged participant configuration and rollout ordering

- What changed: clarified that the Electron launcher owns Bootstrap Peers, Trusted Relays, and Network Mode, so operators must configure ordered infrastructure lists in Settings rather than relying on overridden `.env` values; retained `.env` for incentives mode; and corrected the two-device evidence sequence to preserve an incentives-off relay report before repeating the capture in shadow mode.
- Why: the final physical procedure must match the actual EXE-only lifecycle and Sprint 32's fail-closed incentives-off-before-shadow requirement.
- Status: the runbooks now describe the implemented launcher boundary and rollout order. Independent-host deployment and every physical failure-matrix gate remain open.
