# Active Sprints

> Read this on session start to know which sprint files to load.
> One line per active sprint. Remove a line only when the user explicitly closes the sprint.

| Sprint | File | Goal |
| --- | --- | --- |
| Sprint 04 | `tasks/sprints/sprint-04-local-system-validation.md` | Prove the local end-to-end distributed inference flow before advanced features. |
| Sprint 05 | `tasks/sprints/sprint-05-client-workflow-controls.md` | Clean up client workflow controls after local validation: hidden bootstrap, stop controls, cancellation, and monitoring. |
| Sprint 06 | `tasks/sprints/sprint-06-routing-model-access-and-incentives.md` | Define multi-node serving, runnable-model semantics, and model-aware incentive accounting. |
| Sprint 07 | `tasks/sprints/sprint-07-output-parity-and-quality.md` | Compare HuggingFace-direct and distributed outputs before trusting generated text quality. |
| Sprint 08 | `tasks/sprints/sprint-08-real-incentives-and-settlement.md` | Add real incentive rewards and settlement only after correctness, proof, health, and anti-abuse prerequisites are ready. |

## Completed Sprints

Completed sprint documents live in `tasks/archive/`.

| Sprint | File | Status |
| --- | --- | --- |
| Sprint 01 | `tasks/archive/sprint-01-stabilize-prototype.md` | Completed and archived. |
| Sprint 02 | `tasks/archive/sprint-02-architecture-adapter-and-parity.md` | Completed and archived. |
| Sprint 03 | `tasks/archive/sprint-03-routing-and-dht-hardening.md` | Completed and archived. |

---

When a sprint ends:

1. Move the sprint file to `tasks/archive/`.
2. Remove its row from this table.
3. Update `.context/current.md`.
