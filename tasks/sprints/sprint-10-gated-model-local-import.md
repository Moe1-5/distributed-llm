# Sprint 10 - Gated Model Local Import

**Goal:** Replace the token-first gated-model workflow with a simple local import flow: after the user has Hugging Face approval and downloads the gated model files themselves, DistribLLM validates and uses that local model directory without storing Hugging Face login state or access tokens.
**Start:** 2026-07-09
**End:** TBD

---

## Problem Summary

Sprint 09 added Hugging Face token validation so gated model startup fails early and clearly, but the user experience is still heavier than it needs to be. For this project, we do not need to own the Hugging Face login, SSO, OAuth callback, or long-lived token lifecycle if the user can get approval directly from Hugging Face and provide the model files locally.

The simpler and safer flow is:

1. The user requests/receives gated-model approval on Hugging Face outside DistribLLM.
2. The user downloads the approved model repo through Hugging Face's own tools or website.
3. DistribLLM imports or points to the local model directory.
4. The backend validates that the directory matches a supported model and contains the files needed for serving/generation.
5. Runtime loading uses the local path with offline/local-file semantics instead of requiring an `hf_` token.

This keeps Hugging Face credentials out of the app for the main flow and avoids uploading large model weights through the local API when a folder selection or local path import is more efficient.

---

## Product Decision

- Primary gated-model path: local model directory import after external Hugging Face approval.
- No primary token paste flow for gated startup.
- No OAuth/SSO implementation in Sprint 10 unless local import proves insufficient.
- No Hugging Face password, session, OAuth refresh token, or long-lived API token is collected by DistribLLM for this flow.
- Keep token validation code only as a fallback/diagnostic path until the local import flow fully replaces it.
- Store only local model metadata and a local filesystem path or model-cache reference.

## In Progress

- [x] Start Sprint 10 as the current active sprint.

## Todo

- [ ] Add a local model registry for supported imported model directories, separate from Hugging Face token storage.
- [ ] Define the import contract for each supported model: expected `config.json`, tokenizer files, weight shards, architecture type, layer count, hidden size, and model identity.
- [ ] Add backend validation for a selected local model directory without loading full weights first.
- [ ] Add backend settings endpoints to add, list, inspect, and remove imported local model directories.
- [ ] Update node startup and generator startup to prefer an imported local path for gated models.
- [ ] Use offline/local-file loading semantics for imported models so startup does not require a Hugging Face token.
- [ ] Update the Network gated-model flow to guide users to import a local approved model directory instead of pasting a token.
- [ ] Add clear UI states for missing import, invalid import, wrong model, incomplete files, and valid local import.
- [ ] Keep local paths and import metadata out of version control and out of API responses where the full path is not needed.
- [ ] Add backend tests for valid import, missing files, wrong model identity, incomplete shards, and local-path startup selection.
- [ ] Add frontend typecheck coverage for the new API contract.
- [ ] Document that Hugging Face approval and model download happen outside DistribLLM.

## Deferred To Later Sprint

- Hugging Face OAuth, SSO, or IdP-style account connection.
- Automatic Hugging Face gated access requests.
- Automatic model download inside DistribLLM.
- Model repository upload over HTTP.
- Real incentives, rewards, receipts, anti-abuse checks, and settlement.
- Broader model-cache management, cleanup, disk quota controls, and multi-version model library features.

## Done

- [ ] None yet.

---

## Acceptance Criteria

- [ ] A user with Hugging Face approval can use a gated model in DistribLLM without pasting or storing an `hf_` token.
- [ ] The app can import or reference a local model directory and validate it before expensive model loading.
- [ ] Gated node startup and generator startup use the imported local path when available.
- [ ] Invalid, incomplete, or wrong-model local directories fail with clear actionable errors.
- [ ] The Network page presents local import as the primary gated-model path.
- [ ] Token/OAuth login is not required for the primary gated-model flow.
- [ ] No Hugging Face credential or login state is stored for the primary local import flow.

---

## Session Log

### 2026-07-09 - Replace Sprint 10 incentives with gated local import plan

- What changed: reformatted Sprint 10 around local gated-model import after external Hugging Face approval, with token paste and OAuth/SSO deferred out of the primary flow.
- Why: the user wants a simple and efficient gated-model path where DistribLLM does not take the user's Hugging Face login/token when local approved model files are enough.
- Status: Sprint 10 is planned but not started. Real incentives and settlement moved to a later sprint.

### 2026-07-09 - Start Sprint 10 branch

- What changed: started Sprint 10 and switched work to the `sprint-10-gated-model-local-import` branch.
- Why: Sprint 09 is closed and archived, and the next active work is the local gated-model import flow.
- Status: Sprint 10 is active; implementation work has not begun yet.
