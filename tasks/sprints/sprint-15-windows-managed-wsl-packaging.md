# Sprint 15 - Windows Managed WSL Packaging

**Goal:** Package DistribLLM as a Windows desktop experience while isolating the Linux-dependent backend inside a managed WSL 2 runtime.
**Start:** 2026-08-12
**End:** TBD

---

## Problem Summary

DistribLLM should feel like a Windows desktop application, but the backend currently depends on a Linux-friendly stack: Hivemind, PyTorch CUDA wheels, Transformers, uv-managed Python dependencies, and WSL-tested networking behavior. Compiling the Python backend directly into a native Windows `.exe` would introduce a second dependency and networking surface before the distributed route is proven.

The packaging direction is therefore:

```text
Windows Electron application
  -> launches and monitors a managed WSL 2 backend
  -> backend runs in an isolated uv/Python environment
  -> P2P network defaults to VPS relay fallback
  -> direct LAN mode remains an advanced/dev path
```

This preserves the current working backend environment while giving testers a Windows-first install and launch flow.

## Product Decision

- Do not compile the Python/Hivemind backend into the Windows `.exe` for the first packaged release.
- Package the Electron frontend as the Windows desktop entry point.
- Run the backend inside a managed WSL 2 distro or managed WSL environment.
- Keep backend dependencies isolated inside WSL with uv.
- Use VPS relay-backed `auto` network mode as the default packaged configuration.
- Keep direct LAN mode available as an advanced/dev configuration for faster local routes.
- Treat native Windows backend support as a later compatibility investigation, not the Sprint 15 default.

## In Progress

- [x] Register Sprint 15 as the planned sprint after Sprint 14.
- [x] Define the initial managed WSL distro strategy and packaging boundary.

## Todo

- [x] Define the managed WSL distro strategy: import a project distro, provision an existing Ubuntu distro, or support both.
- [x] Design the Windows installer flow for checking WSL 2, installing/importing the backend environment, and reporting missing prerequisites.
- [x] Add an Electron main-process backend launcher that starts FastAPI through `wsl.exe`.
- [x] Add backend health polling and clear UI states for starting, ready, failed, and missing WSL runtime.
- [x] Add a packaged-app configuration path for VPS bootstrap and trusted relay addresses.
- [x] Add a first-run setup screen or settings flow for configuring relay/bootstrap addresses without editing `.env` manually.
- [x] Decide where model cache, OAuth token state, local imports, logs, and traces live in a managed WSL install.
- [x] Add safe stop/restart behavior for the WSL backend when the Electron app exits or updates.
- [x] Add diagnostics for WSL missing, distro missing, backend dependency install failure, backend port conflict, and relay config missing.
- [x] Produce a Windows packaged build with `electron-builder`.
- [ ] Smoke-test packaged launch on a clean Windows machine with WSL 2.
- [ ] Run two-device inference using the packaged app and VPS relay default configuration.
- [x] Document full install, uninstall, update, logs, model storage, and troubleshooting flows.

## Deferred To Later Sprint

- Native Windows Python backend support.
- Fully offline prebundled backend dependency image.
- Automatic WSL installation that requires administrator privileges.
- Signed production installer and auto-update channel.
- Enterprise deployment policy support.
- Store/Microsoft Store packaging.

## Acceptance Criteria

- [ ] A Windows user can launch DistribLLM from a packaged Electron app.
- [ ] The app can start the backend in WSL without the user manually opening a WSL terminal.
- [ ] The backend runs in an isolated, reproducible Python environment.
- [x] The app reports actionable errors when WSL, the managed distro, dependencies, or backend startup fail.
- [x] The packaged default network mode uses the VPS bootstrap and trusted relay configuration.
- [ ] Two Windows devices can complete distributed inference from the packaged flow in `auto` mode.
- [x] Direct LAN mode remains documented but is not required for the default user path.
- [x] No Hugging Face tokens, P2P identities, model weights, traces, or local archives are bundled accidentally.
- [x] Packaging documentation explains the boundary between Windows Electron, WSL backend, and VPS relay.

---

## Session Log

### 2026-08-23 - Reject packaged frontend and WSL backend revision drift

- What changed: added a packaged preflight and schema-three acceptance field that require the configured WSL backend checkout to be tracked-clean and equal to the commit embedded in the EXE before dependency synchronization or launch.
- Why: the existing-Ubuntu packaging boundary keeps backend source outside the executable, so matching EXE hashes alone did not prove that two physical devices executed the same backend code.
- Status: automated launcher and manifest checks pass. Acceptance uses only a clean package rebuilt from this commit and matching schema-three reports; clean-Windows and two-device physical acceptance remain open.

### 2026-08-11 - Create Windows managed WSL packaging sprint

