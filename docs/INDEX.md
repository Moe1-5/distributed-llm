# Docs Index

> Docs-only sub-index. The master file map for the whole repo is the root `INDEX.md`.
> Update this table whenever a doc is added, removed, or changes scope.

| Topic | File | What's inside |
|-------|------|---------------|
| Documentation landing page | `README.md` | Start-here guide, current project goal, and end vision |
| Source file map | `REPO_MAP.md` | Responsibilities for backend, frontend, and generated artifacts |
| Current architecture | `CURRENT_ARCHITECTURE.md` | Backend, frontend, DHT, node, RPC, generation, and limitations |
| Runtime flows | `FLOWS.md` | Bootstrap, local gated-model import, serving, generator, streaming, dashboard, settings, and failure flows |
| Implementation roadmap | `IMPLEMENTATION.md` | Phased plan for core correctness, reliability, public-swarm operations, incentives, API access, and later training resources |
| Petals comparison | `PETALS_COMPARISON.md` | Similarities, differences, public-swarm positioning, and what to borrow from Petals |
| Errors and debugging | `ERRORS_AND_DEBUGGING.md` | Known errors, trace diagnostics, likely causes, and inspection points |
| Validation and testing | `VALIDATION_AND_TEST_PLAN.md` | Phase gates, parity checks, and smoke-test evidence needed before trusting distributed inference or adding advanced features |
| Starter-system architecture summary | `architecture.md` | Stack, folder ownership, key patterns, and external services |
| Decision log | `decisions.md` | Append-only architecture decision records |

---

To add a new doc:

1. Create the file in `docs/`.
2. Add a row to this table with topic, filename, and one-line description.
3. Update the root `INDEX.md` if the new doc is important enough to route from the master map.
