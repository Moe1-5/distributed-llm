# Windows Managed WSL Packaging

**Status:** Sprint 15 design baseline
**Date:** 2026-08-12

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

The Electron main process should eventually expose these states to the renderer:

- `missing_wsl`: `wsl.exe` is unavailable or WSL 2 is not installed.
- `missing_distro`: the configured distro name is absent.
- `installing_backend`: uv sync or backend bootstrap is running.
- `starting_backend`: FastAPI has been launched but health has not passed.
- `ready`: `/status` responds from the WSL backend.
- `failed`: process exit, dependency install failure, port conflict, or health timeout.

The backend process should receive relay defaults through environment variables, not by mutating the user's root `.env` silently:

```text
DISTRIBLLM_NETWORK_MODE=auto
DISTRIBLLM_INITIAL_PEERS=<project VPS bootstrap multiaddr>
DISTRIBLLM_TRUSTED_RELAYS=<project VPS relay multiaddr>
DISTRIBLLM_AUTO_RELAY=true
```

## Stop and Update Behavior

Closing the Electron app should stop the FastAPI process it launched, but it should not delete model caches, local imports, OAuth state, or P2P identity. App uninstall may offer a separate cleanup action for WSL state.

Updates should be staged as:

1. stop the backend process,
2. update the application files,
3. run `uv sync` inside WSL when backend dependencies changed,
4. restart and poll health,
5. show a diagnostic state if startup fails.

## First Smoke Test

The first Windows package smoke test should prove only the desktop-to-WSL lifecycle:

1. packaged app opens,
2. WSL presence is detected,
3. backend starts without a manual WSL terminal,
4. `/status` responds,
5. relay probe can be run from the backend environment,
6. node startup surfaces relay failure details if no circuit address appears.

Two-device inference remains gated on Sprint 16 relay validation.
