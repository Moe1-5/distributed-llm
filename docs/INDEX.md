# Docs Index

> Docs-only sub-index. The master file map for the whole repo is the root `INDEX.md`.
> Update this table whenever a doc is added, removed, or changes scope.

| Topic | File | What's inside |
|-------|------|---------------|
| Documentation landing page | `README.md` | Start-here guide, current project goal, and end vision |
| Source file map | `REPO_MAP.md` | Responsibilities for backend, frontend, placement, deployment tooling, tests, incentives, and generated artifacts |
| Current architecture | `CURRENT_ARCHITECTURE.md` | Backend, frontend, persistent network supervisor, transactional placement, role identities, publication/recovery policy, RPC paths, generation, useful-work settlement, and limitations |
| Runtime flows | `FLOWS.md` | Bootstrap, transactional placement, model access, lifecycle, routing, inference, useful-work receipts, remote workers, and cleanup |
| Implementation roadmap | `IMPLEMENTATION.md` | Current validation status, the Sprints 28-32 distributed-runtime architecture program, implemented reliability work, incentives, API access, and later training work |
| Petals comparison | `PETALS_COMPARISON.md` | Similarities, differences, public-swarm positioning, and what to borrow from Petals |
| Network reachability and relay review | `NETWORK_REACHABILITY_AND_RELAY_REVIEW.md` | Sprint 14 transport defect, Petals-derived implementation, Windows/WSL direct setup, locked VPS deployment procedure, live relay diagnosis, rollout, and acceptance criteria |
| Coverage-aware serving | `COVERAGE_AWARE_SERVING.md` | Adjacent-range route selection, atomic coordinator reservations, replica behavior, serving-plan API, desktop workflow, and incentives boundary |
| VPS relay operations | `VPS_RELAY_OPERATIONS.md` | Manual foreground launch, persistent systemd installation, machine-readable restart evidence, relay-probe binding, upgrade, rollback, recovery, backup, and monitoring procedure |
| Infrastructure redundancy acceptance | `INFRASTRUCTURE_REDUNDANCY_ACCEPTANCE.md` | Separated DHT/relay/control service deployment, independent-host requirements, packaged Settings configuration, cross-sprint lifecycle/placement scenarios, controlled failure matrix, recovery objectives, and hash-bound final evidence validation |
| Architecture failure matrix template | `ARCHITECTURE_FAILURE_MATRIX_TEMPLATE.json` | Fail-closed physical component, protocol, outage, topology, evidence-hash, and rollout-order template |
| Deployment and live testing | `DEPLOYMENT_AND_LIVE_TESTING.md` | Backend-bundled EXE rebuild rules, managed WSL runtime, VPS bootstrap, transactional placement, shadow settlement deployment, relay probes, and two-device checks |
| Current two-device live-test issues and production roadmap | `CURRENT_TWO_DEVICE_LIVE_TEST_ISSUES.md` | Timestamped lease-persistence evidence, implemented first-stage DHT/expert repair, production service architecture, packaging plan, and open physical acceptance gates |
| Relay tensor RPC reset handoff and test chronology | `RELAY_RECEIPT_RPC_STREAM_RESET_HANDOFF.md` | Physical test steps and outcome ledger, common sustained relay-path boundary, controlled tensor-probe runbook, correlated evidence, isolation matrix, and next-agent fix decision tree |
| System analysis and finalization | `SYSTEM_CODE_ANALYSIS_AND_FINALIZATION_REPORT.md` | Full source, runtime, security, deployment, validation, incentive-outage, and final desktop readiness audit |
| Windows managed WSL packaging | `WINDOWS_MANAGED_WSL_PACKAGING.md` | Sprint 15 backend-bundled existing-WSL boundary, managed operator configuration, versioned launcher contract, schema-four manifest binding, sanitized acceptance report, and clean-Windows runbook |
| Useful-work incentives | `USEFUL_WORK_INCENTIVES.md` | Signed receipt protocol, durable participant outbox, SQLite settlement rules, read-only credits, VPS rollout, and remaining two-device acceptance |
| Two-device acceptance evidence | `TWO_DEVICE_ACCEPTANCE_EVIDENCE.md` | Sanitized incentives-off-first relay/direct captures, Windows and VPS artifact assembly, hash-bound session and off-to-shadow validation, complete-route checks, standby non-payment, and manual gates |
| Local split acceptance | `LOCAL_SPLIT_ACCEPTANCE.md` | One-command real two-peer OPT split inference, parity, accounting, cleanup, and recorded evidence |
| TinyLlama performance baseline | `TINYLLAMA_PERFORMANCE_BASELINE.md` | Bounded real distributed timing probe and the current Transformers chat-template compatibility blocker |
| Errors and debugging | `ERRORS_AND_DEBUGGING.md` | Current OAuth, model access, CUDA/RAM, bootstrap, bounded RPC/reset diagnosis, route, and trace troubleshooting |
| Validation and testing | `VALIDATION_AND_TEST_PLAN.md` | Current regression status plus local, gated, instruction-ready, cleanup, and real multi-machine phase gates |
| Starter-system architecture summary | `architecture.md` | Stack, folder ownership, key patterns, and external services |
| Decision log | `decisions.md` | Append-only architecture decision records |
| Active sprint plans | `../tasks/active.md` | Current implementation and acceptance sprints, including relay stability, responsiveness, and Windows build work |
| Historical issue findings | `../ISSUES.md` | Dated runtime findings, verification evidence, resolutions, and confirmed open defects |

---

To add a new doc:

1. Create the file in `docs/`.
2. Add a row to this table with topic, filename, and one-line description.
3. Update the root `INDEX.md` if the new doc is important enough to route from the master map.
