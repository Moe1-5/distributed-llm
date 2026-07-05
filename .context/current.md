# Current State

> Keep this under 40 lines. Read this after `INDEX.md`.
> Update whenever the sprint changes, a major decision is made, or the stack changes.

## Project

**Name:** DistribLLM  
**Description:** Electron + FastAPI prototype for project-owned public/discoverable peer-to-peer distributed LLM inference over Hivemind DHT/RPC.  
**Stage:** [ ] Scaffolding [x] MVP prototype [ ] Feature-complete [ ] Production

## Active Sprint

**Sprint:** Sprint 04 - Local System Validation remains the current lowest active sprint. Sprint 05 through Sprint 07 are planned follow-on splits.
**Goal:** Prove local end-to-end distributed inference first, then clean up client workflow controls, routing/model-access/incentives, and output parity.

## Tech Stack

React 19 + Electron/Vite frontend, FastAPI Python backend, Hivemind DHT/RPC, Transformers/PyTorch, uv for backend dependencies, npm for frontend scripts.

## Last Decision

[2026-06-30] Adopt project operating system from Project-Starter

## Status Flags

- [x] Tests configured
- [ ] CI/CD active
- [ ] Auth implemented
- [ ] First deploy done
- [ ] Database migrations tracked
- [x] Documentation map created

**Latest Sprint Update**

- Sprint 04 local smoke test passed for bootstrap, API, single OPT-125M serving node, generator readiness, and short `/chat` inference.
- Sprint 04 found two blockers: one backend process cannot host multiple local layer slices, and node shutdown needed bounded cleanup. `/node/stop` now returns, but Hivemind worker cleanup remains open.
- Sprint 05 client workflow cleanup is implemented pending user review: bootstrap is hidden from normal tabs, Nodes has local stop control, Inference gates send by backend/WebSocket/generator/route readiness, and Monitoring shows real status/coverage.
- Sprint 06 now holds multi-node serving strategy, runnable-model semantics, and model-aware incentive accounting.
- Sprint 07 now holds HuggingFace-direct versus distributed output parity and quality checks.
