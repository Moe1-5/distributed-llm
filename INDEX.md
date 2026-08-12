# Project Index

> Canonical map of every important file in the repo.
> Assistants should look here first instead of guessing or searching.
> Update this index any time a file is added, moved, renamed, or changes scope.

---

## Quick "Where do I look for..." lookup

| If you need...                            | Go to                                     |
| ----------------------------------------- | ----------------------------------------- |
| Project rules / workflow                  | `CLAUDE.md`                               |
| Cross-tool assistant rules / Read Aloud   | `AGENTS.md`                               |
| Current project state                     | `.context/current.md`                     |
| Active sprint, goal, and completed sprint history | `tasks/active.md`, `tasks/sprints/`, then `tasks/archive/` |
| AI self-correction lessons                | `tasks/lessons.md`                        |
| Main documentation landing page           | `docs/README.md`                          |
| Docs-only routing                         | `docs/INDEX.md`                           |
| Repo file responsibilities                | `docs/REPO_MAP.md`                        |
| Current backend/frontend/DHT architecture | `docs/CURRENT_ARCHITECTURE.md`            |
| Runtime flows                             | `docs/FLOWS.md`                           |
| Implementation roadmap                    | `docs/IMPLEMENTATION.md`                  |
| Petals comparison                         | `docs/PETALS_COMPARISON.md`               |
| Network reachability and relay review      | `docs/NETWORK_REACHABILITY_AND_RELAY_REVIEW.md` |
| Windows managed WSL packaging        | `docs/WINDOWS_MANAGED_WSL_PACKAGING.md` |
| Useful-work incentives and settlement | `docs/USEFUL_WORK_INCENTIVES.md`       |
| Two-device acceptance evidence          | `docs/TWO_DEVICE_ACCEPTANCE_EVIDENCE.md` |
| Local split acceptance                   | `docs/LOCAL_SPLIT_ACCEPTANCE.md` |
| TinyLlama performance baseline            | `docs/TINYLLAMA_PERFORMANCE_BASELINE.md` |
| Errors and debugging                      | `docs/ERRORS_AND_DEBUGGING.md`            |
| Validation plan                           | `docs/VALIDATION_AND_TEST_PLAN.md`        |
| Architectural decision history            | `docs/decisions.md`                       |
| Sprint-log hook scripts                   | `scripts/sprint-log/`                     |
| Backend API                               | `backend/api/server.py`                   |
| Backend env loading                      | `backend/api/env_loader.py`              |
| Backend Hugging Face OAuth/download       | `backend/api/hf_oauth.py`                 |
| Backend settings/token storage            | `backend/api/settings.py`                 |
| Backend local model import registry       | `backend/api/local_models.py`             |
| Backend constants/model registry          | `backend/constants.py`                    |
| Node serving lifecycle                    | `backend/node/node.py`                    |
| Remote layer routing                      | `backend/client/sequential.py`            |
| Distributed generation                    | `backend/client/generation.py`            |
| Frontend API client                       | `frontend/src/renderer/src/api/client.ts` |
| Frontend pages                            | `frontend/src/renderer/src/pages/`        |
| Env variable template                     | `.env.example`                            |

---

## Root

| File                         | What's inside                                                                    |
| ---------------------------- | -------------------------------------------------------------------------------- |
| `AGENTS.md`                  | Cross-tool assistant instructions.                                               |
| `CLAUDE.md`                  | Session rules, workflow, sprint logging, testing protocol, and repo conventions. |
| `INDEX.md`                   | This master file map.                                                            |
| `.context/current.md`        | Short current-state summary for assistants.                                      |
| `.env.example`               | Documented environment variable template with no secrets.                        |
| `.gitignore`                 | Version-control exclusions.                                                      |
| `ISSUES.md`                  | Running project issue log.                                                       |
| `cuda-keyring_1.1-1_all.deb` | Local/generated CUDA package artifact; should not be committed.                  |

---

## `tasks/` - sprint and lessons tracking

