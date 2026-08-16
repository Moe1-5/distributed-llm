# Sprint 26 - Credit-Gated API Access

**Goal:** Keep fair-use Electron chat available without an API key while verified useful-work credits unlock and fund authenticated model API access.
**Start:** 2026-08-14
**End:** TBD

---

## Dependencies

- Sprint 25 runtime readiness and diagnostics must pass before access enforcement.
- Useful-work receipt settlement remains in shadow mode until physical two-device review.

## Planned Work

- [x] Add versioned off, shadow, and enforced access policy modes.
- [x] Use the existing Ed25519 application identity for free-chat capability requests.
- [x] Add one shared verified-credit balance with model-weighted API pricing.
- [x] Add revocable, hashed API keys that require a positive verified balance to create.
- [x] Add short-lived signed inference capabilities instead of exposing API keys to workers.
- [x] Add an authenticated OpenAI-compatible local chat-completions API.
- [x] Add idempotent credit reservation, settlement, and unused-credit release.
- [x] Keep gated-model authorization independent from credits and API access.
- [x] Plan the hosted project gateway as phase two using the same API contract.

## Acceptance Criteria

- [x] Free Electron chat works without an API key under configured fair-use policy.
- [x] API-key creation fails without verified credits and succeeds with an eligible balance.
- [x] API requests stop when available credits are insufficient without revoking the key.
- [x] Forged, expired, replayed, wrong-model, or oversized capabilities fail before execution.
- [x] Failed, cancelled, standby, probe, and self-dealing work earns no credit.
- [x] Concurrent requests cannot double-spend credits.
- [x] Off and shadow modes preserve compatibility before explicit enforcement approval.

---

## Session Log

### 2026-08-14 - Create approved credit-gated access sprint

- What changed: created Sprint 26 as the dependent access-control and developer-API phase.
- Why: serving rewards and API credentials must be separate; useful serving should earn credits while free users remain limited to Electron chat.
- Status: queued behind Sprint 25 runtime acceptance.

### 2026-08-14 - Implement local credit-gated developer API foundation

- What changed: added off/shadow/enforced policy, hashed revocable API keys, application-wide atomic reservations, model-weighted charging, unused reservation release, and single-use signed inference capabilities.
- API: added local OpenAI-compatible streaming and non-streaming chat completions; bearer keys terminate at the generator API and never enter worker RPC metadata.
- Frontend: the Incentives view now reports available, reserved, and locally spent credits and manages API keys without exposing application private-key material.
- Verified: twenty-one focused backend access/runtime/lifecycle tests and the frontend production build passed.
- Remaining: keep access mode off until Sprint 25 and useful-work two-device shadow acceptance pass; the globally atomic hosted gateway remains a later deployment phase.
