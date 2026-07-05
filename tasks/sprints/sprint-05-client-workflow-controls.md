# Sprint 05 - Client Workflow Controls

**Goal:** Turn the Electron app into a clear client workflow after local inference validation is understood: no bootstrap-as-product tab, clear node controls, inference cancellation, and a monitoring page shell backed by real status.
**Start:** 2026-07-06
**End:** TBD

---

## Problem Summary

The 2026-07-05 screen review showed that the app exposes infrastructure and hides real user controls. Bootstrap setup is shown as a normal Network tab even though it is an internal discovery concern. Stop controls are not where the user naturally looks. Inference can run without a visible cancel path. There is no dedicated monitoring page for the network view.

Sprint 05 starts only after Sprint 04 gives enough confidence about backend readiness/status semantics. The UI should bind to real backend state instead of painting optimistic status.

---

## In Progress

- [x] Start client workflow cleanup.

## Todo

- [x] Remove Bootstrap from normal client navigation and move setup guidance to docs/internal operations.
- [ ] Keep bootstrap peers as default/advanced configuration rather than primary client workflow.
- [ ] Add event-driven WebSocket state in the frontend: connecting, open, closed, error.
- [ ] Prevent first prompt send before WebSocket `onopen`.
- [ ] Add active inference stop/cancel control to the Inference page.
- [x] Add local node stop controls to the primary Nodes page when a node belongs to this backend.
- [x] Add a Monitoring page to main navigation.
- [x] Monitoring page shows backend status, generator readiness, discovered nodes, layer coverage, route completeness, and latency placeholders.
- [x] Keep the monitoring view visually distinct from setup controls.
- [ ] Update frontend type definitions and API client responses for any backend status fields used by the UI.

## Done

- [x] Bootstrap setup removed from the normal Network tab flow.
- [x] Monitoring page shell added to main navigation.
- [x] Local node stop control added to the primary Nodes page.

---

## Acceptance Criteria

- [ ] User can tell backend reachability, WebSocket state, generator readiness, and route readiness apart.
- [ ] User cannot send a prompt while the WebSocket is not open or the generator is not ready.
- [ ] User can cancel active inference from the Inference page.
- [x] User can stop local served nodes from the primary Nodes view.
- [x] Bootstrap setup is no longer a normal client tab.
- [x] Monitoring appears as a main navigation page and reflects real backend/network status.
- [ ] Frontend typecheck passes.

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