| File                                                         | What's inside                                                                        |
| ------------------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `tasks/active.md`                                            | Active sprint routing table.                                                         |
| `tasks/lessons.md`                                           | Assistant lessons: active and internalized.                                          |
| `tasks/sprints/sprint-13-real-incentives-and-settlement.md`   | Active future sprint plan for real rewards, contribution receipts, anti-abuse checks, and settlement. |
| `tasks/sprints/sprint-14-performance-and-visibility.md`       | Active sprint for runtime/generation performance metrics, Monitoring visibility, chat templates, and context-aware streamed decoding. |
| `tasks/sprints/sprint-15-windows-managed-wsl-packaging.md`    | Active future sprint plan for Windows Electron packaging with a managed WSL 2 backend runtime. |
| `tasks/sprints/sprint-16-vps-relay-and-live-inference-validation.md` | Active sprint for diagnosing the live AutoRelay reservation failure and proving VPS-relayed two-device inference. |
| `tasks/sprints/sprint-17-coverage-aware-serving.md` | Active sprint for adjacent-range route planning, live coverage recommendations, and serving/inference workflow guidance. |
| `tasks/archive/sprint-10-gated-model-local-import.md`          | Completed sprint for Hugging Face browser/device OAuth download of approved gated models into validated local imports, with manual folder import as fallback. |
| `tasks/archive/sprint-11-instruction-ready-model-expansion.md` | Completed sprint for instruction-ready/chat-ready model registry expansion, tuning labels, local-import contracts, and live TinyLlama generation validation. |
| `tasks/archive/sprint-12-auth-lifecycle-and-startup-cleanup.md` | Completed sprint for public-model auth isolation, failed-start cleanup, model cache cleanup, local expert routing, and safetensors/bin-index gated import validation. |
| `tasks/archive/sprint-09-node-lifecycle-token-validation-and-trace-analysis.md` | Completed sprint for safe node lifecycle, duplicate/multi-model local serving, HuggingFace token validation, trace analysis, and shutdown hardening. |
| `tasks/archive/sprint-01-stabilize-prototype.md`              | Completed sprint for stabilizing distributed inference.                              |
| `tasks/archive/sprint-02-architecture-adapter-and-parity.md` | Completed sprint for architecture-aware distributed inference and parity validation. |
| `tasks/archive/sprint-03-routing-and-dht-hardening.md`       | Completed sprint for DHT metadata validation, model-aware routing, and RPC UID safety. |
| `tasks/archive/sprint-04-local-system-validation.md`          | Completed sprint for local end-to-end distributed inference validation.              |
| `tasks/archive/sprint-05-client-workflow-controls.md`          | Completed sprint for client workflow controls, inference cancellation, hidden bootstrap, and monitoring. |
| `tasks/archive/sprint-06-routing-model-access-and-incentives.md` | Completed sprint for multi-node serving strategy, runnable-model semantics, and model-aware incentive accounting. |
| `tasks/archive/sprint-07-output-parity-and-quality.md`        | Completed sprint for HuggingFace-direct versus distributed-output parity and quality checks. |
| `tasks/archive/sprint-08-client-refinements-and-generation-diagnostics.md` | Completed sprint for client workflow refinements, monitoring redesign, token-level output diagnostics, generator stop control, and same-model non-overlapping local served nodes. |
| `tasks/archive/`                                             | Closed sprint audit trail.                                                           |

---

## `docs/` - documentation

