# Architecture

> Starter-system architecture summary adapted to this repository.
> For the deeper current implementation details, see `docs/CURRENT_ARCHITECTURE.md`.

## Stack

| Layer | Technology | Reason |
|-------|------------|--------|
| Desktop frontend | Electron + Vite + React 19 + TypeScript | Cross-platform local dashboard and chat UI |
| Backend API | FastAPI + Uvicorn | Local HTTP/WebSocket control plane for node, generator, settings, and dashboard state |
| Distributed discovery | Hivemind DHT | Peer discovery and node metadata announcements |
| Distributed execution | Hivemind RPC experts | Remote transformer layer forwarding |
| Model runtime | PyTorch + HuggingFace Transformers | Local model components, layer loading, tokenization, and generation |
| Backend dependencies | uv | Locked Python dependency management |
| Frontend dependencies | npm | Electron/Vite script ecosystem |
| Testing | Pytest + TypeScript typecheck + parity/trace tooling | Regression coverage, runtime contracts, and staged live distributed validation |
| CI/CD | Not active | Future lint, typecheck, backend tests, and frontend build pipeline |

## Folder Structure

```text
backend/
  api/        FastAPI app and settings endpoints
  client/     distributed generation and remote layer routing
  node/       serving node, layer loader, RPC server, GPU monitor
  models/     model-specific adapter placeholders
frontend/
  src/main/   Electron main process
  src/preload Electron preload bridge
  src/renderer React app, pages, API client, assets
docs/         architecture, flows, implementation notes, validation plans
tasks/        active sprint, sprint files, lessons
scripts/      workflow automation and hooks
```

## Key Patterns

- State management: frontend uses local React state; backend uses a process-level local node registry plus one generator/client DHT.
- Data fetching: frontend centralizes HTTP/WebSocket calls in `frontend/src/renderer/src/api/client.ts`.
- Error handling: runtime errors should be explicit and user-facing where possible; inference readiness should move failures earlier.
- Validation: API boundaries, DHT metadata, route plans, model names, layer ranges, and WebSocket payloads require validation.
- Model support: architecture-specific behavior is selected through `backend/models/architecture_adapter.py`; each user-facing model still needs parity evidence.
- Distributed routing: clients validate metadata and execute a contiguous non-overlapping compatible route.
- Model access: public models load anonymously; gated models use browser OAuth for managed download followed by validated offline local runtime.

## External Services

| Service | Purpose | Docs URL |
|---------|---------|----------|
| HuggingFace Hub | Model weights/tokenizer downloads and gated model access | https://huggingface.co/docs |
| Hivemind | DHT and RPC layer-serving infrastructure | https://github.com/learning-at-home/hivemind |
| PyTorch | Tensor runtime and model execution | https://pytorch.org/docs |
| Electron | Desktop shell | https://www.electronjs.org/docs |
| FastAPI | Backend API and WebSocket server | https://fastapi.tiangolo.com/ |
