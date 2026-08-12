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
- [ ] Build the portable Windows executable and record filename, size, SHA-256, source commit, app version, Python version, and Hivemind version.
- [x] Run package-content audit and reject secrets, private runtime state, archives, and local absolute paths.
- [x] Exercise start/stop/restart/recovery behavior in the launcher test contract.
- [ ] Run the exact same artifact on two physical Windows devices for relay and direct acceptance capture.

## Test Plan

- Backend regression suite and changed Python compilation pass.
- Frontend type checks, production build, launcher tests, and package audit pass.
- The packaged app reports actionable missing-WSL, stopped-distro, backend-failure, relay-configuration, and model-access errors.
- Two acceptance reports bind to the same executable hash and source commit.
- Relay inference, direct inference, monitoring, cancellation, and clean shutdown are captured without private data.

## Acceptance Criteria

- [x] One portable Windows executable passes the package audit.
- [ ] Artifact metadata is reproducible and bound to the integrated source commit.
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
