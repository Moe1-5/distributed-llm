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
| Testing | Not fully configured | Needs backend unit tests, split-path parity tests, and focused frontend checks |
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

- State management: frontend uses local React state; backend currently uses process-level globals for one node and one generator.
- Data fetching: frontend centralizes HTTP/WebSocket calls in `frontend/src/renderer/src/api/client.ts`.
- Error handling: runtime errors should be explicit and user-facing where possible; inference readiness should move failures earlier.
- Validation: API boundaries, DHT metadata, route plans, model names, layer ranges, and WebSocket payloads require validation.
- Model support: architecture-specific forward behavior should live under `backend/models/` rather than being implicit in generation code.
- Distributed routing: clients should execute a contiguous non-overlapping route, not every discovered node.

## External Services

| Service | Purpose | Docs URL |
|---------|---------|----------|
| HuggingFace Hub | Model weights/tokenizer downloads and gated model access | https://huggingface.co/docs |
| Hivemind | DHT and RPC layer-serving infrastructure | https://github.com/learning-at-home/hivemind |
| PyTorch | Tensor runtime and model execution | https://pytorch.org/docs |
| Electron | Desktop shell | https://www.electronjs.org/docs |
| FastAPI | Backend API and WebSocket server | https://fastapi.tiangolo.com/ |