| File                               | What's inside                                             |
| ---------------------------------- | --------------------------------------------------------- |
| `docs/INDEX.md`                    | Docs-only routing index.                                  |
| `docs/README.md`                   | Documentation landing page, project goal, and end vision. |
| `docs/REPO_MAP.md`                 | Source-file responsibilities.                             |
| `docs/CURRENT_ARCHITECTURE.md`     | Current architecture, network goal, and limitations.      |
| `docs/FLOWS.md`                    | Runtime flows.                                            |
| `docs/IMPLEMENTATION.md`           | Practical implementation roadmap and long-term phases.    |
| `docs/PETALS_COMPARISON.md`        | Comparison with Petals and project-owned public swarm direction. |
| `docs/NETWORK_REACHABILITY_AND_RELAY_REVIEW.md` | Review proposal for Petals-style direct reachability, automatic relay fallback, VPS infrastructure, security, and production validation. |
| `docs/COVERAGE_AWARE_SERVING.md` | Adjacent-range route selection, serving recommendations, stale-plan validation, and serving/inference workflow. |
| `docs/VPS_RELAY_OPERATIONS.md` | Persistent VPS relay installation, runtime validation, restart continuity, recovery, and rollback runbook. |
| `docs/WINDOWS_MANAGED_WSL_PACKAGING.md` | Sprint 15 packaging boundary, managed WSL distro strategy, state locations, launcher contract, and smoke-test plan. |
| `docs/USEFUL_WORK_INCENTIVES.md` | Signed useful-work receipt protocol, SQLite settlement policy, rollout modes, VPS deployment, and remaining acceptance gates. |
| `docs/TWO_DEVICE_ACCEPTANCE_EVIDENCE.md` | Sanitized relay/direct capture commands, route and ownership validation, shadow receipt evidence, standby non-payment, and manual gates. |
| `docs/LOCAL_SPLIT_ACCEPTANCE.md` | Isolated real two-peer OPT split inference, direct parity, accounting evidence, cleanup, and recorded local result. |
| `docs/TINYLLAMA_PERFORMANCE_BASELINE.md` | Bounded cached TinyLlama distributed timing probe and the active chat-template compatibility finding. |
| `docs/ERRORS_AND_DEBUGGING.md`     | Known errors, symptoms, and debugging guidance.           |
| `docs/VALIDATION_AND_TEST_PLAN.md` | Validation phase gates before trusting inference or adding advanced features. |
| `docs/architecture.md`             | Starter-system architecture summary adapted to this repo. |
| `docs/decisions.md`                | Append-only ADR log.                                      |

---

## `backend/` - FastAPI and distributed inference

| Path                     | What's inside                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------ |
| `backend/main.py`        | Uvicorn entry point.                                                                 |
| `backend/colab_worker.py` | Headless remote worker entry point with browser OAuth support for Colab and GPU hosts. |
| `backend/api/`           | FastAPI app, root `.env` loading, settings/token endpoints, Hugging Face OAuth/download helpers, and local model import registry. |
| `backend/client/`        | Distributed generation and remote sequential client.                                 |
| `backend/client/coverage.py` | Pure adjacent-range route planning, provider segmentation, snapshot revision, and serving recommendation logic. |
| `backend/incentives/` | Ed25519 application identities, canonical BLAKE3 receipt protocol, runtime submission queue, and SQLite settlement service. |
| `backend/node/`          | Serving node, layer loading, direct/relay transport, Hivemind RPC, and GPU monitoring. |
| `backend/node/reachability.py` | Petals-derived independent direct-reachability probe used before relay fallback. |
| `backend/node/relay_compat.py` | Hivemind 1.1.12 compatibility shim that selects configured trusted relays as static AutoRelay candidates. |
| `backend/models/`        | Model-specific adapter placeholders and architecture-specific preprocessing helpers. |
| `backend/traces/`        | Gitignored runtime JSON generation traces written by `/generator/trace`.             |
| `backend/bootstrap.py`   | Hivemind DHT bootstrap, circuit relay, and reachability-check node.                  |
| `backend/bootstrap_service_validate.py` | Validates non-secret VPS runtime evidence, deployed versions, relay state, and restart identity continuity. |
| `backend/acceptance_evidence.py` | Captures sanitized participant evidence and validates two-device route, transport, timing, receipts, ownership, and optional standby non-payment. |
| `backend/local_split_probe.py` | Runs cached-model local split inference through two real Hivemind serving peers and records parity, accounting, and cleanup evidence. |
| `backend/tinyllama_performance_probe.py` | Runs a bounded cached TinyLlama distributed timing, accounting, resource, and cleanup baseline. |
| `backend/relay_probe.py` | Minimal Hivemind-only circuit-relay reservation probe for Sprint 16 diagnostics.     |
| `backend/constants.py`   | Supported models, DHT constants, transport settings, and generation defaults.        |
| `backend/pyproject.toml` | Python project metadata and dependencies.                                            |
| `backend/uv.lock`        | Python dependency lockfile.                                                          |
| `backend/tests/test_gpu_monitor.py` | Focused runtime resource-monitor metric and failure-path tests.                       |
| `backend/tests/test_bootstrap_relay.py` | Focused public bootstrap relay and reachability-argument tests.                    |
| `backend/tests/test_coverage_serving.py` | Coverage route scenarios, recommendations, revisions, standby selection, and HTTP conflict regressions. |
| `backend/tests/test_sprint14_output_and_visibility.py` | Sprint 14 chat-template, context-aware streaming, and local lifecycle visibility regressions. |
| `backend/tests/test_useful_work_incentives.py` | Receipt signatures and commitments, RPC wrapper, settlement abuse rejection, durability, concurrency, pagination, and rollout-mode regressions. |
| `backend/tests/test_acceptance_evidence.py` | Evidence sanitization, route ownership, relay validation, replica selection, receipt deltas, and standby non-payment regressions. |
| `backend/tests/test_local_split_probe.py` | Local split probe option, range, evidence sanitization, and private-output regressions. |
| `backend/tests/test_tinyllama_performance_probe.py` | TinyLlama probe bounds, metric sanitization, and acceptance-contract regressions. |

