# Sprint 31 - Session-Aware KV-Cache Inference

**Goal:** Replace full-sequence retransmission per token with bounded prefill and decode sessions while preserving parity, cancellation, accounting, and safe recovery.
**Start:** 2026-08-23
**End:** TBD
**Status:** Planned behind the stateless transport baseline and Sprints 28 through 30.

---

## Problem Summary

The current generator embeds and forwards the full accumulated sequence through every remote layer for each generated token. Payload size and computation therefore grow throughout generation, increasing relay traffic and exposure to the sustained stream failure that a small startup canary does not reproduce. Petals avoids this pattern with session-scoped remote key/value caches and sends only new positions during decode.

This is a protocol change, not a performance flag. Cache ownership, model semantics, cancellation, failover, memory admission, and useful-work accounting must remain explicit.

## Dependencies and Boundaries

- Require Sprint 22's direct-versus-relay stateless payload sweep as the baseline.
- Depend on Sprint 20 RPC safety, Sprint 21 bounded failover, and Sprint 28 exact peer dispatch.
- Start with OPT-125M and expand only through architecture-specific parity evidence.
- Preserve a versioned stateless protocol for compatibility and transport diagnosis.
- Do not claim cache transfer between peers; an alternate must rebuild from known history under defined safety rules.

## Work Plan

- [ ] Define versioned `open`, `prefill`, `decode`, `close`, and `cancel` RPC operations.
- [ ] Create stable session, route, request, and operation identities.
- [ ] Add bounded per-provider cache ownership, admission, expiry, eviction, cleanup, and diagnostics.
- [ ] Keep one exact peer-addressed route stable for a session.
- [ ] Send full context only during prefill and only new positions during decode.
- [ ] Add OPT adapter support for remote cached attention state with Hugging Face parity.
- [ ] Define cancellation and ambiguous-outcome semantics for every session operation.
- [ ] Rebuild state on a safe alternate from known token history without blind ambiguous replay.
- [ ] Make useful-work operations and accepted receipts idempotent across session recovery.
- [ ] Expose prefill/decode bytes, cache use, eviction, first-token time, and per-token timing.

## Test Plan

- Compare cached distributed logits and greedy output with stateless distributed and direct Hugging Face output.
- Prove decode payload remains bounded as total sequence length grows.
- Exercise cache admission, expiry, eviction, explicit close, cancellation, worker unload, and backend shutdown.
- Fail the selected provider before dispatch and rebuild once on an exact healthy alternate.
- Produce an ambiguous in-flight failure and prove there is no blind replay or duplicate accepted work.
- Run local real-Hivemind and physical relayed session generation with request-correlated evidence.

## Acceptance Criteria

- [ ] Cached and stateless greedy output match the accepted Hugging Face reference tolerance.
- [ ] Decode traffic no longer grows with the full accumulated sequence.
- [ ] Cache memory and concurrency are bounded and observable.
- [ ] Cancellation, timeout, unload, and peer loss release cache state.
- [ ] Safe alternate recovery rebuilds once; ambiguous execution never triggers blind replay.
- [ ] One real two-device relayed session completes with correlated prefill and decode evidence.

---

## Session Log

### 2026-08-23 - Create session-aware inference sprint

- What changed: created the sprint for versioned prefill/decode RPCs, bounded remote key/value caches, exact route ownership, parity, cleanup, and accounting-safe recovery.
- Why: the architecture audit identified full-sequence retransmission and recomputation as a growing sustained-relay load that is absent from the successful one-position startup canary.
- Status: planning is complete and remains behind the stateless transport baseline plus Sprints 28 through 30; no runtime source changed in this session.
