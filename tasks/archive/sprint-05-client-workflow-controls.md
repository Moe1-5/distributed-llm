# Sprint 05 - Client Workflow Controls

**Goal:** Turn the Electron app into a clear client workflow after local inference validation is understood: no bootstrap-as-product tab, clear node controls, inference cancellation, and a monitoring page shell backed by real status.
**Start:** 2026-07-06
**End:** 2026-07-06

---

## Problem Summary

The 2026-07-05 screen review showed that the app exposes infrastructure and hides real user controls. Bootstrap setup is shown as a normal Network tab even though it is an internal discovery concern. Stop controls are not where the user naturally looks. Inference can run without a visible cancel path. There is no dedicated monitoring page for the network view.

Sprint 05 starts only after Sprint 04 gives enough confidence about backend readiness/status semantics. The UI should bind to real backend state instead of painting optimistic status.

---

## In Progress

- [x] Start client workflow cleanup.

## Todo

- [x] Remove Bootstrap from normal client navigation and move setup guidance to docs/internal operations.
- [x] Keep bootstrap peers as default/advanced configuration rather than primary client workflow.
- [x] Add event-driven WebSocket state in the frontend: connecting, open, closed, error.
- [x] Prevent first prompt send before WebSocket `onopen`.
- [x] Add active inference stop/cancel control to the Inference page.
- [x] Add local node stop controls to the primary Nodes page when a node belongs to this backend.
- [x] Add a Monitoring page to main navigation.
- [x] Monitoring page shows backend status, generator readiness, discovered nodes, layer coverage, route completeness, and latency placeholders.
- [x] Keep the monitoring view visually distinct from setup controls.
- [x] Update frontend type definitions and API client responses for any backend status fields used by the UI.

## Done

- [x] Bootstrap setup removed from the normal Network tab flow.
- [x] Monitoring page shell added to main navigation.
- [x] Local node stop control added to the primary Nodes page.
- [x] Inference page now separates backend, WebSocket, generator, and route readiness before sending.
- [x] Frontend typecheck passes.

---

## Acceptance Criteria

- [x] User can tell backend reachability, WebSocket state, generator readiness, and route readiness apart.
- [x] User cannot send a prompt while the WebSocket is not open or the generator is not ready.
- [x] User can cancel active inference from the Inference page.
- [x] User can stop local served nodes from the primary Nodes view.
- [x] Bootstrap setup is no longer a normal client tab.
- [x] Monitoring appears as a main navigation page and reflects real backend/network status.
- [x] Frontend typecheck passes.

---

## Session Log

### 2026-07-06 - Create client workflow controls sprint

- What changed: created Sprint 05 to hold product workflow fixes split out from Sprint 04.
- Why: bootstrap hiding, stop controls, inference cancellation, WebSocket state, and monitoring are too large to mix into the local validation sprint.
- Status: sprint is planned but not started.

### 2026-07-06 - Start client workflow cleanup

- What changed: removed Bootstrap from normal Network tabs; added Monitoring to main navigation; created a Monitoring page backed by status, generator status, models, and discovered nodes; added a primary Nodes-page stop control for the local served node.
- Why: Sprint 05 should make the app show serving, inference, node control, and monitoring as product workflow while keeping bootstrap infrastructure out of the normal client tabs.
- Status: implementation is ready for frontend typecheck; broader WebSocket/generator readiness gating remains open.

### 2026-07-06 - Gate inference by real readiness state

- What changed: updated the Inference page to poll backend and generator readiness, show backend/WebSocket/generator/route states separately, require an open stream before sending, keep active stop/cancel behavior, and fixed the frontend TypeScript deprecation setting so typecheck runs with the installed compiler.
- Why: Sprint 05 requires users to distinguish connection state from generator and route readiness, and the UI should not enqueue a first prompt while the WebSocket is still connecting.
- Status: `npm run typecheck` passes. Sprint 05 acceptance criteria are now satisfied pending user review.

### 2026-07-06 - Remove deprecated frontend baseUrl config

- What changed: removed the deprecated `baseUrl` compiler option from the frontend web TypeScript config and made the renderer path alias explicitly relative.
- Why: the IDE reported that `baseUrl` is deprecated for TypeScript 7.0, while using the TypeScript 6-only deprecation silencer would break the repo's installed TypeScript 5.9 compiler.
- Status: `npm run typecheck` passes.

### 2026-07-06 - Fix frontend backend URL configuration

- What changed: corrected the local frontend WebSocket env key to `VITE_WS_BASE_URL`; updated Electron content security policy entries to allow localhost backend HTTP and WebSocket URLs; updated architecture/debug docs that still described the old hardcoded WSL backend IP.
- Why: the backend was running on `127.0.0.1:8000`, but the Electron renderer could still fail fetches because the dev env/CSP path was stale or blocked.
- Status: backend `/status` responds on `127.0.0.1:8000` and frontend `npm run typecheck` passes. Restart the Electron/Vite dev server after env changes so Vite reloads the values.

### 2026-07-06 - Close Sprint 05

- What changed: archived Sprint 05 after confirming its checklist and acceptance criteria were complete.
- Why: the user explicitly asked to close Sprint 05 if finished.
- Status: Sprint 05 is closed and archived.
