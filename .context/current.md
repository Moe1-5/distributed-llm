# Current State

> Keep this under 40 lines. Read this after `INDEX.md`.
> Update whenever the sprint changes, a major decision is made, or the stack changes.

**Name:** DistribLLM  
**Description:** Electron + FastAPI prototype for project-owned public/discoverable peer-to-peer distributed LLM inference over Hivemind DHT/RPC.  
**Stage:** [ ] Scaffolding [x] MVP prototype [ ] Feature-complete [ ] Production

## Active Sprint
**Sprint:** Sprints 18 through 24 are implemented locally on `feature/fundamental-live-stability`; physical two-device acceptance remains open.
**Goal:** Rebuild the audited Windows executable and collect relay, failover, and direct-LAN evidence on two physical devices.

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
- Sprint 15 now has a tested Electron-to-WSL launcher, lifecycle diagnostics, safe PID shutdown, package auditing, and a configuration-bound sanitized acceptance report; clean-Windows and two-device packaged validation remain open.
- Sprint 16 has verified public circuit reservation and expert metadata RPC. Persistent service tooling plus a cross-sprint manifest now bind VPS restart, relay probe, Windows lifecycle, relay inference, and direct inference evidence; the actual live run remains open.
- Sprint 17 implements adjacent-range route selection, capacity-aware serving plans, stale/redundancy conflicts, selected/standby route visibility, and the inference-to-serving gap workflow on `feature/coverage-aware-serving`; local acceptance passes and two-device validation remains open.
- Sprint 13 has signed settlement, real local split parity, and clean cached remote-expert P2P teardown on `feature/remote-expert-p2p-cleanup`; only separate-device, live VPS shadow/restart, and credit-approval gates remain.
- Sprint 22 prevents the generator relay-daemon panic, classifies RPC certainty, bounds retries, reuses sessions, and compresses legacy activation tensors; physical relay inference remains open.
- Sprint 23 implements asynchronous lifecycle jobs, prompt stop/unload paths, bounded status calls, and responsive frontend controls.
- Sprint 18 selectively materializes only served safetensor layers with strict fallback and measured TinyLlama memory/startup evidence.
- Sprint 19 continuously probes DHT/protocol/transport/RPC health with hysteresis, deadlines, bounded concurrency, and clean lifecycle ownership.
- Sprint 20 enforces tensor/metadata limits, bounded admission, cooperative deadlines, safe counters, and receipt accounting after successful output.
- Sprint 21 selects revisioned healthy complete routes and has a real two-expert local failover pass; physical relay failure injection remains open.
- Runtime commit `7c75b84` produced the initial audited portable executable; rebuild from the final runtime commit and physical relay/failover/direct acceptance remain open.
