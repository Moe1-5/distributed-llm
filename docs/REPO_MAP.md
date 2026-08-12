# Repo Map

Generated dependency folders, caches, downloaded weights, tokens, identities, and trace output are intentionally excluded from this map.

## Backend Entry Points

- `backend/main.py`: starts FastAPI/Uvicorn; defaults to port 8000.
- `backend/bootstrap.py`: stable Hivemind bootstrap, circuit-relay peer, and direct-reachability checker using `bootstrap.id`.
- `backend/colab_worker.py`: headless serving worker with model/layer/device/peer arguments and optional Hugging Face device OAuth.

## Backend API and Configuration

- `backend/api/server.py`: HTTP/WebSocket API, node registry, generator/client DHT lifecycle, route/readiness/status endpoints, parity/trace endpoints, settings, OAuth/download APIs, and bounded failed-start cleanup.
- `backend/api/env_loader.py`: loads the gitignored root `.env` for local backend processes.
- `backend/api/hf_oauth.py`: Hugging Face device OAuth, redacted connection state, managed snapshot-download jobs, cancellation, and download-to-import handoff.
- `backend/api/local_models.py`: local snapshot inspection, validation, registry, revalidation, and safe managed-cache deletion.
- `backend/api/settings.py`: OAuth token storage outside the repository by default, with a documented path override.
- `backend/constants.py`: supported models, tuning/shape/generation metadata, DHT timing/prefix constants, environment-backed initial peers, and typed direct/relay transport settings.

## Distributed Serving and Generation

- `backend/node/node.py`: direct-reachability selection, relay fallback, DHT, layer handler, RPC, announcements, pause/resume/delete lifecycle, and contribution state for one layer slice.
- `backend/node/reachability.py`: Petals-derived protocol that asks an independent peer to test direct libp2p dialing with relay disabled.
- `backend/node/block_loader.py`: architecture-aware selective safetensors planning/materialization, strict tensor validation, load-memory diagnostics, and explicit binary-format fallback policy.
- `backend/node/handler.py`: validated remote forward execution, architecture adapter integration, dtype/device conversion, and accounting.
- `backend/node/rpc_server.py`: legacy Hivemind expert plus optional receipt expert, unique RPC UIDs, and bounded server shutdown helpers.
- `backend/node/rpc_safety.py`: typed public-expert limits, pre-execution tensor/metadata validation, bounded admission, cooperative deadline errors, and multiprocessing-safe counters.
- `backend/node/gpu_monitor.py`: CPU, RAM, GPU, and VRAM sampling.
- `backend/client/sequential.py`: DHT metadata validation, coverage-aware route planning, legacy/receipt RPC lookup and fail-open retry, settlement submission, and route traces.
- `backend/client/health.py`: typed provider health state, DHT/protocol/transport signals, revision snapshots, and lifecycle-owned bounded metadata probes.
- `backend/client/generation.py`: tokenizer/local components, architecture preparation, distributed autoregressive generation, sampling controls, cancellation, unload cleanup, parity probes, and trace generation.
- `backend/client/distributed_model.py`: reserved model-level facade; currently not the primary runtime path.
- `backend/models/architecture_adapter.py`: explicit OPT and Llama-family adapter behavior used by generation/handler paths.
- `backend/models/llama/`: Llama-family extension placeholders.
- `backend/incentives/`: persistent application identity, canonical signed documents, BLAKE3 commitments, receipt construction, settlement runtime, and SQLite ledger service.

## Backend Tests and Local Artifacts

- `backend/tests/test_generation_readiness.py`: consolidated regression coverage for metadata, routing, lifecycle, local imports, OAuth/downloads, model registry, parity/readiness, cleanup, and auth isolation.
- `backend/tests/test_useful_work_incentives.py`: identity, signed receipt, abuse rejection, RPC wrapper, concurrency, durability, pagination, and rollout-mode coverage.
- `backend/pyproject.toml` and `backend/uv.lock`: Python 3.12+ dependency contract and locked environment.
- `backend/.local_models.json`: gitignored local import registry.
- User config Hugging Face token file: gitignored and outside the repository by default.
- `backend/traces/`: gitignored diagnostic JSON.
- Hugging Face cache: downloaded model snapshots; never committed.
- `bootstrap.id`: private stable peer identity; never committed or shared.

## Frontend

- `frontend/src/main/index.ts`: Electron window, WSL/browser fallbacks, safe Hugging Face external URL handling, and folder-picker IPC.
- `frontend/src/preload/`: typed bridge for renderer-safe Electron actions.
- `frontend/src/renderer/src/App.tsx`: page selection for Nodes, Network, Inference, Monitoring, Incentives, and Settings.
- `frontend/src/renderer/src/api/client.ts`: typed HTTP/WebSocket contracts for backend status, models, nodes, OAuth/downloads, imports, lifecycle, readiness, traces, and inference.
- `frontend/src/renderer/src/pages/Dashboard.tsx`: local hardware and node overview.
- `frontend/src/renderer/src/pages/Network.tsx`: model selection, layer serving, node lifecycle, OAuth/download/import flow, and generator startup.
- `frontend/src/renderer/src/pages/Chat.tsx`: readiness-aware streaming inference and cancellation.
- `frontend/src/renderer/src/pages/Monitoring.tsx`: route/coverage/network monitoring and accounting views.
- `frontend/src/renderer/src/pages/Incentives.tsx`: public identity, verified credits, receipt queue, useful positions, and settlement connectivity.
- `frontend/src/renderer/src/pages/Settings.tsx`: backend/Hugging Face/local model settings.
- `frontend/src/renderer/src/components/Sidebar.tsx`: product navigation; bootstrap is not exposed as a user tab.

## Project Operations and Documentation

- `CLAUDE.md` and `AGENTS.md`: repository workflow and coding-assistant rules.
- `INDEX.md`: canonical project file router.
- `.context/current.md`: short current state.
- `tasks/active.md`: active sprint routing.
- `tasks/sprints/`: open sprint plans and logs.
- `tasks/archive/`: immutable historical sprint records.
- `tasks/lessons.md`: active assistant corrections and internalized lessons.
- `docs/`: architecture, flows, roadmap, validation, troubleshooting, comparison, and decisions.

## Never Commit or Share

- `.env` files containing local values
- `.hf_token` or user config tokens
- `bootstrap.id`
- `.local_models.json`
- model weight files and Hugging Face caches
- runtime traces unless intentionally sanitized
- ad hoc project ZIP archives that may contain credentials or identities
