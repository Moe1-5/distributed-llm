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

The running issue log is kept at the repository root: [../ISSUES.md](../ISSUES.md).

## Current Project Goal

The codebase is building an Electron + FastAPI application for system-owned public/discoverable P2P LLM inference. The target network should feel similar to Petals in that outside devices can join and serve resources, but it should be isolated to this project rather than accidentally joining public Petals/IPFS infrastructure. Isolation comes from this project's bootstrap nodes, DHT prefixing, metadata contracts, model registry, routing rules, and future protocol changes.

One machine can serve a slice of transformer layers over Hivemind RPC, while another machine starts a generator that keeps embeddings and the LM head locally and routes hidden states through remote layer servers discovered through a DHT.

This is similar in spirit to Petals, but currently much simpler:

- one backend process owns global node and generator state
- nodes announce layer ranges manually into the DHT
- the client chains all discovered nodes in layer order
- model-specific forward logic is not yet faithfully reproduced
- route selection, health checks, cancellation, and session/cache management are still missing

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
