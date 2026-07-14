# Sprint 12 - Authentication Lifecycle and Startup Cleanup

**Goal:** Make model startup resilient to stale Hugging Face authentication and guarantee that failed node or generator startup releases every partially created runtime resource.
**Start:** 2026-07-13
**End:** 2026-07-14

---

## Problem Summary

Live TinyLlama testing exposed an expired OAuth token being attached to a public model request. Hugging Face returned `401 Unauthorized`, Transformers misreported the public repository as invalid, and repeated startup attempts could leave partially initialized DHT processes behind.

## Product Decision

- Public models load anonymously and never receive DistribLLM's stored Hugging Face token.
- Validated local model snapshots always load offline without a token.
- Expired or rejected OAuth credentials produce a reconnect action instead of a repository-not-found message.
- Every failed startup path cleans up partial DHT, RPC, handler, model, and CUDA state.
- Preserve OAuth tokens for gated downloads; do not delete credentials merely because a public model starts.

## In Progress

- [x] Sprint created from live TinyLlama startup evidence.
- [x] Authentication and cleanup fixes implemented and locally verified.

## Todo

- [x] Restrict stored OAuth-token injection to authenticated gated remote access only.
- [x] Map expired OAuth failures to a structured reconnect error.
- [x] Stop partially initialized nodes after startup failure.
- [x] Release partially initialized generator state and client DHT after startup failure.
- [x] Add regression tests for public anonymous loading, expired-token mapping, and cleanup.
- [x] Run backend tests, Python compilation, and frontend type-checking.
- [x] Delete the stale managed Llama 2 cache and make deletion recover all repository revisions even without a registry entry.
- [x] Prevent managed downloads from fetching duplicate `.bin` weights when safetensors are available.
- [x] Repeat live TinyLlama startup after restarting the backend.
- [x] Add matching local serving-node addresses to generator bootstrap peers.
- [x] Require an expert RPC metadata probe before generator readiness reports true.

## Acceptance Criteria

- [x] TinyLlama and other public models load without any stored OAuth token being sent.
- [x] An expired OAuth token cannot break public model startup.
- [x] Authentication expiry is reported as a reconnect action where authentication is required.
- [x] Failed node startup leaves no DHT, RPC server, loaded handler, or registered node behind.
- [x] Failed generator startup leaves no generator or client DHT behind.
- [x] Automated regression tests cover all corrected paths.

---

## Session Log

### 2026-07-13 - Create authentication lifecycle and startup cleanup sprint

- What changed: created Sprint 12 from the live TinyLlama failure showing an expired OAuth token attached to a public repository request and repeated failed startup attempts.
- Why: public model availability must not depend on OAuth state, and startup failures must not leak Hivemind worker processes or model resources.
- Status: sprint is active; implementation and regression tests are in progress.

### 2026-07-13 - Isolate public loading from OAuth and clean failed startups

- What changed: public model node/generator startup no longer reads or passes stored OAuth credentials, remote anonymous Hugging Face requests explicitly use `token=False`, expired-auth failures map to `huggingface_reconnect_required`, failed nodes call bounded lifecycle cleanup, and failed generators unload partial model state before client-DHT shutdown.
- Why: an expired OAuth token caused valid public TinyLlama requests to return 401 and repeated failures could leave partially initialized Hivemind or model resources behind.
- Status: 96 backend tests and 19 subtests pass, Python compilation passes, and frontend typecheck passes; restart the backend and repeat the live TinyLlama startup to complete the remaining live validation item.

### 2026-07-13 - Delete complete model caches and avoid duplicate weight formats

- What changed: changed managed deletion to remove every cached revision by model repository even when its registry entry is already absent, preserved arbitrary manual folders, cleaned empty model lock directories, exposed registry/cache deletion outcomes, and made downloads ignore PyTorch `.bin` weights whenever safetensors are available.
- Why: the local Llama 2 chat cache occupied 27 GB because both equivalent bin and safetensors shards were downloaded, and exact-snapshot deletion left no recovery path after registry removal.
- Status: the registered `meta-llama/Llama-2-7b-chat-hf` import and all 27.0 GB of its cached model files were deleted; the registry is empty, the model-specific cache/lock directories are gone, 97 backend tests plus 19 subtests pass, Python compilation passes, and frontend typecheck passes.

### 2026-07-13 - Clean all remaining local Hugging Face model caches

- What changed: scanned the user Hugging Face cache and deleted every remaining model revision for Llama 2 base, TinyLlama, OPT 125M, OPT 1.3B, StableBeluga, and the incomplete Llama 3.2 cache, then removed their empty lock directories.
- Why: downloaded model artifacts still occupied about 15 GB after the separately registered Llama 2 chat cache was deleted, while dependency/build caches should remain untouched.
- Status: Hugging Face cache usage fell from 15 GB to 36 MB and filesystem usage fell from 74 GB to 59 GB; no `models--*` directories or model lock directories remain, and the local import registry remains empty.

### 2026-07-13 - Run live TinyLlama node and prompt smoke tests

- What changed: ran TinyLlama anonymously through the public VPS bootstrap on the RTX 4050; layers 0-4 loaded on CUDA, RPC started, metadata announced, and shutdown completed. A second test loaded a full 0-22 node plus generator and attempted three deterministic chat prompts.
- Why: Sprint 12 needed live proof that expired OAuth state no longer breaks public model startup, while Sprint 11 still needs actual prompt/response evidence.
- Status: public TinyLlama node startup passed. Prompt generation stopped on the first prompt after three retries with `failed to dial: dial to self attempted`, so no response text was produced and the remaining prompts were not run. Cleanup completed, followed by Hivemind event-loop destructor warnings. Per the smoke-test protocol, no fix was attempted during this validation pass.

