# Current State

> Keep this under 40 lines. Read this after `INDEX.md`.
> Update whenever the sprint changes, a major decision is made, or the stack changes.

## Project

**Name:** DistribLLM  
**Description:** Electron + FastAPI prototype for project-owned public/discoverable peer-to-peer distributed LLM inference over Hivemind DHT/RPC.  
**Stage:** [ ] Scaffolding [x] MVP prototype [ ] Feature-complete [ ] Production

## Active Sprint

**Sprint:** Sprint 13 is implementing useful-work receipts and settlement; Sprints 14-16 cover performance, Windows packaging, and live relay validation; Sprint 17 covers coverage-aware allocation.
**Goal:** Validate coverage-aware two-device inference and signed useful-work receipts through the VPS relay before enabling credit mode.

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
- Sprint 14 now exposes resource metrics plus generator startup, route, first-token, throughput, and per-hop RPC timing on its side branch; chat-template and streamed-output fixes plus live baselines remain open.
- Sprint 15 plans a Windows Electron package with the Linux-dependent backend isolated in managed WSL 2 and relay-backed `auto` mode as the default.
- Sprint 16 has verified a public VPS circuit reservation from Windows/WSL in 1.63 seconds and an independent-peer OPT-125M expert metadata RPC through that circuit; tensor forwarding and two-device inference remain unproven.
- Sprint 17 now recommends useful layer ranges, selects complete route subsets despite overlap, and exposes coverage in both Network tabs; live two-device validation remains open.
- Sprint 13 now has Ed25519/BLAKE3 receipts, a shadow/credit SQLite settlement service, anti-abuse validation, and read-only incentives UI; credit mode remains gated by live shadow evidence.
