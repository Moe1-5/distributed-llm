# Sprint 03 - Routing and DHT Hardening

**Goal:** Make distributed inference reject unsafe DHT/RPC state before generation, so each request uses the right model, a valid contiguous route, and unique reachable RPC targets.
**Start:** 2026-07-02  
**End:** TBD

---

## Problem Summary

The backend flow audit found that the local generation path is healthier than before, but the distributed boundary still trusts network metadata too much. DHT node records can contain wrong models, malformed layer ranges, invalid negative layers, or colliding RPC UIDs. The `/nodes` endpoint also ignores custom DHT prefixes, and important runtime validation still depends on `assert`.

The sprint should turn those findings into tested implementation work without changing the architecture goal: bootstrap nodes remain discovery-only, serving nodes provide model layers, and the generator validates a full route before inference.

---

## In Progress

- [x] Confirm the exact model-aware route validation API and where the selected model should be stored.

## Todo

- [x] Add regression tests for the audit findings before implementation where practical:
  - wrong-model node metadata is rejected
  - non-integer layer metadata is skipped or rejected without crashing status
  - negative layer ranges never count as valid coverage
  - multi-node RPC UIDs are unique under one DHT prefix
  - `/nodes` uses the active node/generator prefix
  - optimized Python mode does not bypass critical runtime validation
- [x] Add a DHT node metadata validator for discovered records.
- [x] Make `RemoteSequential` model-aware and reject nodes whose `model_name` does not match the generator model.
- [x] Ensure coverage and route planning only use validated layer ranges.
- [x] Generate unique Hivemind-compatible RPC UIDs per node or layer slice.
- [x] Track the active DHT prefix for node/generator startup and use it in status/discovery endpoints.
- [x] Replace critical runtime `assert` checks with explicit `ValueError`, `RuntimeError`, or `HTTPException` paths.
- [x] Update backend docs or issue notes if any behavior contract changes.

## Done

- [x] Completed the initial backend flow audit and recorded findings in `ISSUES.md`.
- [x] Created this sprint plan from the audit findings.

---

## Acceptance Criteria

- [x] Existing backend regression tests pass.
- [x] New routing/DHT hardening regression tests pass.
- [x] Bad DHT metadata cannot crash `/nodes`, network status, readiness, or route validation.
- [x] A generator cannot route through nodes serving a different model.
- [x] Multi-node serving under one prefix produces unique RPC UIDs.
- [x] Custom DHT prefixes are reflected in `/nodes` discovery.
- [x] Critical validation still runs when Python assertions are disabled.

---

## Session Log

### 2026-07-02 - Plan routing and DHT hardening sprint

- What changed: created Sprint 03 to turn the backend flow audit findings into a focused implementation plan.
- Why: the audit found distributed-boundary issues around DHT metadata, model-aware routing, RPC UID uniqueness, active prefix tracking, and assert-based validation.
- Status: sprint plan is ready; implementation has not started.

### 2026-07-02 - Clarify archived sprint documentation

- What changed: added a completed-sprints section to the active sprint routing file and clarified the project index lookup path for archived sprint history.
- Why: Sprint 02 was documented in the archive, but the routing docs made it easy to miss.
- Status: Sprint 02 is now visibly listed as completed and archived from the sprint routing file.

### 2026-07-02 - Mirror Sprint 02 into the sprints folder

- What changed: added the completed Sprint 02 document to the main `tasks/sprints/` sequence and updated sprint routing plus the project index to point to it.
- Why: the user expected completed sprint documents to be visible in the sprints folder alongside Sprint 01 and Sprint 03.
- Status: `tasks/sprints/` now contains Sprint 01, Sprint 02, and Sprint 03.

### 2026-07-03 - Implement routing and DHT hardening phase

- What changed: added routing/DHT regression tests, model-aware DHT metadata validation, validated-route coverage, unique RPC UID generation per layer slice, active-prefix discovery for `/nodes`, and explicit runtime exceptions in the touched backend paths.
- Why: Sprint 03 audit findings showed the distributed boundary could trust wrong-model nodes, malformed layer ranges, duplicate RPC UIDs, stale prefixes, and assert-based checks.
- Status: backend regression suite passes with 20 tests; full live multi-node P2P behavior still needs a manual/system run before closing the sprint.