- What changed: created Sprint 15 for packaging DistribLLM as a Windows Electron application with a managed WSL 2 backend runtime and VPS relay-backed default networking.
- Why: the backend has Linux/WSL-dependent packages, so the first Windows packaging path should isolate those dependencies instead of forcing the Python/Hivemind stack into a native Windows executable.
- Status: Sprint 15 is registered as the planned sprint after Sprint 14; implementation has not started.

### 2026-08-12 - Define managed WSL packaging baseline

- What changed: added `docs/WINDOWS_MANAGED_WSL_PACKAGING.md` with the Electron-to-WSL boundary, early existing-Ubuntu provisioning path, later managed-distro import path, runtime state ownership, launcher states, relay env contract, stop/update behavior, and first smoke-test scope; updated the root and docs indexes.
- Why: Sprint 15 needs a stable packaging contract before the Electron main-process launcher and installer flow are implemented.
- Status: the packaging strategy and state ownership decisions are documented; Electron launcher implementation, installer diagnostics, packaged build, and clean Windows smoke testing remain open.

### 2026-08-12 - Implement and package the managed WSL launcher

- What changed: added the Electron-main WSL 2 state machine, verbose distro/version checks, uv dependency sync, FastAPI process and health lifecycle, PID-based stop/restart, persisted non-secret relay configuration, typed preload IPC, Settings setup/diagnostics, the verified VPS `auto` defaults, fourteen focused launcher regressions, repeatable ASAR state auditing, and an unsigned portable Windows build.
- Why: Windows testers need one desktop entry point that can diagnose and control the Linux-dependent backend without opening a WSL terminal or editing `.env`, while preserving all model, identity, OAuth, trace, and receipt state outside the application package.
- Status: 122 backend tests plus 19 subtests, fourteen launcher tests, frontend type checks, lint, production build, a 1280-by-800 Electron Settings visual/overflow check, portable packaging, and an ASAR audit of 34 entries with zero forbidden entries pass. `DistribLLM-1.0.0-portable.exe` is 87,648,690 bytes with SHA-256 `1f1fec51489a27eb393ecef89053e283255c7187c407537ddfebf97f395999c8`. Clean-Windows WSL startup, the Windows-host NSIS installer, and two-device packaged inference remain open.

### 2026-08-13 - Add configuration-bound Windows acceptance reports

- What changed: added sanitized launcher transition and lifecycle evidence, a versioned report contract, Electron save-dialog IPC, a Settings export action, configuration-change invalidation, acceptance-report regressions, and a clean-Windows capture runbook.
- Why: clean-machine testing needs reviewable evidence that the packaged Windows app itself checked WSL 2, synchronized the isolated environment, reached backend health, and stopped cleanly without exposing backend paths, raw logs, relay addresses, tokens, identities, or receipts.
- Status: seventeen launcher tests, frontend node and renderer type checks, lint with zero errors, the production Electron build, portable Windows cross-build, and package audit pass. The rebuilt portable artifact is 87,652,120 bytes with SHA-256 `2f88a3169110820edb3f4af57394aabd045fd5307b25e7a1c5a4f7b280dd5328`; its ASAR has 34 entries and zero forbidden entries. A physical clean-Windows report and two-device packaged inference remain open, so Sprint 15 is not closed.

### 2026-08-14 - Guard WSL runtime directories during managed launch

- What changed: made the Electron WSL launcher initialize safe `XDG_CACHE_HOME`, `XDG_STATE_HOME`, and `UV_CACHE_DIR` defaults before dependency sync, backend start, and backend stop scripts; added launcher regressions for the sync/start/stop scripts.
- Why: a Windows packaged run proved the configured backend path existed, but dependency sync still failed with `mkdir: cannot create directory '': No such file or directory`, indicating an empty WSL runtime/cache directory environment rather than missing backend files.
- Status: launcher tests pass with 19 tests, and the Electron main-process TypeScript check passes. The existing opened executable still needs either the manual workaround or a rebuilt package to include this fix.

### 2026-08-14 - Preserve managed scripts across the Windows-to-WSL boundary

- What changed: encoded managed sync, start, and stop scripts before passing them through `wsl.exe`, decoded them inside WSL, prevented Node's echoed command text from overriding real stderr diagnostics, recognized smart-quoted runtime-directory failures, and added a final-argument round-trip regression.
- Why: the rebuilt Windows package showed the multiline Bash argument flattened at the process boundary. It then reported `uv` missing because the diagnostic parser matched wording embedded in the echoed command instead of the repeated empty-directory errors in stderr.
- Status: all 20 launcher tests, Electron node and renderer type checks, and the production build pass. The rebuilt portable package audit reports 36 ASAR entries and zero forbidden entries. `DistribLLM-1.0.0-portable.exe` is 87,655,591 bytes with SHA-256 `cbff4e06e697833456cfb66126e115ca45b6ec3761e766ffbeec1862970c1f80`; physical Windows startup must now be retried with this artifact.

### 2026-08-23 - Restore the desktop and loopback API trust boundary

