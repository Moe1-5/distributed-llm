# Current State

> Keep this under 40 lines. Read this after `INDEX.md`.
> Update whenever the sprint changes, a major decision is made, or the stack changes.

## Project

**Name:** DistribLLM  
**Description:** Electron + FastAPI prototype for project-owned public/discoverable peer-to-peer distributed LLM inference over Hivemind DHT/RPC.  
**Stage:** [ ] Scaffolding [x] MVP prototype [ ] Feature-complete [ ] Production

## Active Sprint

**Sprint:** Sprint 10 remains the lowest active sprint; Sprint 14 is active for performance and visibility; Sprint 13 remains planned for incentives.
**Goal:** Complete live model validation while measuring runtime/generation performance and correcting chat-template and streamed-output behavior.

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

- Sprint 04 closed: local smoke test passed for bootstrap, API, single OPT-125M serving node, generator readiness, and short `/chat` inference; one backend process still serves only one local layer slice, and Hivemind worker cleanup remains a follow-up issue.
- Sprint 05 closed: bootstrap is hidden from normal tabs, Nodes has local stop control, Inference gates send by backend/WebSocket/generator/route readiness, and Monitoring shows real status/coverage.
- Sprint 06 closed: one backend process per serving participant, runnable-model status in `/models`, simulated contribution accounting only, and real incentives are deferred to Sprint 11 after model-access reliability work.
- Sprint 07 closed: single-node OPT-125M parity passed, a live full-layer OPT-1.3B smoke trace/parity probe passed for first-token correctness, exact generation controls are exposed, and generated-output parity can compare direct HuggingFace text with distributed text. Broader live OPT-1.3B and true multi-node parity are downstream live-route validation items.
- Sprint 08 closed: it hides bootstrap internals, uses one Inference open/send/stop action, shows a P2P Monitoring map, writes `/generator/trace` JSON diagnostics, exposes trace UI, adds generator stop control, separates node turn-off/delete actions, and supports same-model non-overlapping local served nodes.
- Sprint 09 closed: non-destructive node pause/resume/delete semantics, duplicate and multi-model local serving, HuggingFace token validation, structured trace-artifact analysis, and backend shutdown hardening are checklist-complete and archived.
- Sprint 10 implementation is complete: Hugging Face device OAuth, redacted auth status, download job status/cancel, validated local import handoff, startup from validated local paths, and manual folder fallback are tested. External live validation is blocked on approved gated-model access for the selected repo; Llama 2 access is accepted while Llama 3.2 is pending.
- Sprint 11 has started with TinyLlama chat as an open smoke-test option, Llama 2 base/chat gated options for the accepted model family, and explicit base/chat/instruct metadata in the model registry.
- Sprint 12 closed on 2026-07-14: public auth isolation, failed-start cleanup, cache deletion, local expert routing, reachable-route probing, and safetensors/bin-index import validation are implemented and pushed.
- Sprint 14 started on 2026-07-14 with timestamped machine/backend-process resource metrics and Monitoring visibility; generation timing and output-path fixes remain next.
- Canonical docs were refreshed on 2026-07-13 for OAuth/local imports, model registry, public bootstrap/remote workers, current lifecycle/routing, and live validation gaps.
