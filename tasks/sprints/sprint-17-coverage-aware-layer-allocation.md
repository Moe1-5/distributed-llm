# Sprint 17 - Coverage-Aware Layer Allocation

**Goal:** Recommend useful model-layer ranges and select complete non-overlapping inference routes despite redundant or overlapping providers.
**Start:** 2026-08-12
**End:** TBD

---

## Done

- [x] Select a complete subset of spans using dynamic programming.
- [x] Prefer fewer hops and preserve exact-replica round robin.
- [x] Ignore unselected overlap while keeping it visible as standby capacity.
- [x] Add provider-count coverage segments, missing ranges, projected routes, and coverage revisions.
- [x] Add the serving-plan endpoint and stale/redundancy HTTP 409 responses.
- [x] Add Recommended and Custom allocation modes to Serve Layers.
- [x] Show exact route, missing ranges, and standby providers in Run Inference.
- [x] Add focused route, recommendation, and API conflict tests.

## Open

- [ ] Validate recommendation refresh and route selection with two independent devices through the VPS relay.
- [ ] Confirm the full-provider-plus-partial-provider scenario through real tensor RPC.

## Acceptance Criteria

- [x] Two identical `0-6` providers do not make a 12-layer model runnable.
- [x] `0-6` plus `6-12` forms a valid route.
- [x] `0-6` plus `0-12` selects the full provider and leaves the partial provider on standby.
- [x] A locally longer dead-end span does not hide a shorter complete path.
- [x] A user is warned before adding pure redundancy while gaps remain.
- [ ] The behavior is confirmed in the two-device acceptance topology.

## Session Log

### 2026-08-12 - Implement coverage-aware allocation and subset routing

- What changed: added pure coverage planning, dynamic route selection, serving-plan and node-start validation APIs, Network-tab recommendations, inference route visibility, and focused tests.
- Why: operators need to know which layers are useful before serving, and overlapping advertisements must not invalidate an otherwise complete model route.
- Status: automated backend and frontend checks pass; two-device relay validation remains open.

### 2026-08-12 - Verify coverage work on its feature branch

- What changed: placed the implementation on `feature/coverage-aware-incentives`, added the repository-wide purpose-matched feature-branch rule, and repeated backend and frontend verification.
- Why: coverage work must stay isolated from other active sprints until the two-device topology confirms it.
- Status: 149 backend tests plus 19 subtests, frontend type checks, lint, and production build pass; real relay route selection and full-provider standby behavior remain open.
