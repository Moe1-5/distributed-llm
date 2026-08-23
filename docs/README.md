# DistribLLM Documentation Index

This folder documents the current codebase, the intended distributed inference flow, known failure modes, and a practical implementation direction inspired by Petals.

## Start Here

- [Repo Map](./REPO_MAP.md) - what each source file is responsible for.
- [Current Architecture](./CURRENT_ARCHITECTURE.md) - backend, frontend, DHT, node, RPC, and generation components.
- [Runtime Flows](./FLOWS.md) - bootstrap, serving, generator startup, inference, and dashboard polling.
- [Implementation Plan](./IMPLEMENTATION.md) - staged plan to make the prototype correct and more Petals-like.
- [Petals Comparison](./PETALS_COMPARISON.md) - how this project relates to `bigscience-workshop/petals`.
- [Errors and Debugging](./ERRORS_AND_DEBUGGING.md) - current errors, symptoms, root causes, and where to inspect.
- [Validation and Test Plan](./VALIDATION_AND_TEST_PLAN.md) - checks needed before trusting distributed inference.
- [Deployment and Live Testing](./DEPLOYMENT_AND_LIVE_TESTING.md) - executable rebuild rules, VPS bootstrap commands, participant setup, and future backend-bundled packaging notes.
- [Infrastructure Redundancy and Acceptance](./INFRASTRUCTURE_REDUNDANCY_ACCEPTANCE.md) - separated DHT/relay services, independent failure domains, controlled outage tests, and final architecture evidence.
- [System Code Analysis and Finalization Report](./SYSTEM_CODE_ANALYSIS_AND_FINALIZATION_REPORT.md) - current implementation inventory, ranked source findings, settlement outage analysis, unfinished work, and final desktop release gates.

The running issue log is kept at the repository root: [../ISSUES.md](../ISSUES.md).

## Current Project Goal

The codebase is building an Electron + FastAPI application for system-owned public/discoverable P2P LLM inference. The target network should feel similar to Petals in that outside devices can join and serve resources, but it should be isolated to this project rather than accidentally joining public Petals/IPFS infrastructure. Isolation comes from this project's bootstrap nodes, DHT prefixing, metadata contracts, model registry, routing rules, and future protocol changes.

One machine can serve a slice of transformer layers over Hivemind RPC, while another machine starts a generator that keeps embeddings and the LM head locally and routes hidden states through remote layer servers discovered through a DHT.

This is similar in spirit to Petals, but remains an MVP prototype:

- one backend process owns multiple local serving nodes and one generator
- nodes announce validated model/layer/RPC metadata into a project DHT
- the client builds coverage-aware adjacent routes, tracks provider health, and exposes readiness before inference
- OPT and Llama-family architecture behavior is explicit, with parity tooling for validation
- cancellation, trace diagnostics, OAuth gated downloads, and local imports exist
- health scoring, bounded route failover, RPC safety, shadow useful-work receipts, and developer API gating are implemented locally
- physical two-device relay generation, distributed KV-cache routing, production security, and backend-bundled Windows installation remain open

## Current Work

- Sprint 10: gated Hugging Face OAuth download/import implementation is complete; live end-to-end gated inference remains open.
- Sprint 11: TinyLlama chat and Llama 2 model expansion is implemented; live instruction-ready distributed inference remains open.
- Sprint 12: public model loading is isolated from stale OAuth state and failed startup cleanup is implemented; live TinyLlama retry remains open.
- Sprint 13: signed useful-work receipts, shadow settlement, and read-only accounting are implemented; live two-device receipt evidence and credit approval remain open.
- Sprint 14: runtime and generation metrics, model-aware chat templates, lossless streamed text deltas, and local-versus-network node visibility are implemented; live performance baselines remain open.
- Sprint 15: Electron can configure, launch, monitor, restart, and stop an existing-Ubuntu WSL backend; managed-distro import and clean-Windows packaged validation remain open.
- Sprints 16 through 26: relay acceptance, coverage routing, selective loading, health, RPC safety, failover, responsiveness, Windows acceptance, runtime validation, and credit-gated API work exist on feature branches or the active integration branch; their remaining live gates are catalogued in the system analysis report.

## End Vision

The long-term product goal is a public, project-specific distributed AI compute network:

- devices can join the network and serve LLM layer ranges for inference
- clients can request inference from models served by the network
- routing provides good fault tolerance when nodes disappear, slow down, or fail
- contributors can earn token-based incentives for useful work such as serving layers
- the monitor page visualizes connected nodes and active routes as an animated network
- users can request API keys for access to inferenced models
- optional later expansion: users can request distributed resources for training or fine-tuning LLMs instead of buying centralized server capacity

The near-term priority remains core correctness: make distributed split inference match the normal HuggingFace model path, then harden routing, readiness, cancellation, health checks, and live multi-node behavior before adding incentives or training features.
