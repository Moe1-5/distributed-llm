# Active Sprints

> Read this on session start to know which sprint files to load.
> One line per active sprint. Remove a line only when the user explicitly closes the sprint.

| Sprint | File | Goal |
| --- | --- | --- |
| Sprint 13 | `tasks/sprints/sprint-13-real-incentives-and-settlement.md` | Add real incentive rewards and settlement only after correctness, proof, health, anti-abuse, model-access, and model-quality prerequisites are ready. |
| Sprint 14 | `tasks/sprints/sprint-14-performance-and-visibility.md` | Measure runtime and generation performance, expose resource/route visibility, and fix model-aware chat formatting plus streamed token spacing. |
| Sprint 15 | `tasks/sprints/sprint-15-windows-managed-wsl-packaging.md` | Package the Windows Electron app while isolating the Linux-dependent backend inside a managed WSL 2 runtime. |
| Sprint 16 | `tasks/sprints/sprint-16-vps-relay-and-live-inference-validation.md` | Verify the VPS circuit relay, expert RPC reachability, and two-device Windows/WSL distributed inference. |
| Sprint 17 | `tasks/sprints/sprint-17-coverage-aware-serving.md` | Select complete adjacent layer routes and recommend useful serving ranges from live coverage. |
| Sprint 18 | `tasks/sprints/sprint-18-memory-efficient-selective-layer-loading.md` | Load only a worker's required layer slice and prove lower peak memory without changing generator semantics. |
| Sprint 19 | `tasks/sprints/sprint-19-continuous-provider-health.md` | Continuously probe provider RPC health and invalidate stale route readiness. |
| Sprint 20 | `tasks/sprints/sprint-20-rpc-resource-safety.md` | Bound and validate public expert RPC work before model execution. |
| Sprint 21 | `tasks/sprints/sprint-21-health-aware-route-failover.md` | Select healthy complete routes and fail over through bounded, accounting-safe attempts. |
| Sprint 22 | `tasks/sprints/sprint-22-relay-tensor-rpc-stability.md` | Stabilize relayed tensor RPC with correlated evidence and strictly bounded attempts. |
| Sprint 23 | `tasks/sprints/sprint-23-responsive-startup-and-generation.md` | Keep startup and generation responsive while removing repeated route and polling work. |
| Sprint 24 | `tasks/sprints/sprint-24-windows-fundamental-acceptance-build.md` | Produce an audited Windows executable for repeatable two-device fundamental acceptance. |
| Sprint 25 | `tasks/sprints/sprint-25-runtime-state-validation.md` | Make node, route, generator, and Monitoring readiness authoritative and reject unusable inference state. |
| Sprint 26 | `tasks/sprints/sprint-26-credit-gated-api-access.md` | Let useful-work credits unlock authenticated API access while preserving fair-use Electron chat. |
| Sprint 27 | `tasks/sprints/sprint-27-frontend-experience-and-model-discovery.md` | Clarify remote model availability, local serving roles, runtime status, diagnostics, and recovery across the desktop UI. |
| Sprint 28 | `tasks/sprints/sprint-28-peer-addressed-expert-protocol.md` | Bind every expert RPC to the selected peer and make duplicate-range providers coexist safely. |
| Sprint 29 | `tasks/sprints/sprint-29-persistent-network-supervisor.md` | Add one control-plane supervisor while preserving distinct worker and generator peers and evidence-based recovery. |
| Sprint 30 | `tasks/sprints/sprint-30-transactional-swarm-placement.md` | Allocate useful layer ranges through authoritative expiring reservations instead of UI snapshots. |
| Sprint 31 | `tasks/sprints/sprint-31-session-aware-kv-cache-inference.md` | Source-implemented bounded OPT prefill/decode sessions; real direct and relayed two-device acceptance remains open. |
| Sprint 32 | `tasks/sprints/sprint-32-infrastructure-redundancy-and-architecture-acceptance.md` | Separate infrastructure roles, add real redundancy, and prove the revised architecture under failures. |

## Completion Audit - 2026-08-23

All currently planned repository source work is implemented and locally verified. The remaining gates are intentionally not source checkboxes that can be completed on this development machine:

- **Source-complete with local automated acceptance complete:** Sprints 18, 19, 23, 26, and 27.
- **Source-complete with physical direct, relay, package, lifecycle, failover, session, or multi-host evidence still required:** Sprints 13 through 17, 20 through 25, and 28 through 32.
- **Explicitly deferred product work:** Sprint 13's claim/payout UI remains gated until real multi-machine settlement mechanics pass and the user approves a later payout sprint. Credit mode remains prohibited.
- **Closure rule:** every sprint stays in this active table until the user explicitly says to close that sprint, even after its source and physical evidence pass.

The physical gates are consolidated in `docs/INFRASTRUCTURE_REDUNDANCY_ACCEPTANCE.md`, `docs/TWO_DEVICE_ACCEPTANCE_EVIDENCE.md`, and `docs/WINDOWS_MANAGED_WSL_PACKAGING.md`. Full completion requires a second independent VPS/failure domain, the two existing Windows participants, a three-provider topology, one identical reviewed package, and the dependency-ordered incentives-off then shadow matrix.

## Completed Sprints

Completed sprint documents live in `tasks/archive/`.

| Sprint | File | Status |
| --- | --- | --- |
| Sprint 01 | `tasks/archive/sprint-01-stabilize-prototype.md` | Completed and archived. |
| Sprint 02 | `tasks/archive/sprint-02-architecture-adapter-and-parity.md` | Completed and archived. |
| Sprint 03 | `tasks/archive/sprint-03-routing-and-dht-hardening.md` | Completed and archived. |
| Sprint 04 | `tasks/archive/sprint-04-local-system-validation.md` | Completed and archived. |
| Sprint 05 | `tasks/archive/sprint-05-client-workflow-controls.md` | Completed and archived. |
| Sprint 06 | `tasks/archive/sprint-06-routing-model-access-and-incentives.md` | Completed and archived. |
| Sprint 07 | `tasks/archive/sprint-07-output-parity-and-quality.md` | Completed and archived. |
| Sprint 08 | `tasks/archive/sprint-08-client-refinements-and-generation-diagnostics.md` | Completed and archived. |
| Sprint 09 | `tasks/archive/sprint-09-node-lifecycle-token-validation-and-trace-analysis.md` | Completed and archived. |
| Sprint 10 | `tasks/archive/sprint-10-gated-model-local-import.md` | Completed and archived. |
| Sprint 11 | `tasks/archive/sprint-11-instruction-ready-model-expansion.md` | Completed and archived. |
| Sprint 12 | `tasks/archive/sprint-12-auth-lifecycle-and-startup-cleanup.md` | Completed and archived. |

---

When a sprint ends:

1. Move the sprint file to `tasks/archive/`.
2. Remove its row from this table.
3. Update `.context/current.md`.
