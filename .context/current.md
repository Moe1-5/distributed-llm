# Current State

> Keep this under 40 lines. Read this after `INDEX.md`.
> Update whenever the sprint changes, a major decision is made, or the stack changes.

## Project
**Name:** DistribLLM  
**Description:** Electron + FastAPI prototype for project-owned public/discoverable peer-to-peer distributed LLM inference over Hivemind DHT/RPC.  
**Stage:** [ ] Scaffolding [x] MVP prototype [ ] Feature-complete [ ] Production

## Active Sprint

**Sprint:** Sprint 13 implements useful-work incentives; Sprints 14 through 17 retain live gates; Sprints 18 through 21 are proposals awaiting review.
**Goal:** Finish cross-device relay evidence, then implement selective loading, continuous health, RPC safety, and failover in dependency order after approval.

## Tech Stack
React 19 + Electron/Vite frontend, FastAPI Python backend, Hivemind DHT/RPC, Transformers/PyTorch, uv for backend dependencies, npm for frontend scripts.

## Last Decision

[2026-06-30] Adopt project operating system from Project-Starter

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
- `feature/two-device-acceptance-evidence` adds sanitized relay/direct captures, actual-route ownership and timing validation, accepted shadow-receipt deltas, and optional standby non-payment checks; all 165 backend tests and 19 subtests pass, while physical-device and live VPS execution remain open.
- Sprints 18 through 21 are proposal-only plans for selective worker loading, continuous provider health, RPC resource safety, and health-aware failover; no runtime implementation has started.
