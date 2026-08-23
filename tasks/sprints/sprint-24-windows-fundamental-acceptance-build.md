# Sprint 24 - Windows Fundamental Acceptance Build

**Goal:** Produce one audited Windows portable executable containing the accepted fundamental networking, serving, generator, inference, monitoring, and lifecycle behavior for repeatable multi-device testing.
**Start:** 2026-08-13
**End:** TBD
**Status:** In progress; a local audited portable build exists and physical Windows acceptance remains.

---

## Problem Summary

Sprint 15 established the Electron-to-managed-WSL packaging boundary and package audit, but the distributable must be rebuilt only after the live tensor and responsiveness fixes are integrated. Test devices need one identifiable artifact and one configuration contract so results cannot be mixed across commits or runtime versions.

## Dependencies and Boundaries

- Build from the integrated feature branch after Sprint 22 and Sprint 23 acceptance tests pass.
- Reuse Sprint 15's managed WSL launcher, package audit, and sanitized acceptance evidence.
- Never package `.env`, model caches, identities, receipts, traces, archives, or developer paths.
- The executable simplifies deployment; it does not remove the Windows, WSL 2, VPS, model-download, or physical-device acceptance requirements.

## Work Plan

- [x] Reconcile the launcher and package configuration with all fundamental backend changes.
- [x] Retain preflight checks for WSL availability, distro state, configured bootstrap relay, writable runtime paths, and backend readiness.
- [x] Build the portable Windows executable and record filename, size, SHA-256, runtime source commit, and app version; packaged-device evidence will add Python and Hivemind versions.
- [x] Run package-content audit and reject secrets, private runtime state, archives, and local absolute paths.
- [x] Exercise start/stop/restart/recovery behavior in the launcher test contract.
- [x] Bind each exported Windows report to the actual executable SHA-256, byte size, clean source commit, and application version.
- [ ] Run the exact same artifact on two physical Windows devices for relay and direct acceptance capture.

## Test Plan

- Backend regression suite and changed Python compilation pass.
- Frontend type checks, production build, launcher tests, and package audit pass.
- The packaged app reports actionable missing-WSL, stopped-distro, backend-failure, relay-configuration, and model-access errors.
- Two acceptance reports bind to the same executable hash and source commit.
- Relay inference, direct inference, monitoring, cancellation, and clean shutdown are captured without private data.

## Acceptance Criteria

- [x] One portable Windows executable passes the package audit.
- [x] Local artifact metadata is bound to the integrated runtime source commit.
- [ ] Both physical devices run the same executable hash and managed backend contract.
- [ ] The package completes fundamental relay and direct inference workflows.
- [ ] Stop, restart, failed startup, and cleanup behavior leave no orphan backend or stale UI state.
- [x] Remaining future features are not represented as part of the fundamental acceptance build.

---

## Session Log

### 2026-08-13 - Create Windows fundamental acceptance build sprint

- What changed: created the packaging sprint that binds one audited executable to the integrated fundamental feature set and two-device evidence.
- Why: multi-device testing is easier and more trustworthy when both machines run the same reviewed artifact after transport and responsiveness fixes land.
- Status: approved and queued after Sprints 22 and 23; no executable has been built from the integration branch yet.

### 2026-08-13 - Build and audit the integrated portable executable

- What changed: built the Windows x64 portable target after backend and frontend validation, ran the package-content audit, and verified the existing managed-WSL lifecycle contract through 17 launcher tests.
- Why: both physical test devices need one reviewed artifact after the relay and responsiveness fixes, without packaged secrets or developer runtime state.
- Status: the pre-commit build is 87,654,380 bytes with SHA-256 `4a49b057674a873d3d6e10c6ff5ee8e25174b66e139eef98fc24078a6e6f807e`; its audit found 34 ASAR entries and zero forbidden entries. A final post-commit rebuild/hash and two physical Windows device runs remain open.

### 2026-08-13 - Bind the post-commit portable artifact

- What changed: rebuilt `DistribLLM-1.0.0-portable.exe` from runtime source commit `7c75b84`, reran the package audit, and recorded the final local artifact identity.
- Why: the device-test executable must be distinguishable from the earlier package produced before the relay and responsiveness implementation was committed.
- Status: the artifact is 87,654,381 bytes with SHA-256 `22038dc3f6c045105e39d3051a8180d7b84bbd3c5ba4565480e607b5bacc4ba2`; the audit reports 34 ASAR entries and zero forbidden entries. Python/Hivemind runtime binding and relay/direct inference still require the two physical Windows runs.

