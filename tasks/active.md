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
