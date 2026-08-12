# Sprint 22 - Relay Tensor RPC Stability

**Goal:** Make expert tensor forwards complete predictably through the project VPS relay, with bounded attempts, actionable failure evidence, and no retry loop.
**Start:** 2026-08-13
**End:** TBD
**Status:** Approved and in progress.

---

## Problem Summary

The live two-device run now proves bootstrap discovery, relay reservations, route coverage, and generator startup. Real tensor inference still does not complete reliably: the generator repeatedly reports `stream reset`, while VPS logs show each circuit stream carrying request and response bytes before reset. The current client retries every hop up to three times, validates the DHT route on every generated token, and does not attach a stable request identity or duration/size evidence to transport failures.

This sprint owns the immediate inference blocker. It must distinguish worker execution failures, client cancellation, relay resets, receipt fallback, and slow requests instead of masking all of them as identical retries.

## Dependencies and Boundaries

- Build on Sprint 16's verified relay reservation and expert metadata RPC.
- Preserve Sprint 17's complete adjacent route semantics and Sprint 13's no-credit-on-failure rule.
- Treat relay log `131072` byte copies as buffering evidence, not proof of a payload limit until a controlled payload test confirms it.
- Keep one immutable route for one forward attempt.
- Do not implement automatic alternate-route failover; Sprint 21 owns that behavior.
- Do not claim two-device completion from local or simulated tests.

## Work Plan

- [x] Add typed RPC attempt and backoff configuration with validated environment overrides.
- [x] Replace module-level retry constants with one bounded request policy and terminal error contract.
- [x] Record request ID, hop, attempt, input bytes, sequence length, elapsed time, and classified failure without recording tensor values.
- [x] Prevent receipt fallback from multiplying legacy forward attempts or awarding uncertain work.
- [x] Cache one discovered route snapshot for a generation request and invalidate it at session boundaries.
- [x] Add worker-side request timing and exception evidence around actual model execution.
- [x] Add controlled real-Hivemind payload coverage above 128 KiB and compression boundary tests.
- [x] Update debugging guidance with correlated generator, worker, and VPS evidence.

## Test Plan

- Unit tests cover policy validation, retryable classification, bounded backoff, terminal errors, and cancellation.
- A failed hop performs no more than the configured total attempt count.
- Receipt-enabled fallback never causes an unbounded or hidden second retry tree.
- Route discovery happens once per generation request rather than once per generated token.
- Real local Hivemind peers forward representative OPT activations and payloads larger than 128 KiB.
- Existing coverage, receipt, local split, relay probe, and acceptance-evidence tests remain green.
- A live two-device relay run records one complete OPT-125M generation with no unexplained stream reset.

## Acceptance Criteria

- [x] Every tensor RPC attempt has correlated client and worker diagnostics.
- [x] Retry count and backoff are finite, validated, and visible in the terminal error.
- [x] Cancellation stops additional attempts promptly.
- [x] A representative activation larger than 128 KiB crosses a real Hivemind RPC locally.
- [x] Failed or uncertain attempts create no accepted useful-work settlement.
- [ ] Two physical devices complete inference through the VPS relay without a retry loop.

---

## Session Log

### 2026-08-13 - Create relay tensor RPC stability sprint

- What changed: created the focused sprint for correlated transport evidence, bounded retry policy, route reuse, payload coverage, and live relay acceptance.
- Why: live discovery and generator startup now pass, but tensor forwards repeatedly end in `stream reset` and retry without completing generation.
- Status: approved and in progress; source implementation and controlled transport tests are next.

### 2026-08-13 - Bound expert attempts and reduce ordinary activation traffic

- What changed: added validated RPC attempt policy and failure classification, stopped ambiguous reset retries, restricted receipt fallback to pre-execution absence, reused one route per generation session, added request/worker timing evidence, restored dialing-only generator startup, and enabled float-sixteen wire compression only for legacy inference.
- Why: live relay calls reset after transferring data, while repeated blind attempts could duplicate completed work; route discovery and uncompressed activations also added avoidable latency and traffic.
- Status: 195 backend tests plus 22 subtests pass, including a real exact Hivemind receipt activation larger than 128 KiB and compression restoration coverage. A correlated physical two-device relay generation remains the acceptance gate; cancellable RPC execution timeout remains owned by Sprint 20 because Hivemind's synchronous expert call cannot be safely abandoned without leaving remote work running.

### 2026-08-13 - Make generation cancellation prompt and accounting-safe

- What changed: moved synchronous Hivemind route forwards off the FastAPI event loop and threaded one cancellation event through same-peer retries, retry backoff, route attempts, and hops.
- Semantics: an already-dispatched remote kernel is allowed to finish, but its result and pending receipts are discarded after cancellation. No additional retry, alternate route, or downstream hop starts.
- Evidence: controlled tests interrupt a five-second retry backoff after one call and keep the event loop responsive. The real local split probe completed 26.522 milliseconds after a stop request during active route work, generated no token, and did not call the second hop.
- Remaining gate: correlated generator and worker diagnostics plus complete inference still require the two-device VPS relay run.

### 2026-08-13 - Correlate legacy tensor attempts without changing the RPC schema

- What changed: successful and failed generator attempts now log request ID, hop, peer, RPC UID, attempt budget, range, tensor shape, byte count, and duration. Legacy workers log the matching RPC UID, range, shape, bytes, duration, and exception state around execution.
- Why: ordinary non-receipt inference cannot carry application metadata without changing the established expert tensor schema, but the shared RPC UID and non-secret tensor metadata provide a deterministic join across generator and worker logs.
- Verification: the full backend suite passes with 250 tests and 54 subtests. A physical relay generation remains the only transport acceptance gate.
