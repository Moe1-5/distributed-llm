# Current State

> Keep this under 40 lines. Read this after `INDEX.md`.
> Update whenever the sprint changes, a major decision is made, or the stack changes.

## Project

**Name:** DistribLLM  
**Description:** Electron + FastAPI prototype for project-owned public/discoverable peer-to-peer distributed LLM inference over Hivemind DHT/RPC.  
**Stage:** [ ] Scaffolding [x] MVP prototype [ ] Feature-complete [ ] Production

## Active Sprint

**Sprint:** Sprint 09 is the lowest active sprint; Sprint 10 is planned for incentives.
**Goal:** Add safe node lifecycle controls, multi-node local serving, HuggingFace token validation, and trace analysis.

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

- Sprint 04 closed: local smoke test passed for bootstrap, API, single OPT-125M serving node, generator readiness, and short `/chat` inference; one backend process still serves only one local layer slice, and Hivemind worker cleanup remains a follow-up issue.
- Sprint 05 closed: bootstrap is hidden from normal tabs, Nodes has local stop control, Inference gates send by backend/WebSocket/generator/route readiness, and Monitoring shows real status/coverage.
- Sprint 06 closed: one backend process per serving participant, runnable-model status in `/models`, simulated contribution accounting only, and real incentives deferred to Sprint 10.
- Sprint 07 closed: single-node OPT-125M parity passed, a live full-layer OPT-1.3B smoke trace/parity probe passed for first-token correctness, exact generation controls are exposed, and generated-output parity can compare direct HuggingFace text with distributed text. Broader live OPT-1.3B and true multi-node parity are downstream live-route validation items.
- Sprint 08 closed: it hides bootstrap internals, uses one Inference open/send/stop action, shows a P2P Monitoring map, writes `/generator/trace` JSON diagnostics, exposes trace UI, adds generator stop control, separates node turn-off/delete actions, and supports same-model non-overlapping local served nodes.
- Sprint 09 is current: non-destructive node pause/resume/delete semantics, duplicate and multi-model local serving scope, HuggingFace token validation, structured trace-artifact analysis, and backend shutdown hardening.