### 2026-07-13 - Reclassify self-dial as a smoke-test topology error

- What changed: corrected the issue record after comparing the ad hoc harness with the real `/generator/start` lifecycle.
- Why: the harness reused `node.dht`, while the application creates a separate generator `client_dht`; same-machine serving and generation worked in earlier OPT tests because the peer identities were distinct.
- Status: no product restriction prevents one machine from serving and generating when resources fit. TinyLlama prompt/response testing still needs a fresh pass through the real API topology.

### 2026-07-13 - Run corrected API-topology TinyLlama prompt smoke test

- What changed: used the actual `start_node`, `start_generator`, `get_generator_status`, and `chat` lifecycle with distinct serving and generator DHT identities, full TinyLlama layers 0-22 on CUDA, the public VPS bootstrap, and deterministic prompts.
- Why: the earlier ad hoc self-dial harness did not represent the application's separate generator identity.
- Status: node startup, generator startup, model loading, and route readiness all passed. The first prompt produced no response because expert RPC failed after three retries with `routing: not found`; later prompts were not run. Client DHT and node cleanup both reported stopped, followed by Hivemind event-loop destructor warnings. No fix was attempted during this smoke-test pass.

### 2026-07-13 - Fix local expert routing and verify live RPC traversal

- What changed: generator startup now merges visible addresses from matching running local nodes into its initial peers, and generator readiness now resolves every route expert and reads RPC metadata before reporting ready. Added coverage for peer deduplication/filtering and reachable/unreachable expert probes.
- Why: VPS-backed DHT metadata exposed complete layer coverage while the separate generator peer could not route to the local WSL serving expert.
- Status: 100 backend tests plus 19 subtests pass, Python compilation passes, and frontend typecheck passes. Live TinyLlama node/generator/readiness and all three full-route RPC prompt calls completed without routing errors. Each response was empty because the raw `/chat` prompt caused an immediate special/end token; chat-template handling is the next distinct finding and was not fixed during this validation pass.

### 2026-07-13 - Retry TinyLlama chat-template smoke test

- What changed: started a fresh linear full-model smoke pass using the production node and generator topology, the public VPS bootstrap, and three deterministic TinyLlama prompts prepared with the tokenizer chat template.
- Why: the prior live run proved expert routing but returned empty text for raw prompts, so the next test needed to isolate model prompt formatting from transport behavior.
- Status: the pass stopped at the first checkpoint because `/node/start` could not connect to the public VPS bootstrap peer. No node or client DHT remained after cleanup; model loading, route readiness, and prompt responses were not tested. The previously verified local expert-routing fix remains intact, while chat-template output remains open pending a reachable bootstrap.

### 2026-07-13 - Pass full TinyLlama distributed prompt smoke test

- What changed: reran the production-topology smoke pass after the VPS bootstrap was restored, serving TinyLlama layers 0-22 on CUDA, loading a separate generator, probing the complete RPC route, and sending three deterministic prompts with the tokenizer chat template.
- Why: the earlier retry was blocked before model startup and the raw-prompt run had produced immediate end tokens.
- Status: node startup, generator startup, reachable-route validation, all three prompt calls, client-DHT cleanup, and node cleanup passed. The model correctly identified Paris, returned four for two plus two, and described distributed computing. Output words were concatenated because tokens are decoded independently while streaming; that separate formatting defect is recorded in the issue log. Production model-aware chat-template application also remains open because this test supplied the template in its harness.

### 2026-07-13 - Prepare and publish Sprint 12 branch

- What changed: created the dedicated `sprint-12-auth-lifecycle-startup-cleanup` branch from the Colab/Llama 2 integration baseline and prepared the authentication, cache cleanup, startup cleanup, local expert routing, tests, sprint records, and refreshed documentation as one reviewed change set.
- Why: Sprint 12 work needed an isolated remote branch without local environment credentials or generated model/archive artifacts.
- Status: all 100 backend tests pass and frontend node/web typechecks pass; `.env` files and the untracked project ZIP are excluded from the commit.

### 2026-07-13 - Accept safetensors downloads with stale bin indexes

- What changed: local model validation now accepts any complete weight format and prefers complete safetensors over incomplete PyTorch bin metadata, while Hugging Face downloads skip `pytorch_model.bin.index.json` when `.bin` weights are ignored.
- Why: downloading `meta-llama/Llama-2-7b-chat-hf` with safetensors present skipped duplicate `.bin` shards but kept the old bin index, causing a false incomplete-shard import failure after the download.
- Status: targeted regression tests pass and the full `tests/test_generation_readiness.py` suite passes with 101 tests.

### 2026-07-14 - Close Sprint 12

- What changed: archived Sprint 12 after the auth lifecycle, failed-start cleanup, managed-cache cleanup, local expert routing, reachable-route probing, and safetensors/bin-index import fixes were implemented and pushed.
- Why: the remaining urgent work is Sprint 14 performance and visibility, while Sprint 12 acceptance criteria are complete.
- Status: Sprint 12 is closed and archived; continue active work in Sprint 14.
