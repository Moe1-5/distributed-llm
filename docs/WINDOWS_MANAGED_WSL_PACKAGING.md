# Windows Managed WSL Packaging

**Status:** Sprint 15 implementation and acceptance runbook
**Date:** 2026-08-13

## Packaging Boundary

DistribLLM's first Windows package should keep the Python/Hivemind backend inside WSL 2 and ship Electron as the native Windows entry point.

```text
Windows Electron app
  -> checks WSL availability
  -> provisions or opens a managed DistribLLM WSL distro
  -> starts uv run python main.py inside WSL
  -> polls FastAPI health on localhost
  -> renders backend status, setup errors, logs, and relay configuration
```

This avoids maintaining a second native-Windows Python backend while relay networking and live inference are still being validated.

## Managed Distro Strategy

Support two install modes in this order:

1. **Provision an existing Ubuntu WSL distro for early testers.** This is simpler to validate now and does not require distributing a large root filesystem image.
2. **Import a project-managed distro later.** This gives cleaner uninstall/update boundaries once the dependency set and relay flow are proven.

The Electron launcher should treat the distro name as configuration, defaulting to `Ubuntu` during development and `DistribLLM` for the later managed import path.

## Windows State Locations

| State | Owner | Default Location |
| --- | --- | --- |
| Electron app settings | Windows app | Electron user data directory |
| Backend source/runtime | WSL | distro home or managed install directory |
| Python dependencies | WSL | project `.venv` managed by uv |
| Model cache | WSL | Hugging Face cache or configured local model import path |
| OAuth token state | WSL/backend | configured token file, never bundled |
| P2P identity | WSL/backend | generated runtime file, never bundled |
| Logs and diagnostics | Both | Electron user data plus WSL backend log directory |

## Launcher Contract

The Electron main process exposes these states to the renderer:

- `needs_setup`: required distro, backend path, bootstrap, or relay configuration is absent or invalid.
- `missing_wsl`: `wsl.exe` is unavailable or WSL 2 is not installed.
- `missing_distro`: the configured distro name is absent.
- `installing_backend`: uv sync or backend bootstrap is running.
- `starting_backend`: FastAPI has been launched but health has not passed.
- `ready`: `/status` responds from the WSL backend.
- `failed`: process exit, dependency install failure, port conflict, or health timeout.

`frontend/src/main/backendLauncher.ts` owns this state machine. It validates loopback-only backend URLs and shell-safe WSL settings, checks `wsl.exe --status`, parses the installed distro list, optionally runs `uv sync --python 3.12`, launches FastAPI, and polls `/status`. A PID file under the WSL state directory lets Electron stop the exact backend process it launched instead of terminating the entire distro.

The preload bridge exposes typed status, configuration, start, stop, and restart IPC methods. The Settings page persists non-secret launcher configuration to `backend-launcher.json` in Electron's user data directory. The file contains distro/path/network settings only; OAuth tokens, model files, P2P identities, and receipts remain in WSL-owned storage.

The backend process should receive relay defaults through environment variables, not by mutating the user's root `.env` silently:

```text
DISTRIBLLM_NETWORK_MODE=auto
DISTRIBLLM_INITIAL_PEERS=<project VPS bootstrap multiaddr>
DISTRIBLLM_TRUSTED_RELAYS=<project VPS relay multiaddr>
DISTRIBLLM_AUTO_RELAY=true
```

Auto and relay modes require both a bootstrap peer and trusted relay in the saved packaged configuration. Direct mode permits empty relay fields for advanced LAN development. The package defaults both lists to the sprint sixteen verified project VPS multiaddress at `178.156.212.0:7001`; Settings keeps both fields editable so an operator can rotate the public service without editing `.env`. First run remains `needs_setup` until an absolute WSL backend path is supplied.

## Existing Ubuntu Installer Flow

The early-tester package uses an existing Ubuntu WSL distro:

1. Electron checks `wsl.exe --status` and reports `missing_wsl` without attempting administrator-level installation.
2. Electron parses `wsl.exe --list --verbose`, reports installed alternatives when the configured distro is absent, and rejects a selected distro that is not version two.
3. The user selects the distro and absolute backend path in Settings.
4. Electron validates the relay/bootstrap addresses and loopback API URL before writing configuration.
5. On start, Electron runs `uv sync --python 3.12` inside the backend directory and reports missing uv, missing project files, or dependency failure.
6. Electron launches `uv run --python 3.12 python main.py`, rejects an occupied API port, and waits for `/status`.
7. Closing or restarting the application terminates the PID recorded by the launcher without deleting WSL state.

The later managed-distro installer will import a versioned root filesystem and prefill the distro/path fields, but it will reuse the same launcher state machine and IPC contract.

`npm run build:win` produces the unsigned portable Windows artifact used for cross-build verification. `npm run build:win:installer` produces the NSIS setup executable on a Windows build host; electron-builder requires Wine when that NSIS target is invoked from Linux because it executes the generated installer to prepare its uninstaller. Signing and release-channel automation remain deferred.

## Stop and Update Behavior

Closing the Electron app stops the FastAPI process it launched, but it does not delete model caches, local imports, OAuth state, or P2P identity. App uninstall may offer a separate cleanup action for WSL state.

Updates should be staged as:

1. stop the backend process,
2. update the application files,
3. run `uv sync` inside WSL when backend dependencies changed,
4. restart and poll health,
5. show a diagnostic state if startup fails.

## Install, Uninstall, and State Retention

For the existing-Ubuntu tester path:

