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
| Errors and debugging                      | `docs/ERRORS_AND_DEBUGGING.md`            |
| Validation plan                           | `docs/VALIDATION_AND_TEST_PLAN.md`        |
| Architectural decision history            | `docs/decisions.md`                       |
| Sprint-log hook scripts                   | `scripts/sprint-log/`                     |
| Backend API                               | `backend/api/server.py`                   |
| Backend settings/token storage            | `backend/api/settings.py`                 |
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
| `tasks/sprints/sprint-04-local-system-validation.md`          | Active sprint for local end-to-end distributed inference validation.                 |
| `tasks/archive/sprint-01-stabilize-prototype.md`              | Completed sprint for stabilizing distributed inference.                              |
| `tasks/archive/sprint-02-architecture-adapter-and-parity.md` | Completed sprint for architecture-aware distributed inference and parity validation. |
| `tasks/archive/sprint-03-routing-and-dht-hardening.md`       | Completed sprint for DHT metadata validation, model-aware routing, and RPC UID safety. |
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
| `docs/ERRORS_AND_DEBUGGING.md`     | Known errors, symptoms, and debugging guidance.           |
| `docs/VALIDATION_AND_TEST_PLAN.md` | Validation phase gates before trusting inference or adding advanced features. |
| `docs/architecture.md`             | Starter-system architecture summary adapted to this repo. |
| `docs/decisions.md`                | Append-only ADR log.                                      |

---

## `backend/` - FastAPI and distributed inference

| Path                     | What's inside                                                                        |
| ------------------------ | ------------------------------------------------------------------------------------ |
| `backend/main.py`        | Uvicorn entry point.                                                                 |
| `backend/api/`           | FastAPI app and settings/token endpoints.                                            |
| `backend/client/`        | Distributed generation and remote sequential client.                                 |
| `backend/node/`          | Serving node, layer loading, Hivemind RPC, and GPU monitoring.                       |
| `backend/models/`        | Model-specific adapter placeholders and architecture-specific preprocessing helpers. |
| `backend/bootstrap.py`   | Hivemind DHT bootstrap node.                                                         |
| `backend/constants.py`   | Supported models, DHT constants, and generation defaults.                            |
| `backend/pyproject.toml` | Python project metadata and dependencies.                                            |
| `backend/uv.lock`        | Python dependency lockfile.                                                          |

---

## `frontend/` - Electron, React, TypeScript

| Path                                    | What's inside                                 |
| --------------------------------------- | --------------------------------------------- |
| `frontend/package.json`                 | Frontend dependencies and scripts.            |
| `frontend/src/main/`                    | Electron main process.                        |
| `frontend/src/preload/`                 | Electron preload bridge.                      |
| `frontend/src/renderer/`                | React renderer application.                   |
| `frontend/src/renderer/src/api/`        | HTTP and WebSocket API client.                |
| `frontend/src/renderer/src/pages/`      | Dashboard, Network, Chat, and Settings pages. |
| `frontend/src/renderer/src/components/` | Shared renderer components.                   |
| `frontend/src/renderer/src/assets/`     | CSS and static renderer assets.               |

---

## `scripts/` - automation

| File                                      | What's inside                                                              |
| ----------------------------------------- | -------------------------------------------------------------------------- |
| `scripts/sprint-log/record-edit.mjs`      | Claude Code PostToolUse hook that records source/sprint edits.             |
| `scripts/sprint-log/check-sprint-log.mjs` | Claude Code Stop hook that enforces sprint-log updates after source edits. |

---

## `.claude/` - Claude Code config

| File                       | What's inside                              |
| -------------------------- | ------------------------------------------ |
| `.claude/settings.json`    | Permission allowlist and sprint-log hooks. |
| `.claude/sprint-sessions/` | Generated local hook state, gitignored.    |

---

## Maintenance Rule

When a file is created, moved, renamed, or changes scope, update this index in the same task. If a docs file changes, update `docs/INDEX.md` as well.
