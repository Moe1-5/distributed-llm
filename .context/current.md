# Current State

> Keep this under 40 lines. Read this after `INDEX.md`.
> Update whenever the sprint changes, a major decision is made, or the stack changes.

## Project
**Name:** DistribLLM  
**Description:** Electron + FastAPI prototype for project-owned public/discoverable peer-to-peer distributed LLM inference over Hivemind DHT/RPC.  
**Stage:** [ ] Scaffolding [x] MVP prototype [ ] Feature-complete [ ] Production

## Active Sprint

**Sprint:** Sprint 13 implements useful-work incentives; Sprints 14 through 17 retain live acceptance gates.
**Goal:** Validate signed receipt accounting in shadow mode and prove cross-device inference through the persistent VPS relay.

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
- Sprint 15 now has a tested Electron-to-WSL launcher, first-run relay settings, lifecycle diagnostics, safe PID shutdown, package auditing, and a portable Windows artifact; clean-Windows and two-device packaged validation remain open.
- Sprint 16 has verified a public VPS circuit reservation in 1.63 seconds and expert metadata RPC through it. A persistent systemd service, runtime validator, restart continuity check, and recovery runbook are implemented on `feature/vps-relay-service-and-validation`; live VPS restart, tensor forwarding, direct mode, and two-device inference remain open.
- Sprint 17 implements adjacent-range route selection, capacity-aware serving plans, stale/redundancy conflicts, selected/standby route visibility, and the inference-to-serving gap workflow on `feature/coverage-aware-serving`; local acceptance passes and two-device validation remains open.
- Sprint 13 now has signed receipt settlement plus a real local OPT-125M `0-6 -> 6-12` two-peer parity pass on `feature/local-split-acceptance`; only separate-device, live VPS shadow/restart, and credit-approval gates remain.
- `feature/two-device-acceptance-evidence` adds sanitized relay/direct captures, actual-route ownership and timing validation, accepted shadow-receipt deltas, and optional standby non-payment checks; all 165 backend tests and 19 subtests pass, while physical-device and live VPS execution remain open.