1. Install WSL 2 and an Ubuntu distro through Windows.
2. Place the DistribLLM backend checkout at an absolute path inside that distro and install `uv` there.
3. Launch the portable Windows artifact or install the NSIS package built on Windows.
4. Open Settings, confirm the distro, enter the WSL backend path, and start the managed backend.

Removing the Windows package deletes only Electron application files. It deliberately leaves the Ubuntu distro, backend checkout, uv environment, model cache, local imports, OAuth state, P2P identity, traces, receipts, and launcher PID state untouched. To remove those, stop DistribLLM first and delete the chosen WSL directories explicitly. The future project-managed distro path may offer an opt-in `wsl.exe --unregister DistribLLM` cleanup, but it must never run during an ordinary app uninstall.

## Logs and Diagnostics

- The Settings launcher status shows the latest state, diagnostic code context, process exit detail, and the last captured backend standard error tail.
- Electron launcher configuration is `backend-launcher.json` under the Windows Electron user data directory.
- The managed backend PID is `${XDG_STATE_HOME:-$HOME/.local/state}/distribllm/backend.pid` inside WSL.
- Backend application logs continue to use the backend's WSL runtime output and configured trace locations.
- Model weights and Hugging Face caches remain in WSL and never enter Electron user data or package resources.

Diagnostic mapping:

| State/code | Operator action |
| --- | --- |
| `configuration_invalid` | Correct the distro, absolute backend path, loopback URL, and relay fields in Settings. |
| `wsl_missing` | Install or enable WSL 2, then restart DistribLLM. |
| `distro_missing` | Select one of the installed distros shown in the status detail. |
| `distro_not_wsl2` | Convert the selected distro with `wsl.exe --set-version <name> 2`. |
| `uv_missing` | Install uv inside the selected distro. |
| `backend_path_invalid` | Point Settings at the directory containing `pyproject.toml`. |
| `backend_port_conflict` | Stop the process already using the configured loopback port. |
| `backend_health_timeout` | Inspect the captured backend error tail and WSL backend logs. |
| `backend_stop_failed` | Stop the recorded PID inside the selected distro before retrying. |

## Package Verification

`npm run test:launcher` exercises configuration validation, WSL output parsing, shell quoting, missing prerequisites, process launch, health readiness, port conflict, and safe stop/restart behavior. `npm run audit:win-package` inspects the generated ASAR and rejects environment files, archives, model state, traces, tokens, identities, and receipts. The portable artifact is generated output under `frontend/dist` and is not committed.

The Settings page can export a versioned JSON acceptance report after a launcher lifecycle. The report contains only application metadata, non-secret configuration counts, sanitized launcher state transitions, diagnostic codes, and boolean checks. It deliberately excludes the backend path, peer and relay addresses, process output, model data, tokens, identities, receipts, and local usernames.

The report passes only when one unchanged configuration has completed all of these checks:

1. the app is a packaged build running on Windows,
2. WSL is available,
3. the configured distro exists and uses WSL 2,
4. dependency synchronization was enabled and completed,
5. backend health reached ready, and
6. the managed backend subsequently stopped cleanly.

Saving launcher configuration resets accumulated evidence so one distro or relay setup cannot certify another. A report exported before clean stop remains useful for diagnostics but has `ok: false`.

## First Smoke Test

The first Windows package smoke test should prove only the desktop-to-WSL lifecycle:

1. packaged app opens,
2. WSL presence is detected,
3. backend starts without a manual WSL terminal,
4. `/status` responds,
5. relay probe can be run from the backend environment,
6. node startup surfaces relay failure details if no circuit address appears.

Two-device inference remains gated on Sprint 16 relay validation.

## Clean-Windows Acceptance Capture

On each physical Windows test device:

1. record the portable artifact SHA-256 and compare it with the reviewed branch artifact,
2. launch the portable application without manually starting the backend in WSL,
3. configure the WSL 2 distro and backend path in Settings, leave dependency sync enabled, and retain `auto` mode with the reviewed VPS bootstrap and relay values,
4. start the backend and wait for the launcher state to become `ready`,
5. complete the relay probe and two-device inference capture described in `TWO_DEVICE_ACCEPTANCE_EVIDENCE.md`,
6. return to Settings and stop the managed backend,
7. select **Export report** and retain the generated JSON beside the two-device evidence files.

The Windows acceptance report proves only the packaged Electron-to-WSL lifecycle. It does not by itself prove relay reservation, route ownership, tensor forwarding, inference parity, or two-device operation; those remain separate live evidence gates. After both device reports and the network evidence exist, use the final manifest workflow in `TWO_DEVICE_ACCEPTANCE_EVIDENCE.md` to reject mixed application versions, VPS runs, or participant sets before manual review.

The portable artifact produced from `feature/windows-package-acceptance-report` is 87,652,120 bytes with SHA-256 `2f88a3169110820edb3f4af57394aabd045fd5307b25e7a1c5a4f7b280dd5328`. Generated artifacts remain outside version control.

The current fundamental acceptance artifact produced from runtime commit `a9f6af8817bcacab21bd750cc6f517120c926213` is `DistribLLM-1.0.0-portable.exe`, 87,652,373 bytes, with SHA-256 `b5cfe37b29a501f431f1dcad7e6f8bb0b515e2da7dd136d0281b28d80edefd41`. Its audit found 36 ASAR entries and zero forbidden entries. This artifact supersedes every earlier package for Sprint 24 physical-device evidence; generated artifacts remain outside version control.
