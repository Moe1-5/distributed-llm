# Sprint 28 - Peer-Addressed Expert Protocol

**Goal:** Ensure every routed expert RPC executes on the selected peer and allow same-range providers to coexist without shared-UID overwrite.
**Start:** 2026-08-23
**End:** TBD
**Status:** Implemented locally; physical two-device rollout validation remains open.

---

## Problem Summary

The route planner selects a provider by peer ID, model revision, and layer range, but the forward path resolves a shared expert UID again before dispatch. Two independent providers serving the same range can publish that same UID, so the DHT value can change after route selection and the RPC can execute on a different peer from the one shown in readiness, Monitoring, diagnostics, or useful-work ownership.

Hivemind is declared with a lower-bounded dependency even though the implementation depends on version-specific and private behavior. The installed and locked version is Hivemind 1.1.12, so the protocol must be implemented and tested against that exact dependency rather than an unspecified future release.

## Dependencies and Boundaries

- Build on Sprint 17's adjacent-range route planner and persistent worker identities from Sprint 25.
- Preserve Sprint 20's RPC admission and resource-safety rules.
- Keep stateless payload and relay-stream diagnosis in Sprint 22.
- Do not add persistent network ownership, transactional placement, or session key/value caching; Sprints 29 through 31 own those concerns.
- Treat normal and receipt experts as two schemas that must both preserve exact peer ownership.

## Work Plan

- [x] Pin Hivemind exactly to 1.1.12 in project metadata and the lockfile, and document the compatibility boundary.
- [x] Version the provider and expert-ownership metadata contract.
- [x] Give every normal and receipt expert a globally peer-unique RPC UID derived from stable worker identity.
- [x] Construct remote experts from the route's exact peer ID and RPC UID instead of rediscovering a shared UID.
- [x] Reject route metadata when provider peer identity and expert ownership disagree.
- [x] Record the selected peer and execution-bound peer in client traces and diagnostics, preserve provider ownership in worker counters, and independently verify the receipt signer on receipt RPCs.
- [x] Define an explicit compatibility policy for legacy ambiguous advertisements.
- [x] Add duplicate-range, concurrent-publication, complementary-route, and receipt-path regressions using the installed Hivemind code.

## Test Plan

- Start two real Hivemind peers serving the same model revision and exact range, select each one deterministically, and assert that each RPC reaches only the selected peer.
- Republish one peer while a route for the other remains cached and prove execution ownership cannot change.
- Reject mismatched peer and RPC ownership before tensor dispatch.
- Preserve the existing real local `0-6` plus `6-12` split parity result.
- Preserve exact-range alternates for Sprint 21 without allowing an alternate to replace a peer inside one attempt.
- Run receipt-mode integration and prove the receipt signer matches the executed route owner.

## Acceptance Criteria

- [x] Two peers can advertise the same model and range without overwriting one another's expert identity.
- [x] Selecting peer A always invokes peer A while peer B publishes concurrently.
- [x] Normal RPC evidence shows the selected and peer-bound executor match by construction, while receipt RPC evidence independently verifies the worker signer.
- [x] A peer/RPC ownership mismatch fails before tensor dispatch.
- [x] Complementary routes and exact-range alternates remain valid.
- [x] Tests exercise the installed Hivemind 1.1.12 implementation.

---

## Session Log

### 2026-08-23 - Create peer-addressed protocol sprint

- What changed: created the first architecture-program sprint for exact dependency pinning, peer-unique expert ownership, peer-pinned dispatch, compatibility handling, and execution-attribution evidence.
- Why: source and Hivemind 1.1.12 analysis proved that a selected route peer can differ from the peer resolved through a shared expert UID when same-range providers publish concurrently.
- Status: planning is complete; implementation belongs on the dedicated Sprint 28 feature branch and no runtime source changed in this session.

### 2026-08-23 - Implement and locally accept peer-addressed RPC

- What changed: pinned Hivemind 1.1.12; introduced version-two peer-scoped normal and receipt UIDs; advertised explicit RPC ownership; replaced UID-only expert discovery in health, readiness, normal forwarding, and receipt forwarding with exact peer construction; exposed selected and executed peers in generation metrics and Monitoring; updated probes and compatibility documentation; and added real-Hivemind duplicate-range coverage.
- Why: route selection was authoritative only until dispatch, where resolving a shared UID could silently substitute another same-range provider. The versioned UID and direct peer binding make the executor an invariant of the selected route.
- Status: 46 focused protocol/failover tests pass, including real same-range providers, cached peer A execution while peer B republishes, typed missing-expert preflight, safe alternate selection, missing-receipt fallback, and signed receipt RPC. Real local OPT-125M complementary `0-6` plus `6-12` generation matches direct output, and real exact-range alternate failover passes; both probes clean up their temporary identities and processes. Python compilation, the exact lock check, frontend type checking/build, and renderer-flow tests pass. A packaged two-device direct/relay rollout remains required and Sprint 22 still owns any sustained relay stream reset.
