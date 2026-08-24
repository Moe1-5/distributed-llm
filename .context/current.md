# Current State

> Keep this under 40 lines. Read this after `INDEX.md`.
> Update whenever the sprint changes, a major decision is made, or the stack changes.
**Name:** DistribLLM  
**Description:** Electron + FastAPI prototype for project-owned public/discoverable peer-to-peer distributed LLM inference over Hivemind DHT/RPC.  
**Stage:** [ ] Scaffolding [x] MVP prototype [ ] Feature-complete [ ] Production
## Active Sprint
**Sprint:** Sprint 13 remains the lowest active sprint by workflow; Sprint 31 passed its bidirectional physical relay gate, Sprints 28-30 and 32 retain physical rollout work, and Sprints 22, 25, and 27 retain non-session transport, runtime, and UI gates.
**Goal:** Deploy and physically validate exact identities, persistent network ownership, atomic placement, bounded OPT sessions, independent DHT/relay failure domains, and the final architecture matrix.
## Tech Stack
React 19 + Electron/Vite frontend, FastAPI Python backend, Hivemind DHT/RPC, Transformers/PyTorch, uv for backend dependencies, npm for frontend scripts.
## Last Decision
[2026-06-30] Adopt project operating system from Project-Starter.

## Status Flags
- [x] Tests configured
- [ ] CI/CD active
- [x] Auth implemented
- [ ] First deploy done
- [ ] Database migrations tracked
- [x] Documentation map created
**Latest Sprint Update**
- Sprints 10 and 11 are closed and archived; Sprint 13 remains planned for real incentives behind substantial correctness and proof prerequisites.
- Sprint 14 now has a verified local TinyLlama baseline after the Transformers 5.3 chat-template fix on `feature/transformers-chat-template-compat`; only direct/relay two-device validation remains open.
- Sprint 15 now has a tested Electron-to-WSL launcher, sandboxed custom-origin renderer, exact IPC/CORS/WebSocket trust boundaries, a checksum-bound bundled backend installed atomically by commit, frozen isolated environments, safe PID shutdown, package auditing, and schema-four acceptance evidence. Physical package retries exposed first raw `wslpath` backslash loss and then an attempted `.venv` creation in the immutable runtime; the repaired launcher transports paths inside the encoded script and explicitly syncs and runs a versioned external environment. The audit now also rejects stale portable wrappers when a Linux cross-build refreshes only the unpacked runtime, so the next replacement must be produced on a Windows-capable host.
- Sprint 16 has verified public circuit reservation, expert metadata RPC, live managed-service restart continuity, and an external Device 1 circuit reservation bound to the exact VPS report. The package-hosted relay probe reached a circuit in 2.507 seconds with stable relay peer `QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y`, and the two-device adjacent slice has now completed through the relay in both generator directions.
- Sprint 17 implements adjacent-range route selection, capacity-aware serving plans, stale/redundancy conflicts, selected/standby route visibility, and the inference-to-serving gap workflow on `feature/coverage-aware-serving`; local acceptance passes and two-device validation remains open.
- Sprint 13 has signed settlement, real local split parity, an identity-bound durable SQLite retry outbox, loader-resolved immutable checkpoint receipts, append-only policy version two, and a VPS shadow service; separate-device receipt/restart evidence and credit approval remain open. The full integration and final desktop audit is in `docs/SYSTEM_CODE_ANALYSIS_AND_FINALIZATION_REPORT.md`.
- Sprint 22 preserves the historical evidence that stateless full-provider and adjacent split generation could reset after route validation. Sprint 31's retained exact-peer session handles now pass five bidirectional relayed streams, including three fingerprint-bound retained-result recoveries; the controlled relay/direct stateless payload sweep and shadow receipt path remain separate open gates.
- Sprint 23 implements asynchronous jobs, prompt cancellation, a bounded warm component cache, and measured local startup/generation baselines.
- Sprint 18 selectively materializes only served safetensor layers with strict fallback and measured TinyLlama memory/startup evidence.
- Sprint 19 continuously probes DHT/protocol/transport/RPC health with hysteresis, deadlines, bounded concurrency, and clean lifecycle ownership.
- Sprint 20 enforces tensor/metadata limits, bounded admission, cooperative deadlines, safe counters, and receipt accounting after successful output.
- Sprint 21 selects revisioned healthy complete routes and has a real two-expert local failover pass; physical relay failure injection remains open.
- Sprint 24 now binds device reports to executable SHA-256 plus the exact tracked-clean WSL revision; physical lifecycle, relay, failover, and direct acceptance remain open.
- Sprint 25 now refreshes worker metadata and membership on bounded heartbeats, rejects stale local advertisements, gates generator readiness on RPC health and a tensor canary, and exposes suspended generators for explicit unload. Physical relay retesting remains open.
- Sprint 26 now keeps Electron chat free while verified credits unlock hashed local API keys, shared atomic reservations, signed capabilities, and OpenAI-compatible chat completions; hosted global spending remains future work.
- Sprint 27 now has explicit model availability, coherent runtime stages, pollable legacy Trace diagnostics, provider-role explanations, responsive/accessibility fixes, and renderer recovery-state tests. The latest physical session run still recorded recoverable serving-plan, model, status, and generator-status polling timeouts. Sprint 31's bounded sessions passed bidirectional relay acceptance; packaged multi-host infrastructure, shadow receipts, and the remaining physical matrix stay open.
