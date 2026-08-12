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
