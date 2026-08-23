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
| Current backend/frontend/network architecture | `docs/CURRENT_ARCHITECTURE.md`        |
| Runtime flows                             | `docs/FLOWS.md`                           |
| Implementation roadmap                    | `docs/IMPLEMENTATION.md`                  |
| Petals comparison                         | `docs/PETALS_COMPARISON.md`               |
| Network reachability and relay review      | `docs/NETWORK_REACHABILITY_AND_RELAY_REVIEW.md` |
| Windows managed WSL packaging and acceptance capture | `docs/WINDOWS_MANAGED_WSL_PACKAGING.md` |
| Deployment, VPS services, and live testing | `docs/DEPLOYMENT_AND_LIVE_TESTING.md` |
| Infrastructure redundancy and failure acceptance | `docs/INFRASTRUCTURE_REDUNDANCY_ACCEPTANCE.md` |
| Current two-device live-test issues and production roadmap | `docs/CURRENT_TWO_DEVICE_LIVE_TEST_ISSUES.md` |
| Relay tensor RPC stream-reset handoff and physical test chronology | `docs/RELAY_RECEIPT_RPC_STREAM_RESET_HANDOFF.md` |
| Useful-work incentives and settlement | `docs/USEFUL_WORK_INCENTIVES.md`       |
| Two-device and hash-bound final acceptance evidence | `docs/TWO_DEVICE_ACCEPTANCE_EVIDENCE.md` |
| Local split acceptance                   | `docs/LOCAL_SPLIT_ACCEPTANCE.md` |
| TinyLlama performance baseline            | `docs/TINYLLAMA_PERFORMANCE_BASELINE.md` |
| Errors and debugging                      | `docs/ERRORS_AND_DEBUGGING.md`            |
| Validation plan                           | `docs/VALIDATION_AND_TEST_PLAN.md`        |
| Architectural decision history            | `docs/decisions.md`                       |
| Sprint-log hook scripts                   | `scripts/sprint-log/`                     |
| Backend API                               | `backend/api/server.py`                   |
| Backend checkout/packaged XDG env loading | `backend/api/env_loader.py`             |
| Backend Hugging Face OAuth/download       | `backend/api/hf_oauth.py`                 |
| Backend settings/token storage            | `backend/api/settings.py`                 |
| Local model import registry and packaged XDG state | `backend/api/local_models.py`       |
| Backend constants/model registry          | `backend/constants.py`                    |
| Node serving lifecycle                    | `backend/node/node.py`                    |
| Public RPC safety policy                  | `backend/node/rpc_safety.py`              |
| RPC safety tests                          | `backend/tests/test_rpc_safety.py`        |
| Selective worker layer loading            | `backend/node/block_loader.py`            |
| Selective layer loading tests             | `backend/tests/test_selective_layer_loading.py` |
| Remote layer routing                      | `backend/client/sequential.py`            |
| Stateful session protocol and cache       | `backend/node/session_protocol.py`, `backend/node/session_cache.py` |
| Health-aware route failover policy        | `backend/client/failover.py`              |
| Peer-addressed expert protocol            | `tasks/sprints/sprint-28-peer-addressed-expert-protocol.md` |
| Peer-addressed expert RPC tests            | `backend/tests/test_peer_addressed_rpc.py` |
| Persistent network supervisor implementation | `tasks/sprints/sprint-29-persistent-network-supervisor.md` |
| Transactional layer placement implementation | `backend/placement/`, `tasks/sprints/sprint-30-transactional-swarm-placement.md` |
| Session and KV-cache inference implementation | `tasks/sprints/sprint-31-session-aware-kv-cache-inference.md` |
| Infrastructure resilience and final architecture acceptance | `tasks/sprints/sprint-32-infrastructure-redundancy-and-architecture-acceptance.md` |
| Continuous provider health                | `backend/client/health.py`                |
| Provider health tests                     | `backend/tests/test_provider_health.py`   |
| Route failover tests                      | `backend/tests/test_route_failover.py`    |
| Real local Hivemind failover probe        | `backend/local_failover_probe.py`         |
| Local failover probe tests                | `backend/tests/test_local_failover_probe.py` |
| Physical legacy tensor payload sweep      | `backend/tensor_payload_probe.py`         |
| Tensor payload probe tests                | `backend/tests/test_tensor_payload_probe.py` |
| Distributed generation                    | `backend/client/generation.py`            |
| Frontend API client                       | `frontend/src/renderer/src/api/client.ts` |
| Persistent renderer diagnostics           | `frontend/src/renderer/src/api/diagnostics.ts` |
| Serving-plan freshness policy             | `frontend/src/renderer/src/api/servingPlanState.ts` |
| Model and runtime presentation contract   | `frontend/src/renderer/src/api/presentationState.ts` |
| Packaged executable identity capture      | `frontend/src/main/artifactIdentity.ts`   |
| Electron renderer, navigation, and CSP trust policy | `frontend/src/main/securityPolicy.ts` |
| Independent renderer refresh primitive    | `frontend/src/renderer/src/api/independentRefresh.ts` |
| Renderer partial-state, diagnostics, and serving-plan freshness tests | `frontend/tests/independentRefresh.test.ts` |
| Renderer partial-state test runner         | `frontend/scripts/test-renderer-flow.mjs`  |
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
| `tasks/active.md`                                            | Active sprint routing table and current source-versus-physical completion audit.     |
| `tasks/lessons.md`                                           | Assistant lessons: active and internalized.                                          |
| `tasks/sprints/sprint-13-real-incentives-and-settlement.md`   | Active future sprint plan for real rewards, contribution receipts, anti-abuse checks, and settlement. |
| `tasks/sprints/sprint-14-performance-and-visibility.md`       | Active sprint for runtime/generation performance metrics, Monitoring visibility, chat templates, and context-aware streamed decoding. |
| `tasks/sprints/sprint-15-windows-managed-wsl-packaging.md`    | Active future sprint plan for Windows Electron packaging with a managed WSL 2 backend runtime. |
| `tasks/sprints/sprint-16-vps-relay-and-live-inference-validation.md` | Active sprint for diagnosing the live AutoRelay reservation failure and proving VPS-relayed two-device inference. |
| `tasks/sprints/sprint-17-coverage-aware-serving.md` | Active sprint for adjacent-range route planning, live coverage recommendations, and serving/inference workflow guidance. |
| `tasks/sprints/sprint-18-memory-efficient-selective-layer-loading.md` | Active implemented sprint for architecture-aware partial worker loading, memory evidence, cleanup, and parity. |
| `tasks/sprints/sprint-19-continuous-provider-health.md` | Active implemented sprint for provider health states, background probes, and stale-readiness invalidation. |
| `tasks/sprints/sprint-20-rpc-resource-safety.md` | Active implemented sprint for typed expert RPC limits, validation, bounded admission, and safety counters. |
| `tasks/sprints/sprint-21-health-aware-route-failover.md` | Proposed sprint for health-aware complete routes, bounded failover, and accounting-safe retry behavior. |
| `tasks/sprints/sprint-22-relay-tensor-rpc-stability.md` | Active sprint for relayed tensor RPC diagnostics, bounded attempts, route reuse, and live inference acceptance. |
| `tasks/sprints/sprint-23-responsive-startup-and-generation.md` | Active sprint for startup jobs, progress, request deadlines, partial UI state, and generation-path performance. |
| `tasks/sprints/sprint-24-windows-fundamental-acceptance-build.md` | Active sprint for one audited Windows executable bound to fundamental two-device acceptance evidence. |
| `tasks/sprints/sprint-25-runtime-state-validation.md` | Active sprint for authoritative runtime readiness, lifecycle dependency validation, and staged diagnostics. |
| `tasks/sprints/sprint-26-credit-gated-api-access.md` | Active sprint for fair-use chat, verified-credit API access, and signed inference capabilities. |
| `tasks/sprints/sprint-27-frontend-experience-and-model-discovery.md` | Locally implemented remote-model discovery, role clarity, coherent runtime status, diagnostics, and desktop UI optimization; packaged review remains open. |
| `tasks/sprints/sprint-28-peer-addressed-expert-protocol.md` | Locally implemented peer-unique expert ownership, exact peer dispatch, and Hivemind 1.1.12 compatibility; physical rollout remains open. |
| `tasks/sprints/sprint-29-persistent-network-supervisor.md` | Implemented backend-owned discovery, explicit network state, last-good topology, publication ownership, evidence-based transport recovery, and exact-handle lifecycle ownership; physical validation remains open. |
| `tasks/sprints/sprint-30-transactional-swarm-placement.md` | Implemented source sprint for authenticated atomic layer reservations, provider lease states, expiry, and backend lifecycle integration; physical validation remains open. |
| `tasks/sprints/sprint-31-session-aware-kv-cache-inference.md` | Source-implemented sprint for bounded remote prefill/decode sessions, key/value caches, parity, diagnostics, and safe recovery; physical relay acceptance remains open. |
| `tasks/sprints/sprint-32-infrastructure-redundancy-and-architecture-acceptance.md` | Source-implemented separated infrastructure roles, independent redundancy configuration, failure injection, and final architecture evidence; physical deployment remains open. |
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
| `docs/CURRENT_ARCHITECTURE.md`     | Current architecture, persistent network control plane, role identities, publication/recovery policy, generation, and limitations. |
| `docs/FLOWS.md`                    | Runtime flows.                                            |
| `docs/IMPLEMENTATION.md`           | Practical implementation roadmap and long-term phases.    |
| `docs/PETALS_COMPARISON.md`        | Comparison with Petals and project-owned public swarm direction. |
| `docs/NETWORK_REACHABILITY_AND_RELAY_REVIEW.md` | Review proposal for Petals-style direct reachability, automatic relay fallback, VPS infrastructure, security, and production validation. |
| `docs/COVERAGE_AWARE_SERVING.md` | Adjacent-range route selection, serving recommendations, stale-plan validation, and serving/inference workflow. |
| `docs/VPS_RELAY_OPERATIONS.md` | Manual foreground launch, persistent VPS relay installation, machine-readable restart validation, external probe binding, recovery, and rollback runbook. |
| `docs/DEPLOYMENT_AND_LIVE_TESTING.md` | EXE rebuild rules, local WSL backend updates, VPS bootstrap and shadow settlement deployment, relay probes, two-device checks, and future backend-bundled packaging. |
| `docs/INFRASTRUCTURE_REDUNDANCY_ACCEPTANCE.md`, `docs/ARCHITECTURE_FAILURE_MATRIX_TEMPLATE.json` | Separated infrastructure deployment, physical outage procedure, recovery objectives, and fail-closed final evidence template. |
| `docs/CURRENT_TWO_DEVICE_LIVE_TEST_ISSUES.md` | Timestamped lease-persistence evidence, implemented first-stage DHT/expert repair, production service architecture, packaging plan, and open physical acceptance gates. |
| `docs/RELAY_RECEIPT_RPC_STREAM_RESET_HANDOFF.md` | Physical test chronology, common sustained relay-path boundary, request-correlated diagnostics, outcome ledger, controlled isolation matrix, and next-agent fix decision tree. |
| `docs/SYSTEM_CODE_ANALYSIS_AND_FINALIZATION_REPORT.md` | Full codebase and runtime-state audit, ranked technical findings, settlement connection-refused repair, unfinished work, and final desktop release sequence. |
| `docs/WINDOWS_MANAGED_WSL_PACKAGING.md` | Sprint 15 packaging boundary, managed WSL distro strategy, launcher contract, sanitized acceptance report, and clean-Windows runbook. |
| `docs/USEFUL_WORK_INCENTIVES.md` | Signed useful-work receipt protocol, durable participant outbox, SQLite settlement policy, rollout modes, VPS deployment, and remaining acceptance gates. |
| `docs/TWO_DEVICE_ACCEPTANCE_EVIDENCE.md` | Sanitized off-first relay/direct capture commands, hash-bound cross-sprint artifact assembly, route and ownership validation, later shadow receipts, and manual gates. |
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
| `backend/api/lifecycle_jobs.py` | Thread-safe long-running node/generator jobs, admission closure, deduplication, progress, cancellation, and bounded worker shutdown. |
| `backend/api/runtime_state.py` | Authoritative generator readiness state machine and bounded structured runtime diagnostic events. |
| `backend/client/`        | Distributed generation and remote sequential client.                                 |
| `backend/client/sequential.py` | Validated route construction, exact stateless/receipt/session dispatch, and one safe session-route rebuild from known history. |
| `backend/node/session_protocol.py` | Fixed-frame version-one session lifecycle and exact target metadata validation. |
| `backend/node/session_cache.py` | Bounded provider-owned OPT key/value cache lifecycle and process-shared diagnostics. |
| `backend/client/coverage.py` | Pure adjacent-range route planning, provider segmentation, snapshot revision, and serving recommendation logic. |
| `backend/client/rpc_policy.py` | Validated remote-expert attempt policy, failure classification, and safe receipt fallback rules. |
| `backend/incentives/` | Ed25519 identities, canonical BLAKE3 receipts, durable submission state, SQLite settlement, hashed developer API keys, credit reservations, and signed inference capabilities. |
| `backend/incentives/outbox.py` | Private identity-bound SQLite WAL outbox with restart recovery, expiry-safe rejection, active capacity, and bounded terminal history. |
| `backend/network/` | Persistent control-plane discovery, immutable last-good topology, role state, publication verification, and typed publication outcomes. |
| `backend/network/supervisor.py` | Backend-lifespan network supervisor with an independent cache-disabled DHT, passive topology snapshots, structured failures, role identities, and exact-handle cleanup/quarantine. |
| `backend/network/infrastructure.py` | Ordered participant DHT/relay configuration diagnostics and explicit redundancy/degraded state. |
| `backend/network/publication.py` | Hivemind 1.1.12-aware publication-result classifier that separates equivalent newer records, conflicts, weak acknowledgement, and verified local transport loss. |
| `backend/placement/` | Authenticated SQLite placement coordinator, HTTP client, reservation state machine, and backend heartbeat owner. |
| `backend/placement/service.py` | Separately deployable atomic range allocator with revisioned durable leases, expiry, idempotency, and audit-safe diagnostics. |
| `backend/placement/client.py` | Participant coordinator client, fail-closed errors, exact online attestation, secret-safe status, and lease heartbeats. |
| `backend/node/`          | Serving node, layer loading, direct/relay transport, Hivemind RPC, and GPU monitoring. |
| `backend/node/reachability.py` | Petals-derived independent direct-reachability probe with persistent stop requests and exact startup/shutdown ownership before relay fallback. |
| `backend/node/relay_compat.py` | Hivemind 1.1.12 compatibility shim that selects configured trusted relays as static AutoRelay candidates. |
| `backend/models/`        | Model-specific adapter placeholders and architecture-specific preprocessing helpers. |
| `backend/traces/`        | Gitignored runtime JSON generation traces written by `/generator/trace`.             |
| `backend/bootstrap.py`   | Explicit full-DHT, non-storage relay, and legacy combined Hivemind infrastructure roles. |
| `backend/bootstrap_service_validate.py` | Validates role, protocol, commit, effective flags, identity, address, failure domain, and restart continuity. |
| `backend/architecture_acceptance.py` | Validates redundant infrastructure, controlled failures, packaged lifecycle, pre-role discovery, role identity, placement race, protocol revisions, topology evidence, and rollout ordering. |
| `backend/control_service_validate.py` | Validates coordinator/settlement component health, revisions, failure domain, logs, and restart evidence. |
| `backend/acceptance_evidence.py` | Captures sanitized participant evidence and validates two-device route, transport, timing, receipts, ownership, and optional standby non-payment. |
| `backend/acceptance_manifest.py` | Cross-validates schema-four packaged Windows and integrity-verified WSL runtime revisions, VPS restart, bound relay probe, relay/direct inference, hash-bound incentives-off session ordering, and architecture artifacts while preserving manual approval gates. |
| `backend/local_split_probe.py` | Runs cached-model local split inference through two real Hivemind serving peers and records parity, accounting, and cleanup evidence. |
| `backend/tinyllama_performance_probe.py` | Runs a bounded cached TinyLlama distributed timing, accounting, resource, and cleanup baseline. |
| `backend/relay_probe.py` | Minimal Hivemind-only circuit-relay reservation probe for Sprint 16 diagnostics.     |
| `backend/tensor_payload_probe.py` | Runs a bounded, checkpointed legacy tensor sweep against one exact direct or relayed physical worker without chat sampling or receipts. |
| `backend/constants.py`   | Supported models, DHT constants, transport settings, stable role identity paths, and generation defaults. |
| `backend/pyproject.toml` | Python project metadata and dependencies.                                            |
| `backend/uv.lock`        | Python dependency lockfile.                                                          |
| `backend/tests/test_gpu_monitor.py` | Focused runtime resource-monitor metric and failure-path tests.                       |
| `backend/tests/test_bootstrap_relay.py` | Focused public bootstrap relay and reachability-argument tests.                    |
| `backend/tests/test_coverage_serving.py` | Coverage route scenarios, recommendations, revisions, standby selection, and HTTP conflict regressions. |
| `backend/tests/test_sprint14_output_and_visibility.py` | Sprint 14 chat-template, context-aware streaming, and local lifecycle visibility regressions. |
| `backend/tests/test_useful_work_incentives.py` | Receipt signatures and commitments, RPC wrapper, settlement abuse rejection, durability, concurrency, pagination, and rollout-mode regressions. |
| `backend/tests/test_acceptance_evidence.py` | Evidence sanitization, route ownership, relay validation, replica selection, receipt deltas, and standby non-payment regressions. |
| `backend/tests/test_acceptance_manifest.py` | Cross-sprint artifact compatibility, VPS/probe binding, off-mode session evidence binding, version mismatch, manual-gate, and private-output regressions. |
| `backend/tests/test_architecture_acceptance.py` | Independent failure-domain, protocol-binding, topology-redundancy, rollout-order, and private evidence regressions. |
| `backend/tests/test_infrastructure_config.py` | Ordered and duplicate participant infrastructure configuration state regressions. |
| `backend/tests/test_control_service_validate.py` | Coordinator/settlement health, protocol drift, log, and restart evidence regressions. |
| `backend/tests/test_local_split_probe.py` | Local split probe option, range, evidence sanitization, and private-output regressions. |
| `backend/tests/test_tinyllama_performance_probe.py` | TinyLlama probe bounds, metric sanitization, and acceptance-contract regressions. |
| `backend/tests/test_tensor_payload_probe.py` | Controlled-target validation, tensor byte accounting, repeated-canary ordering, legacy-only dispatch, and first-failure stop regressions. |
| `backend/tests/test_peer_addressed_rpc.py` | Exact Hivemind version, peer-scoped UID ownership, direct peer binding, and real duplicate-range provider regressions. |
| `backend/tests/test_lifecycle_jobs.py` | Long-running startup job progress, admission closure, deduplication, cancellation, shared shutdown deadline, failure, and prompt-response regressions. |
| `backend/tests/test_network_supervisor.py` | Asynchronous supervisor startup, last-good retention, role isolation, and idempotent shutdown regressions. |
| `backend/tests/test_network_supervisor_api.py` | Passive supervisor-backed status, node, model, and serving-plan API contract regressions. |
| `backend/tests/test_publication_classification.py` | Ambiguous Hivemind store result, independent readback, conflict, expiration horizon, and recovery-eligibility regressions. |
| `backend/tests/test_runtime_state_validation.py` | Generator state, tensor canary, local replica limits, route suspension, and dependent-node deletion regressions. |
| `backend/tests/test_transactional_placement.py` | Coordinator concurrency, complementary coverage, state transition, expiry, replay, restart, auth, and conflict regressions. |
| `backend/tests/test_session_cache.py` | OPT cache lifecycle and parity, exact session routes, recovery safety, generator payload bounds, and real Hivemind session RPC regression coverage. |
| `backend/tests/test_placement_client.py` | Authenticated client payload, publication attestation, heartbeat ownership, and secret-safe status regressions. |
| `backend/tests/test_placement_backend_integration.py` | Backend authoritative-plan, coordinator range override, failed-load release, and fail-closed integration regressions. |
| `backend/tests/test_api_access.py` | Hashed API-key eligibility, revocation, shared atomic credit reservations, shadow accounting, and signed capability regressions. |
| `backend/tests/test_env_loader.py` | Managed packaged-XDG and explicit operator environment loading without overriding process variables. |

