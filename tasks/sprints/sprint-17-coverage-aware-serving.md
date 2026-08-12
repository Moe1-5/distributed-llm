# Sprint 17 - Coverage-Aware Serving

**Goal:** Select executable adjacent layer routes despite overlapping advertisements, recommend useful serving ranges from live coverage, and make route gaps actionable in the serving and inference tabs.
**Start:** 2026-08-12
**End:** TBD

---

## Problem Summary

The current route planner groups replicas and then requires every advertised span to form one globally ordered non-overlapping chain. An overlapping partial provider can therefore invalidate a complete route that should simply ignore it. The serving form also asks participants to guess a layer range, so several users can duplicate `0-6` while `6-12` remains missing.

Ranges remain half-open: `0-6` serves six layers and connects exactly to `6-12`.

## In Progress

- [x] Replace overlap-sensitive routing with deterministic adjacent-range dynamic programming.
- [x] Add a revisioned serving-plan API with coverage segments, exact gaps, recommendation scoring, selected route, and standby providers.
- [x] Revalidate starts and require confirmation for unnecessary redundancy while gaps remain.
- [x] Add Recommended and Custom serving modes with a layer-count control and compact coverage strip.
- [x] Show executable routes and exact missing requirements in Run Inference.
- [x] Add a Serve missing range action that preselects the useful recommendation.

## Todo

- [x] Preserve round-robin selection among exact range replicas.
- [x] Prefer fewer route hops, then deterministic range ordering.
- [x] Verify full-range providers remain usable beside overlapping partial providers.
- [x] Verify a shorter range is selected when a longer local choice leads to a dead end.
- [x] Verify recommendation ranking, capacity bounds, stale revisions, and redundancy confirmation.
- [x] Run backend tests, frontend type checks, lint, production build, and focused UI workflow checks.
- [ ] Run the two-device relay acceptance test after Sprint 16's live deployment gate.

## Acceptance Criteria

- [x] `0-6` alone reports an exact `6-12` requirement for a twelve-layer model.
- [x] Two `0-6` replicas remain incomplete and rotate only when that span is selected.
- [x] `0-6` plus `6-12` produces a two-provider route.
- [x] `0-6` plus `0-12` selects `0-12` and marks `0-6` standby.
- [x] A stale recommendation returns HTTP 409 with a fresh plan.
- [x] A redundant custom range while gaps remain requires explicit confirmation.
- [x] Existing node-start clients remain valid when optional revision fields are omitted.
- [x] The UI explains whether a recommendation adds coverage, completes a route, or adds redundancy.

---

## Session Log

### 2026-08-12 - Start approved coverage-aware serving prerequisite

- What changed: created Sprint 17 on `feature/coverage-aware-serving` after the user approved the detailed coverage-aware serving and useful-work incentives plan.
- Why: useful-work rewards must follow an actually selected complete route, and participants need live guidance about which layers are useful before receipt accounting can be trusted.
- Status: implementation started; source, API, UI, regression, and device acceptance work remain open.

### 2026-08-12 - Complete implementation and local acceptance

- What changed: added pure adjacent-range route and coverage analysis, selected-versus-standby classification, provider-density segments, deterministic coverage revisions, capacity-aware recommendations, a serving-plan API, stale-plan and redundancy conflicts, Recommended and Custom serving modes, route visibility, and the Serve missing range workflow.
- Why: overlapping providers must not break a complete route, participants need useful range guidance instead of guessing, and future incentives need an authoritative selected route rather than rewarding advertisements.
- Verification: 139 backend tests plus 19 subtests and 14 launcher tests pass; changed Python files compile; changed renderer files pass ESLint; frontend type checks and the production Electron build pass; `git diff --check` is clean. The built Electron UI was exercised against the live local API at 1280 by 800 and 900 by 600 with no document overflow. Run Inference reported the exact `0-12` gap, and Serve missing range returned to Recommended mode with the same model, capacity 12, and a `0-12` route-completing recommendation.
- Status: implementation and local acceptance are complete on `feature/coverage-aware-serving`. The two-device relay validation remains open behind Sprint 16's live VPS gate, so the sprint stays active until the user approves that external evidence.
