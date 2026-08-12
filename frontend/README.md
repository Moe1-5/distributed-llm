# DistribLLM Frontend

Electron, Vite, React 19, and TypeScript desktop interface for local node control, Hugging Face model access, distributed route readiness, inference, and monitoring.

## Requirements

- Node.js/npm compatible with the lockfile
- DistribLLM FastAPI backend, normally at `http://127.0.0.1:8000`

## Install and Run

```bash
npm install
npm run dev
```

The renderer defaults to local backend URLs. Override before starting Vite when needed:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_WS_BASE_URL=ws://127.0.0.1:8000
```

Restart the frontend after changing Vite environment values.

## Validation

```bash
npm run typecheck
npm run test:launcher
npx eslint . --no-cache --quiet
```

## Build

```bash
npm run build:win
npm run audit:win-package
npm run build:win:installer # Windows build host, or Linux with Wine
npm run build:mac
npm run build:linux
```

`build:win` produces the unsigned portable Windows artifact under `dist`. The Windows package starts and monitors the backend through the managed WSL launcher configured in Settings.

## Runtime Notes

- Bootstrap infrastructure is not exposed as a normal client page.
- Hugging Face OAuth opens only approved Hugging Face URLs; WSL falls back to the Windows browser when Linux has no browser.
- Gated model folders are selected through Electron folder-picker IPC.
- The Inference page depends on backend generator and route readiness, not WebSocket state alone.
- Packaged Windows mode defaults to the project VPS in `auto` mode and requires an absolute WSL backend path on first run.
- Launcher configuration contains no Hugging Face credentials, model data, P2P identity, traces, or receipts.
