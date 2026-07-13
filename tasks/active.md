# Active Sprints

> Read this on session start to know which sprint files to load.
> One line per active sprint. Remove a line only when the user explicitly closes the sprint.

| Sprint | File | Goal |
| --- | --- | --- |
| Sprint 10 | `tasks/sprints/sprint-10-gated-model-local-import.md` | Implementation complete for Hugging Face browser/device OAuth downloads into validated local imports; awaiting explicit close before archive. |
| Sprint 11 | `tasks/sprints/sprint-11-instruction-ready-model-expansion.md` | Add more supported instruction-ready/chat-ready models without fine-tuning the baseline model into instruction behavior. |
| Sprint 12 | `tasks/sprints/sprint-12-auth-lifecycle-and-startup-cleanup.md` | Prevent stale OAuth state from breaking public models and clean up every failed model startup. |
| Sprint 13 | `tasks/sprints/sprint-13-real-incentives-and-settlement.md` | Add real incentive rewards and settlement only after correctness, proof, health, anti-abuse, model-access, and model-quality prerequisites are ready. |

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

---

When a sprint ends:

1. Move the sprint file to `tasks/archive/`.
2. Remove its row from this table.
3. Update `.context/current.md`.
