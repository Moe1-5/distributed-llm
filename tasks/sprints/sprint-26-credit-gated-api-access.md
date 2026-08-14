# Sprint 26 - Credit-Gated API Access

**Goal:** Keep fair-use Electron chat available without an API key while verified useful-work credits unlock and fund authenticated model API access.
**Start:** 2026-08-14
**End:** TBD

---

## Dependencies

- Sprint 25 runtime readiness and diagnostics must pass before access enforcement.
- Useful-work receipt settlement remains in shadow mode until physical two-device review.

## Planned Work

- [ ] Add versioned off, shadow, and enforced access policy modes.
- [ ] Use the existing Ed25519 application identity for free-chat capability requests.
- [ ] Add one shared verified-credit balance with model-weighted API pricing.
- [ ] Add revocable, hashed API keys that require a positive verified balance to create.
- [ ] Add short-lived signed inference capabilities instead of exposing API keys to workers.
- [ ] Add an authenticated OpenAI-compatible local chat-completions API.
- [ ] Add idempotent credit reservation, settlement, and unused-credit release.
- [ ] Keep gated-model authorization independent from credits and API access.
- [ ] Plan the hosted project gateway as phase two using the same API contract.

## Acceptance Criteria

- [ ] Free Electron chat works without an API key under configured fair-use policy.
- [ ] API-key creation fails without verified credits and succeeds with an eligible balance.
- [ ] API requests stop when available credits are insufficient without revoking the key.
- [ ] Forged, expired, replayed, wrong-model, or oversized capabilities fail before execution.
- [ ] Failed, cancelled, standby, probe, and self-dealing work earns no credit.
- [ ] Concurrent requests cannot double-spend credits.
- [ ] Off and shadow modes preserve compatibility before explicit enforcement approval.

---

## Session Log

### 2026-08-14 - Create approved credit-gated access sprint

- What changed: created Sprint 26 as the dependent access-control and developer-API phase.
- Why: serving rewards and API credentials must be separate; useful serving should earn credits while free users remain limited to Electron chat.
- Status: queued behind Sprint 25 runtime acceptance.