- What changed: enabled Electron sandboxing, context isolation, and web security; replaced the opaque file renderer with a privileged path-confined application scheme; removed global origin and CORS header rewriting; narrowed CSP and preload exposure; validated every IPC sender and navigation target; added exact FastAPI HTTP, CORS, and WebSocket origin checks; and rejected non-loopback managed API binds.
- Why: the prior development configuration allowed any browser origin to reach sensitive lifecycle endpoints and gave a renderer compromise unnecessary Electron capabilities.
- Status: all four hundred sixty-five backend tests, twenty-five launcher/security tests, seven renderer-flow tests, both frontend type checks, the production build, and lint with zero errors pass; fifty-eight pre-existing formatting warnings remain outside the changed files. Packaged verification remains to be rerun from the resulting commit; clean-Windows and two-device physical acceptance remain open.

### 2026-08-23 - Bundle and version the managed WSL backend runtime

- What changed: added an explicit tracked backend payload allowlist, exact commit marker, per-file SHA-256 manifest, embedded manifest binding, package-resource auditing, atomic commit-versioned WSL installation, a separate frozen uv environment, XDG-owned mutable state, managed packaged `backend.env` loading, an explicit developer override, and schema-four Windows/final-manifest evidence that requires the packaged runtime.
- Why: the packaged desktop still required every tester to maintain a matching Git checkout and backend path, so the executable did not own the backend it claimed to launch and upgrades could drift across devices.
- Status: frontend node/web type checks, twenty-seven launcher/security tests, the backend-payload tests, seven renderer-flow tests, production build, focused backend acceptance/state checks, frozen isolated-environment dry-run, and a development Windows portable build/resource audit pass. Lint has zero errors and the same fifty-eight pre-existing formatting warnings. The complete backend unittest run reaches a pre-existing Python three point twelve asyncio executor-shutdown stall that reproduces with a one-line standard-library `asyncio.to_thread` program; the four hundred sixty-five-test baseline from the immediately preceding security commit remains green and all backend tests changed here pass. An exact clean commit-bound package audit and physical clean-Windows/two-device runs remain before Sprint 15 acceptance.

### 2026-08-23 - Bundle participant acceptance tools with the managed runtime

- What changed: expanded the explicit packaged backend allowlist to include the non-secret evidence, manifest, architecture, relay, tensor, lease, failover, split, and performance modules required by the physical runbooks; added required-file and exclusion regressions; and documented how packaged operators run those tools through the commit-versioned Python environment without a Git checkout.
- Why: the first backend-bundled candidate removed the checkout requirement for runtime operation but accidentally left the physical evidence commands dependent on a separate checkout, contradicting the managed-package boundary.
- Status: payload and package verification must be rerun, followed by a new exact commit-bound Windows candidate. Physical testing must use the corrected artifact rather than the earlier package.

### 2026-08-23 - Preserve portable extraction paths during WSL conversion

- What changed: replaced the raw `wsl.exe wslpath <Windows path>` argument with the launcher's existing base64-encoded Bash transport, assigns the decoded Windows path with strict shell quoting inside WSL, and added a regression covering a portable temporary path with backslashes and spaces.
- Why: the first physical launch of the checksum-bound package failed before backend installation because WSL argument rewriting changed `C:\Users\albad\AppData\Local\Temp\...\resources\backend-runtime` into `C:UsersalbadAppDataLocalTemp...resourcesbackend-runtime`. Passing only base64 characters through the raw Windows-to-WSL boundary prevents backslashes from being consumed.
- Status: all 28 launcher/security tests and both frontend TypeScript checks pass. The failing package is rejected. The replacement is bound to exact clean commit `7ff147d2a920a084399fee53f15a4dc3c52264ca`; its strict audit reports 38 ASAR entries, 65 checksummed backend runtime files, no forbidden entries, manifest SHA-256 `2542ee41f158c6e3becbae78b5efaf68078901ee1aee29bf09e607e7a2313da2`, and acceptance identity bound. The portable executable is `87919009` bytes with SHA-256 `879228394281ab221142a44ad84308127b5eabf5391a0295eb6c630d8b5ba16c`. Physical Device 1 retry is required before the external relay probe.

### 2026-08-23 - Force the managed virtual environment outside the immutable payload

- What changed: made the packaged launcher explicitly create its versioned virtual environment under mutable DistribLLM state, sync with `uv --active` against that environment, and invoke that environment's Python directly for backend startup; added an exact script regression.
- Why: the physical retry reached the packaged payload but `uv sync` attempted to create `.venv` inside the intentionally read-only backend directory and failed with permission denied. The source already set `UV_PROJECT_ENVIRONMENT`, but the new sequence makes the target active and unambiguous for the installed `uv` version.
- Status: a temporary dry run with the installed `uv 0.9.18` proved that `uv venv` plus `uv sync --active` uses the requested external environment. All 29 launcher/security tests and both frontend TypeScript checks pass. A replacement exact package and physical Device 1 launch are required before relay probing.
