# Current State

> Keep this under 40 lines. Read this after `INDEX.md`.
> Update whenever the sprint changes, a major decision is made, or the stack changes.

## Project

**Name:** DistribLLM  
**Description:** Electron + FastAPI prototype for project-owned public/discoverable peer-to-peer distributed LLM inference over Hivemind DHT/RPC.  
**Stage:** [ ] Scaffolding [x] MVP prototype [ ] Feature-complete [ ] Production

## Active Sprint

**Sprint:** Sprint 13 is the lowest numbered active plan; Sprint 14 is active for performance and visibility; Sprints 15 and 16 cover Windows packaging and live VPS relay validation.
**Goal:** Prove cross-device expert RPC and inference over the now-verified VPS relay, then continue performance/output and packaging work.

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
- Sprint 14 now exposes performance metrics, applies model-aware chat templates, preserves cumulative streamed decoding, and separates local lifecycle controls from all-peer Monitoring; live TinyLlama and two-device baselines remain open.
- Sprint 15 now has a tested Electron-to-WSL launcher, first-run relay settings, lifecycle diagnostics, safe PID shutdown, package auditing, and a portable Windows artifact; clean-Windows and two-device packaged validation remain open.
- Sprint 16 has verified a public VPS circuit reservation from Windows/WSL in 1.63 seconds and an independent-peer OPT-125M expert metadata RPC through that circuit; tensor forwarding and two-device inference remain unproven.