---

## `frontend/` - Electron, React, TypeScript

| Path                                    | What's inside                                 |
| --------------------------------------- | --------------------------------------------- |
| `frontend/package.json`                 | Frontend dependencies and scripts.            |
| `frontend/README.md`                    | Frontend development, validation, Windows packaging, and launcher runtime notes. |
| `frontend/src/main/`                    | Electron main process and managed WSL backend launcher. |
| `frontend/src/main/backendLauncher.ts`  | Validated WSL detection, commit-versioned packaged backend installation, frozen isolated dependency sync, FastAPI health, diagnostics, PID lifecycle, developer override, and sanitized schema-four Windows acceptance evidence. |
| `frontend/scripts/prepare-backend-runtime.mjs` | Builds the sanitized tracked backend resource with an exact commit marker and per-file SHA-256 manifest. |
| `frontend/tests/backendRuntimePayload.test.mjs` | Verifies the backend runtime allowlist, commit stamp, exclusions, and checksum manifest. |
| `frontend/src/preload/`                 | Electron preload bridge.                      |
| `frontend/src/renderer/`                | React renderer application.                   |
| `frontend/src/renderer/src/api/`        | HTTP/WebSocket client, persistent sanitized diagnostics, independent refresh, serving-plan freshness, and explicit model/runtime presentation state. |
| `frontend/src/renderer/src/pages/`      | Dashboard, Network, Chat, Monitoring, and Settings pages. |
| `frontend/src/renderer/src/components/` | Shared renderer components.                   |
| `frontend/src/renderer/src/assets/`     | CSS and static renderer assets.               |
| `frontend/tests/backendLauncher.test.ts` | Managed WSL launcher state, validation, quoting, health, stop, and acceptance-report regressions. |
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
| `deploy/vps/` | Versioned separated DHT, relay, coordinator, and settlement units, environment templates, locked installers, and role validation. |
| `deploy/vps/distribllm-dht.service`, `deploy/vps/distribllm-relay.service` | Hardened role-specific systemd units with separate Unix identities and writable state. |
| `deploy/vps/dht.env.example`, `deploy/vps/relay.env.example` | Secret-free role, address, identity, failure-domain, peer, and pinned-Hivemind configuration. |
| `deploy/vps/run-infrastructure-peer.sh` | Validated launcher for full-DHT and non-storage relay roles. |
| `deploy/vps/install-infrastructure-service.sh` | Role-aware installer that refuses to start unresolved configuration. |
| `deploy/vps/validate-infrastructure-service.sh` | Restart, identity, status, role, and effective-p2pd-flag validator. |
| `deploy/vps/validate-control-service.sh` | Coordinator/settlement health, journal, failure-domain, commit, and restart validator. |
| `deploy/vps/distribllm-placement.service` | Hardened systemd unit template for the transactional placement authority. |
| `deploy/vps/placement.env.example` | Secret-free placement host, port, database, TTL, and placeholder-secret configuration. |
| `deploy/vps/run-placement.sh` | Validated placement service launcher using the locked service-owned environment. |
| `deploy/vps/install-placement-service.sh` | Root installer for the placement environment, state directories, and systemd unit. |

---

## `.claude/` - Claude Code config

| File                       | What's inside                              |
| -------------------------- | ------------------------------------------ |
| `.claude/settings.json`    | Permission allowlist and sprint-log hooks. |
| `.claude/sprint-sessions/` | Generated local hook state, gitignored.    |

---

## Maintenance Rule

When a file is created, moved, renamed, or changes scope, update this index in the same task. If a docs file changes, update `docs/INDEX.md` as well.