### 2026-08-13 - Rebuild after reliability sprints

- What changed: rebuilt `DistribLLM-1.0.0-portable.exe` after Sprints 18 through 21 were integrated in runtime commit `b6aef305892686575e189cabed281d27a3b8dedc`.
- Verification: all 17 managed-WSL launcher tests passed, frontend type checks and production build passed, and the package audit found 34 ASAR entries with zero forbidden entries.
- Artifact: 87,654,070 bytes with SHA-256 `1c52dc54575d99971efdd921c26210493ac90d44449c431070b06c26f590c91e`.
- Status: this supersedes the earlier local artifacts for physical acceptance. Python/Hivemind runtime binding, relay failover, direct inference, and clean lifecycle evidence still require the two physical Windows devices.

### 2026-08-13 - Bind the responsive-generation acceptance artifact

- What changed: rebuilt `DistribLLM-1.0.0-portable.exe` from runtime commit `a9f6af8817bcacab21bd750cc6f517120c926213` after the cancellation, warm component cache, renderer partial-state, and correlated transport diagnostics were committed.
- Verification: 250 backend tests plus 54 subtests, frontend type checks and production build, 17 launcher tests, two renderer timing tests, and the package audit pass.
- Artifact: 87,652,373 bytes with SHA-256 `b5cfe37b29a501f431f1dcad7e6f8bb0b515e2da7dd136d0281b28d80edefd41`; its ASAR contains 36 entries and zero forbidden entries.
- Status: this artifact supersedes every earlier local executable for physical acceptance. Relay/direct inference and lifecycle evidence on two physical Windows devices remain open.

### 2026-08-13 - Make device reports self-identifying

- What changed: upgraded Windows acceptance reports to schema version two, streamed SHA-256 calculation over the actual portable wrapper, embedded the build source commit and dirty-source flag, and made the final manifest reject missing or mixed identities.
- Why: matching app versions do not prove two devices launched the same reviewed executable; manual filename or hash transcription is unnecessarily error-prone.
- Verification: 251 backend tests plus 54 subtests, 18 launcher tests, two renderer timing tests, frontend type checks, and the production build pass. Artifact hashing is streamed and exports only filename, size, and digest rather than a local path.
- Status: the final executable metadata will be replaced after this change is committed, rebuilt with explicit clean-source metadata, and audited.

### 2026-08-13 - Bind the schema-two device-test executable

- What changed: rebuilt `DistribLLM-1.0.0-portable.exe` from runtime commit `ecaf75066e279b04fcd3900eb69fe30a1e1a9ef3` with explicit clean-source build metadata.
- Verification: the package audit reports 36 ASAR entries and zero forbidden entries. Direct ASAR inspection confirms the exact runtime commit, clean-source flag, and report schema version two are embedded.
- Artifact: 87,653,718 bytes with SHA-256 `0c1d84e6566e9ff43a8cd057feb7aa2fb1ceb8d2c77301f7c27caaabd0e38dbe`.
- Status: this is the sole current executable for physical acceptance. Both devices must export passing schema-two reports with this hash before Sprint 24 can close.

### 2026-08-16 - Document live deployment and packaging commands

- What changed: added `docs/DEPLOYMENT_AND_LIVE_TESTING.md` and routed it through the root and docs indexes.
- Why: executable rebuilds, WSL backend updates, VPS bootstrap launch, relay probes, and future backend-bundled packaging needed one operator-facing checklist instead of scattered chat instructions.
- Status: documentation is ready; a new executable should be rebuilt only for latest Electron/UI/launcher behavior or final acceptance, while backend-only fixes still require updating and restarting the external WSL backend checkout.

### 2026-08-23 - Rebuild after runtime presentation and architecture completion

- What changed: rebuilt and audited the portable Windows application after the Sprint 27 runtime-presentation work and the Sprints 29 through 32 architecture source program were integrated; retained the existing schema-two executable/source identity contract.
- Why: the physical matrix must use one artifact that contains the final model-availability states, staged diagnostics, provider ownership explanations, async Trace workflow, and packaged Settings configuration boundary.
- Verification: the committed source passes 449 backend tests, 20 launcher tests, seven renderer-state tests, frontend type checking and production build, lint with zero errors, and package inspection with 36 ASAR entries and zero forbidden entries.
- Status: a final clean-source-metadata rebuild from the evidence-record commit is required next. Two-device direct, relay, lifecycle, session, and failure-injection evidence remains open.
