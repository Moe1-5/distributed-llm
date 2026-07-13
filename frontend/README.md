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
```

## Build

```bash
npm run build:win
npm run build:mac
npm run build:linux
```

## Runtime Notes

- Bootstrap infrastructure is not exposed as a normal client page.
- Hugging Face OAuth opens only approved Hugging Face URLs; WSL falls back to the Windows browser when Linux has no browser.
- Gated model folders are selected through Electron folder-picker IPC.
- The Inference page depends on backend generator and route readiness, not WebSocket state alone.
