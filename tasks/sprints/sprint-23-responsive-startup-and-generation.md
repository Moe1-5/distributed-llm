# Sprint 23 - Responsive Startup and Generation

**Goal:** Keep the desktop responsive during backend startup, model loading, route checks, and inference while removing avoidable repeated work from the generation path.
**Start:** 2026-08-13
**End:** TBD
**Status:** In progress; local implementation and build checks pass, visual and device acceptance remain.

---

## Problem Summary

Live screenshots show Network and Nodes views remaining in blank skeleton states while backend calls are slow. Generator start is one long HTTP request that includes DHT startup and model loading. Frontend polling has no shared timeout or overlap guard, Monitoring waits for every request in one `Promise.all`, and autoregressive generation re-discovers and revalidates the route while recomputing the full sequence for each token.

The app must communicate real progress, return control quickly, and avoid preventable network work. Responsiveness is measured separately from model compute throughput so asynchronous UI behavior does not hide a slow inference engine.

## Dependencies and Boundaries

- Land Sprint 22's bounded transport contract first.
- Reuse Sprint 18 selective loading for worker memory/startup improvements when available.
- Preserve current synchronous endpoints for compatible clients while adding job-oriented behavior.
- Do not claim key/value-cache speedups until architecture adapters preserve Hugging Face semantics with cached state.

## Work Plan

- [x] Add lifecycle-owned startup jobs for nodes and generators with queued, networking, loading, validating, ready, failed, and cancelled states.
- [x] Return an accepted response promptly and expose job progress, elapsed time, stage detail, and cancellation.
- [x] Add API request deadlines, abort handling, and polling overlap guards in the desktop client.
- [x] Render partial Monitoring data and a fast local model catalog instead of waiting for every DHT scan.
- [x] Reuse one validated route snapshot throughout a generation request.
- [ ] Cache reusable local model components within the existing lifecycle and expose cold-versus-warm timing.
- [ ] Establish measured cold start, warm start, first-token, per-token, and cancellation baselines.
- [ ] Plan architecture-aware distributed key/value caching as a separate sprint if full-context recomputation remains the dominant cost.

## Test Plan

- Slow fake startup proves start APIs return promptly and progress remains queryable.
- Cancellation during networking and model loading leaves no DHT, model, RPC, or background-task leak.
- Frontend requests time out and abort on unmount; polling never overlaps the same endpoint.
- One failed Monitoring request does not discard successful stats, node, or model results.
- Route lookup and validation counts remain bounded to one snapshot per generation request.
- Backend tests, frontend type checks, renderer build, and packaged launcher tests pass.

## Acceptance Criteria

- [ ] Network, Nodes, and Monitoring show useful partial state within two seconds of backend availability.
- [x] Starting a node or generator does not block the initiating HTTP request for model-load duration.
- [x] Every node/generator start exposes progress and a cancellation request path.
- [x] Frontend polling is deadline-bound and non-overlapping.
- [x] Generation avoids repeated route discovery for each token.
- [ ] Cold and warm performance evidence identifies remaining model-compute limits honestly.

---

## Session Log

### 2026-08-13 - Create responsive startup and generation sprint

- What changed: created the sprint for startup jobs, progress, cancellation, frontend deadlines, partial rendering, route reuse, and measured performance.
- Why: live pages remain in long skeleton states and the current request/generation lifecycle repeats avoidable work.
- Status: approved and queued behind Sprint 22 so responsiveness builds on a stable RPC contract.

### 2026-08-13 - Add startup jobs and non-blocking desktop data flow

- What changed: added deduplicated lifecycle jobs and cancellation endpoints, moved DHT-heavy status work off the FastAPI event loop, added a fast model catalog, applied frontend request deadlines and polling guards, preserved partial Monitoring responses, and exposed cancel-start controls with live stages.
- Why: one slow DHT/model operation previously held the initiating request and could leave Network, Nodes, or Monitoring in blank loading states.
- Status: lifecycle tests, 195 backend tests plus 22 subtests, frontend type checks, production build, and 17 launcher tests pass. Browser timing/visual acceptance, cold/warm model timing, reusable component caching, and distributed key/value caching remain open.
