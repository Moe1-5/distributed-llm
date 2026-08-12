# Coverage-Aware Serving

**Status:** Implemented; two-device acceptance pending
**Last updated:** 2026-08-12

DistribLLM treats serving ranges as half-open intervals. A node advertising `0-6` serves layers zero through five, and an exactly adjacent `6-12` provider can continue that route.

## Route Selection

The generator groups providers with identical ranges as replicas, then finds a complete path from layer zero to model depth using only exactly adjacent advertised ranges. It ranks complete paths by the fewest network hops and then deterministic range ordering. Providers whose ranges overlap but are not selected remain standby rather than invalidating a valid route.

Replica round-robin advances only for exact spans selected in the executable route. An unselected partial replica does not consume a turn when a full-model provider is chosen.

Examples for a twelve-layer model:

| Advertised ranges | Result |
| --- | --- |
| `0-6` | Unavailable; needs `6-12`. |
| `0-6`, `0-6` | Unavailable; both providers cover the same half. |
| `0-6`, `6-12` | Runnable through two providers. |
| `0-6`, `0-12` | Runnable through `0-12`; `0-6` is standby. |

## Serving Plan API

`GET /models/{model_id}/serving-plan?layer_count=N` returns the active coverage snapshot for a model and requested serving capacity. Model identifiers may contain slashes.

The response includes:

- Provider counts for contiguous coverage segments.
- Current uncovered ranges and exact route requirements.
- A recommended half-open range and its operational effect.
- Current and projected runnable states.
- Selected, projected, and standby route ranges.
- Single-provider or multiple-provider route classification.
- A deterministic coverage revision derived from active provider identity and range metadata.

Recommendations rank candidates in this order: complete a route, cover the most missing layers, extend the prefix reachable from layer zero, prefer the least-replicated layers, then choose the lowest start layer.

## Start Revalidation

Node start accepts optional `coverage_revision` and `confirm_redundancy` fields. Existing callers may omit both.

When a revision is provided, the backend rediscovers active providers immediately before model loading. If the snapshot changed, it returns HTTP 409 with `coverage_revision_stale` and a fresh serving plan. The client updates the form and does not start from stale advice.

When a custom range covers no missing layers, does not complete a route, and route gaps remain, the backend returns HTTP 409 with `redundancy_confirmation_required`. Intentional replicas are accepted only after a second request sets `confirm_redundancy` to true.

Local ranges that partially overlap another local process remain rejected because one backend should not load intersecting slices of the same model accidentally. Exact local replicas retain the existing supported behavior.

## Desktop Workflow

Serve Layers defaults to Recommended mode. The user chooses how many layers the device can hold, sees current coverage and provider density, and starts the latest recommendation. Custom mode exposes explicit boundaries with validation and redundancy warnings.

Run Inference distinguishes one full-model provider from a complete multi-provider route, displays selected and standby ranges, and reports exact missing requirements. Serve missing range opens Serve Layers with the same model and a recommendation sized to the first route requirement.

Both tabs refresh every five seconds, and node start always performs one final refresh before submission.

## Incentive Boundary

Only providers selected in the executable route performed useful inference work. Standby advertisements and duplicated ranges that were not called cannot earn useful-work credit. Sprint 13 receipt generation and settlement must record the selected route and actual successful RPC work rather than infer rewards from DHT coverage alone.
