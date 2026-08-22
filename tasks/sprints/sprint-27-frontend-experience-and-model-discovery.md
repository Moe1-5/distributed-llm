# Sprint 27 - Frontend Experience And Model Discovery

**Goal:** Make the desktop application clearly explain what can run remotely, what is served locally, which runtime stage is healthy or failed, and what the user should do next.
**Start:** TBD
**End:** TBD
**Status:** Proposed from physical two-device usability evidence; implementation has not started.

## Problem Summary

The current Network workflow mixes two different roles: contributing model layers and using remote providers for generation. A generator-only device can show `Needs layers 0-12` even when the authoritative generator DHT later discovers a complete remote route. The model selector also does not clearly distinguish a locally installed model from a model that can be generated through remote providers.

Physical relay testing exposed contradictory Inference states. The header showed `Generator: READY` and `Route: READY` while the conversation retained `Waiting for generator route`. After the real forward failed, the WebSocket or stream state changed independently without a single authoritative explanation. The Trace action then displayed an unrelated generic eight-second HTTP timeout. Source review showed that Trace uses the legacy expert, while shadow-mode chat starts a useful-work session and uses the receipt expert, so the UI currently presents unlike diagnostics as though they were equivalent.

Users cannot currently answer these basic questions from the interface:

1. Can this model be generated now using remote providers?
2. Am I serving this model locally, using it remotely, or both?
3. Is the displayed coverage provisional discovery data or an authoritative validated route?
4. Did a failure occur in discovery, provider health, tensor transport, generation, the local WebSocket, or the diagnostics request?
5. What action is safe and useful after the failure?

## Planned Work

- [ ] Audit Nodes, Network, Inference, Monitoring, Incentives, and Settings as one end-to-end participant workflow.
- [ ] Add an explicit model-availability contract with local, remotely discoverable, route-validating, remotely runnable, gated, and unavailable states.
- [ ] Present a model catalog that labels `Can generate remotely`, `Serving locally`, `Needs providers`, and `Requires local access` without conflating those states.
- [ ] Separate the `Serve layers` contribution workflow from the `Run inference` consumer workflow and explain each role in plain language.
- [ ] Label pre-generator coverage as provisional and replace `Needs layers` with a discovery-aware state when the authoritative generator client has not started.
- [ ] Define one status hierarchy for backend, DHT discovery, provider RPC health, tensor canary, active generation stream, local WebSocket, and diagnostics.
- [ ] Remove stale `Waiting for generator route` messages immediately after authoritative readiness changes.
- [ ] Show transport failures with their stage, peer or route, request identity, retry-safety decision, and recommended next action.
- [ ] Make Trace a non-conflicting diagnostic job with an appropriate deadline and visible progress instead of the generic eight-second API timeout, and label it as legacy-only unless a distinct receipt-path diagnostic is implemented.
- [ ] Explain the difference between local Nodes, remote providers, selected routes, and unprobed workers in Monitoring.
- [ ] Add loading, empty, stale, mixed-version, timeout, suspended, and recovery states to renderer tests.
- [ ] Review responsive layout, keyboard navigation, accessibility labels, contrast, information density, and error-copy consistency across the desktop UI.

## Acceptance Criteria

- A generator-only user can identify a remotely runnable model without starting a local worker.
- Local model availability, local serving, remote coverage, and route readiness are visually distinct.
- The interface never simultaneously claims that a route is ready and that it is still waiting for that route.
- A failed real forward is not presented as a discovery failure when the provider leases and route remain healthy.
- Trace cannot create a second unexplained generation attempt or time out under the generic short API deadline.
- Every disabled primary action has a visible reason and a concrete recovery action.
- Renderer tests cover the physical-test state combinations recorded in the two-device incident report.

## Boundaries

- This sprint does not weaken generator route validation, RPC health checks, tensor canaries, or accounting-safe retry rules.
- This sprint does not treat visual changes as a fix for the open relay stream-reset defect.
- A larger visual redesign must preserve the current backend contracts or version any intentional contract changes.

## Session Log

### 2026-08-22 - Create sprint from physical relay usability evidence

- Recorded that model discovery and local serving are not distinguished clearly enough for generator-only users.
- Recorded the contradictory `Generator: READY`, `Route: READY`, stale `Waiting for generator route`, and failed stream presentation.
- Corrected the initial assumption about Trace: it uses the legacy expert because it does not start a useful-work session, while normal shadow chat uses the receipt expert; it is also hidden behind a generic eight-second frontend request timeout.
- Status: planning only; the current transport failure remains owned by the relay tensor RPC acceptance work.
