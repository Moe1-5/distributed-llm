# Docs Index

> Docs-only sub-index. The master file map for the whole repo is the root `INDEX.md`.
> Update this table whenever a doc is added, removed, or changes scope.

| Topic | File | What's inside |
|-------|------|---------------|
| Documentation landing page | `README.md` | Start-here guide, current project goal, and end vision |
| Source file map | `REPO_MAP.md` | Responsibilities for backend, frontend, and generated artifacts |
| Current architecture | `CURRENT_ARCHITECTURE.md` | Backend, frontend, DHT, node, RPC, generation, and limitations |
| Runtime flows | `FLOWS.md` | Bootstrap operations, public/gated model access, OAuth/download/import, node lifecycle, routing, inference, remote workers, and cleanup |
| Implementation roadmap | `IMPLEMENTATION.md` | Phased plan for core correctness, reliability, public-swarm operations, incentives, API access, and later training resources |
| Petals comparison | `PETALS_COMPARISON.md` | Similarities, differences, public-swarm positioning, and what to borrow from Petals |
| Errors and debugging | `ERRORS_AND_DEBUGGING.md` | Current OAuth, model access, CUDA/RAM, bootstrap, RPC/NAT, route, and trace troubleshooting |
| Validation and testing | `VALIDATION_AND_TEST_PLAN.md` | Current regression status plus local, gated, instruction-ready, cleanup, and real multi-machine phase gates |
| Starter-system architecture summary | `architecture.md` | Stack, folder ownership, key patterns, and external services |
| Decision log | `decisions.md` | Append-only architecture decision records |

---

To add a new doc:

1. Create the file in `docs/`.
2. Add a row to this table with topic, filename, and one-line description.
3. Update the root `INDEX.md` if the new doc is important enough to route from the master map.
