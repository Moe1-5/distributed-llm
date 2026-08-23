# DistribLLM Frontend

Electron, Vite, React 19, and TypeScript desktop interface for local node control, Hugging Face model access, distributed route readiness, inference, and monitoring.

## Requirements

- Bun 1.3.10, as declared by `package.json` and the tracked `bun.lock`
- DistribLLM FastAPI backend, normally at `http://127.0.0.1:8000`

## Install and Run

```bash
bun install --frozen-lockfile
bun run dev
```

The renderer defaults to local backend URLs. Override before starting Vite when needed:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_WS_BASE_URL=ws://127.0.0.1:8000
```

Restart the frontend after changing Vite environment values.

## Validation

```bash
bun run typecheck
bun run test:launcher
npx eslint . --no-cache --quiet
```

## Build

```bash
bun run build:win
bun run audit:win-package
bun run build:win:installer # Windows build host, or Linux with Wine
bun run build:mac
bun run build:linux
```

`build:win` produces the unsigned portable Windows artifact under `dist`. The Windows package starts and monitors the backend through the managed WSL launcher configured in Settings.

## Runtime Notes

- Bootstrap infrastructure is not exposed as a normal client page.
- Hugging Face OAuth opens only approved Hugging Face URLs; WSL falls back to the Windows browser when Linux has no browser.
- Gated model folders are selected through Electron folder-picker IPC.
- The Inference page depends on backend generator and route readiness, not WebSocket state alone.
- Packaged Windows mode defaults to the project VPS in `auto` mode and installs its integrity-verified backend payload automatically; an absolute WSL path is only an explicit developer override.
- Launcher configuration contains no Hugging Face credentials, model data, P2P identity, traces, or receipts.