---

## `frontend/` - Electron, React, TypeScript

| Path                                    | What's inside                                 |
| --------------------------------------- | --------------------------------------------- |
| `frontend/package.json`                 | Frontend dependencies and scripts.            |
| `frontend/README.md`                    | Frontend development, validation, Windows packaging, and launcher runtime notes. |
| `frontend/src/main/`                    | Electron main process and managed WSL backend launcher. |
| `frontend/src/main/backendLauncher.ts`  | Validated WSL detection, dependency sync, FastAPI health, diagnostics, and PID lifecycle state machine. |
| `frontend/src/preload/`                 | Electron preload bridge.                      |
| `frontend/src/renderer/`                | React renderer application.                   |
| `frontend/src/renderer/src/api/`        | HTTP and WebSocket API client.                |
| `frontend/src/renderer/src/pages/`      | Dashboard, Network, Chat, Monitoring, and Settings pages. |
| `frontend/src/renderer/src/components/` | Shared renderer components.                   |
| `frontend/src/renderer/src/assets/`     | CSS and static renderer assets.               |
| `frontend/tests/backendLauncher.test.ts` | Managed WSL launcher state, validation, quoting, health, and stop regressions. |
| `frontend/scripts/test-backend-launcher.mjs` | Temporary esbuild and Node test runner for launcher regressions. |
| `frontend/scripts/audit-windows-package.mjs` | Rejects secrets, local archives, model state, traces, receipts, and identities in Windows package contents. |

---

## `scripts/` - automation

| File                                      | What's inside                                                              |
| ----------------------------------------- | -------------------------------------------------------------------------- |
| `scripts/sprint-log/record-edit.mjs`      | Claude Code PostToolUse hook that records source/sprint edits.             |
| `scripts/sprint-log/check-sprint-log.mjs` | Claude Code Stop hook that enforces sprint-log updates after source edits. |

---

## `deploy/` - deployment operations

| Path | What's inside |
| --- | --- |
| `deploy/vps/` | Versioned systemd templates, environment examples, locked installers, relay validation, and useful-work settlement deployment. |

---

## `.claude/` - Claude Code config

| File                       | What's inside                              |
| -------------------------- | ------------------------------------------ |
| `.claude/settings.json`    | Permission allowlist and sprint-log hooks. |
| `.claude/sprint-sessions/` | Generated local hook state, gitignored.    |

---

## Maintenance Rule

When a file is created, moved, renamed, or changes scope, update this index in the same task. If a docs file changes, update `docs/INDEX.md` as well.
