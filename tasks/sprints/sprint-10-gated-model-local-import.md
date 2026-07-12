# Sprint 10 - Hugging Face Connected Gated Model Import

**Goal:** Replace the token-paste gated-model workflow with a Hugging Face connection flow: the user clicks Connect Hugging Face, authorizes DistribLLM in the browser, and DistribLLM can download/import approved gated models locally without ever asking the user to paste an `hf_` token or password.
**Start:** 2026-07-09
**End:** 2026-07-10

---

## Problem Summary

Sprint 09 added Hugging Face token validation so gated model startup fails early and clearly, but a token text box is still the wrong user experience. DistribLLM should feel like a normal local app: connect your Hugging Face account in the browser, grant the minimum model-download permission, then let the app download or reuse the approved model files on the user's machine.

The important distinction:

- DistribLLM does not need the user's Hugging Face password.
- DistribLLM should not ask the user to manually paste an `hf_` personal access token.
- If DistribLLM downloads gated model files itself, it still needs an authenticated Hugging Face access token under the hood.
- That token should come from Hugging Face OAuth/device login, be scoped as narrowly as possible, be revocable, and be stored only in a local secure credential store or Hugging Face's own local auth cache.
- If the user does not want DistribLLM to hold any Hugging Face access token at all, the fallback is the already-built local folder import flow: download outside DistribLLM, then choose the folder.

Primary connected flow:

1. The user requests/receives gated-model approval on Hugging Face outside DistribLLM.
2. The user clicks **Connect Hugging Face** in DistribLLM.
3. DistribLLM opens Hugging Face's OAuth/device authorization page in the user's browser.
4. The user logs in on Hugging Face and grants read access for public gated repositories.
5. DistribLLM receives a scoped access token, verifies the account/access, and shows connected state without exposing the token.
6. The user clicks **Download approved model** for a supported gated model.
7. DistribLLM downloads the model into the Hugging Face cache or a managed local model library.
8. The existing local model validator imports that downloaded snapshot.
9. Runtime loading uses the validated local path with offline/local-file semantics after download.

This keeps passwords and pasted tokens out of the UI while still allowing DistribLLM to download only model files the connected Hugging Face account is already approved to access.

---

## Product Decision

- Primary gated-model path: Hugging Face browser/device OAuth connection plus in-app model download/import.
- Secondary no-token-held path: validated local folder import after the user downloads the model outside DistribLLM.
- No Hugging Face password collection.
- No token paste as the primary UX.
- No automatic gated access request inside DistribLLM; the user still requests/accepts model terms on Hugging Face.
- Request the least-privilege OAuth scope that can read public gated repositories the user has been granted access to.
- Store authentication state only in a secure local place, never in project files, logs, API responses, traces, or git.
- Allow disconnect/logout that removes local Hugging Face auth state used by DistribLLM.
- Keep the existing local import validator as the safety gate before any downloaded model can be served.

## In Progress

- [x] Start Sprint 10 as the current active sprint.
- [x] Re-plan Sprint 10 around connected Hugging Face OAuth/device login instead of manual-only local import.

## Todo

- [x] Confirm exact Hugging Face OAuth/device flow to use for a desktop Electron + FastAPI app.
- [x] Register/configure hook for a public Hugging Face OAuth app through `DISTRIBLLM_HF_OAUTH_CLIENT_ID`; actual account-side OAuth app creation remains an operator setup step.
- [x] Request only the minimal scopes needed for approved public gated model downloads.
- [x] Add backend Hugging Face auth service: start login, poll/complete login, check connected user, disconnect/logout, and report redacted auth status.
- [x] Store Hugging Face auth material outside repo-local JSON by default in user config storage, with a documented override; never expose token values in traces, logs, or frontend state.
- [x] Add backend download service using `huggingface_hub` snapshot/download APIs with the connected auth token.
- [x] Add download status, cancellation request, error reporting, and cache-backed resume/retry behavior through Hugging Face snapshot downloads.
- [x] Reuse the existing local model validator immediately after download so only supported complete snapshots become runnable.
- [x] Update Network UI from manual import-first to a guided flow: approval status, Connect Hugging Face, Download approved model, Validate, Serve.
- [x] Keep Browse local folder as an explicit privacy-first fallback for users who do not want DistribLLM to keep an auth token.
- [x] Add clear errors for not connected, not approved for the gated repo, missing model terms, expired/revoked auth, disk space issues, interrupted download, unsupported model, and incomplete files.
- [x] Add backend tests for auth-state redaction, disconnected download refusal, access-denied mapping, download-to-import handoff, and logout cleanup.
- [x] Add frontend typecheck coverage for the connected auth/download API contract.
- [x] Document the credential model: DistribLLM never sees the password, does not require pasted tokens, but OAuth/device login still gives the app a scoped access token for downloads.
- [x] Complete mocked/local validation for OAuth, download/import, startup handoff, disconnect, and offline local import behavior.

## Already Built Foundation

- [x] Backend local model import registry and validator.
- [x] Local-file startup wiring for node and generator loading.
- [x] Network local import UI for gated models.
- [x] Local import API/types, docs, and regression coverage.
- [x] Path privacy hardening for normal local import responses.
- [x] Stale local import revalidation before startup.
- [x] Electron folder picker for local imported model directories.

## Deferred To Later Sprint

- Automatic Hugging Face gated access requests or model-term acceptance inside DistribLLM.
- Model repository upload over HTTP.
- Real incentives, rewards, receipts, anti-abuse checks, and settlement.
- Broader model-cache management, cleanup, disk quota controls, and multi-version model library features.

## External Validation Needed

- Live-smoke with one approved gated model account and verify real Hugging Face OAuth authorization, real gated snapshot download, import validation, local startup, disconnect behavior, and offline startup after download. This requires a real public Hugging Face OAuth client ID in `DISTRIBLLM_HF_OAUTH_CLIENT_ID` and a Hugging Face account approved for the selected gated model.

## Done

- [x] Local model import fallback/foundation implemented and tested.
- [x] Hugging Face device OAuth backend and Network UI connection/download flow implemented.
- [x] Sprint 10 implementation is complete and locally verified.

---

## Acceptance Criteria

- [x] A user can click Connect Hugging Face, authorize in the browser, and return to DistribLLM without pasting an `hf_` token.
- [x] DistribLLM can download a supported gated model that the connected Hugging Face account has already been granted access to.
- [x] DistribLLM cannot download a gated model when the connected account lacks access, and the UI explains that access must be requested/accepted on Hugging Face.
- [x] The app validates the downloaded local snapshot before expensive model loading.
- [x] Gated node startup and generator startup use the validated local path after download.
- [x] Invalid, incomplete, unsupported, interrupted, or wrong-model downloads fail with clear actionable errors.
- [x] Disconnect removes DistribLLM's local Hugging Face auth state and future downloads require reconnecting.
- [x] Offline startup still works after a model has already been downloaded and validated.
- [x] Token values are never shown in UI, normal API responses, logs, traces, docs, or committed files.
- [x] The manual local folder import remains available as the privacy-first fallback.

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

### 2026-07-09 - Implement gated local model import flow

- What changed: added a backend local model import registry and validator, settings endpoints for inspect/import/list/remove, offline local-file loading for imported models, Network local import UI, frontend API types, settings copy, runtime flow docs, and regression tests.
- Why: Sprint 10 replaces token-first gated startup with a flow where users get Hugging Face approval and download model files outside DistribLLM, then validate and use a local directory without storing an `hf_` token.
- Status: backend tests pass, frontend typecheck passes, and the primary Sprint 10 checklist is implementation-complete; a live gated-model directory smoke test remains the next practical validation step.

### 2026-07-09 - Harden local import path privacy and startup coverage

- What changed: stopped normal inspect/import/list local-model API responses from echoing raw filesystem paths, kept the explicit single-record lookup as the only path-revealing response, and added regression coverage for endpoint path redaction plus gated generator startup using imported local paths without a Hugging Face token.
- Why: Sprint 10 requires keeping local paths out of API responses where the full path is not needed, and generator startup needed the same local-path regression coverage as node startup.
- Status: backend tests pass with 71 tests, frontend typecheck passes, and Python syntax compile passes; live validation with a real approved gated-model directory remains the practical smoke test.

### 2026-07-09 - Revalidate stored local imports before startup

- What changed: added preflight revalidation for registered local model directories before node or generator startup, returning a structured `local_model_import_invalid` error when a previously imported directory was moved, deleted, or made incomplete.
- Why: a local import can become stale after it is registered, and Sprint 10 needs invalid or incomplete local directories to fail clearly before expensive model loading.
- Status: backend tests pass with 74 tests, frontend typecheck passes, and Python syntax compile passes; live validation with a real approved gated-model directory remains the practical smoke test.

### 2026-07-09 - Fix Electron test environment local-import helpers

- What changed: added Chromium's `disable-dev-shm-usage` startup switch for Electron and changed the Network gated-model model-page helper from opening an external browser to copying/logging the Hugging Face model URL.
- Why: Sprint 10 testing showed Electron could crash when `/dev/shm` was unavailable and `xdg-open` failed noisily in an environment with no browser installed.
- Status: frontend typecheck passes; the local import UI should now be testable without relying on `/dev/shm` permissions or an installed system browser.

### 2026-07-09 - Reject Hugging Face URLs in local path import

- What changed: added frontend and backend validation that rejects `http://`, `https://`, and bare `huggingface.co/...` values in the local model path field, and changed the helper button to show the Hugging Face URL instead of copying it to the clipboard automatically.
- Why: testing showed the copied model URL could be pasted into the local path field, producing a confusing resolved path like `backend/https:/huggingface.co/...` instead of explaining that the user must download the approved files first.
- Status: backend tests pass with 75 tests, frontend typecheck passes, and Python syntax compile passes.

### 2026-07-09 - Replace URL helper with local folder picker

- What changed: added an Electron folder-picker IPC/preload API and replaced the Network local-import URL helper with a Browse action that fills the downloaded model directory path, while keeping manual path paste as a fallback.
- Why: testing showed the URL helper still made the model page URL feel like part of the import path flow; the primary action should be selecting a local downloaded folder.
- Status: backend tests pass with 75 tests, frontend typecheck passes, and Python syntax compile passes.

### 2026-07-10 - Re-plan Sprint 10 around Hugging Face connected downloads

- What changed: reframed Sprint 10 from manual local import as the primary path to a browser/device OAuth connection flow where DistribLLM can download approved gated models locally, then validate and serve them through the existing local import foundation.
- Why: the user wants a seamless Connect Hugging Face button and in-app download flow without pasting a token, while still understanding that any app-managed gated download requires scoped Hugging Face auth under the hood.
- Status: Sprint 10 is re-planned; local import remains the privacy-first fallback and already-built foundation, while OAuth connection, secure auth storage, download progress, access-denied handling, logout, and live gated-model smoke testing remain open implementation work.

### 2026-07-10 - Implement Hugging Face device OAuth download flow

- What changed: added backend Hugging Face device OAuth helpers and endpoints, automatic snapshot download/import using the saved connected-account token, Electron external Hugging Face URL opening, renderer API types, and a Network guided flow with Connect, auth code, Download approved model, Disconnect, and Browse folder fallback.
- Why: the user wants DistribLLM to automate Hugging Face authentication so they do not manually paste a token, while still downloading only gated models their Hugging Face account is approved to access.
- Status: backend tests pass with 78 tests, frontend typecheck passes, and Python API syntax compile passes; live OAuth/download validation still needs a real public Hugging Face OAuth client ID configured through `DISTRIBLLM_HF_OAUTH_CLIENT_ID` and an approved gated-model account.

### 2026-07-10 - Complete Sprint 10 implementation

- What changed: moved Hugging Face auth storage out of the repo by default into user config storage, added Hugging Face download jobs with redacted status/cancel endpoints, updated the Network UI to poll download jobs and request cancellation, added focused tests for storage, job status, cancellation, access-denied mapping, and token redaction, and marked Sprint 10 implementation complete.
- Why: the remaining Sprint 10 checklist items needed non-blocking download status/cancel behavior, safer local auth storage, and clear completion status before the sprint could be considered done.
- Status: backend tests pass with 84 tests, frontend typecheck passes, and Python API syntax compile passes; only external live validation with a real Hugging Face OAuth client ID and approved gated-model account remains outside local verification.

### 2026-07-12 - Add local environment file

- What changed: created the root `.env` file from the documented environment template with local development defaults and blank Hugging Face credential fields.
- Why: local OAuth setup needs a real `DISTRIBLLM_HF_OAUTH_CLIENT_ID` value, and the project only had `.env.example` as a template.
- Status: `.env` is present and gitignored; paste the public Hugging Face OAuth client ID there before restarting the backend/app.

### 2026-07-12 - Load root env in backend startup

- What changed: added a backend root `.env` loader and wired it into server, Hugging Face OAuth, and settings modules so `DISTRIBLLM_HF_OAUTH_CLIENT_ID` is picked up during normal backend startup.
- Why: smoke testing showed the `.env` file contained the OAuth client ID, but an already-running or normally-started backend could still report OAuth as unconfigured if the process environment did not load `.env`.
- Status: backend tests pass with 84 tests, frontend typecheck passes, Python API syntax compile passes, and a fresh backend on port 8001 reports Hugging Face OAuth `configured: true`; restart the existing port 8000 backend so the frontend stops showing "OAuth client ID is not configured."

### 2026-07-12 - Allow Hugging Face short OAuth URL

- What changed: updated the Electron external URL guard to allow both `https://huggingface.co/...` and Hugging Face's short `https://hf.co/...` URLs while still rejecting non-Hugging Face hosts.
- Why: Hugging Face device OAuth returns `https://hf.co/oauth/device`, but the app only allowed `huggingface.co`, causing the Connect action to fail after generating an auth code.
- Status: frontend typecheck passes and Python API syntax compile passes; restart Electron so the main-process URL guard reloads.

### 2026-07-12 - Gracefully handle missing system browser

- What changed: changed the Electron external URL opener to return `false` instead of throwing when the OS cannot open a browser, and updated the Network OAuth panel to show the Hugging Face verification URL with the auth code for manual opening.
- Why: WSL/headless environments may not have `xdg-open` browser targets installed, causing Hugging Face OAuth to fail even though the device code was generated correctly.
- Status: frontend typecheck passes and Python API syntax compile passes; restart Electron so the main-process URL handling reloads.

### 2026-07-12 - Add WSL browser fallback

- What changed: added a WSL-aware Electron fallback that opens Hugging Face OAuth URLs through the Windows default browser via `cmd.exe /c start` when Linux `xdg-open` cannot find a browser.
- Why: the development environment is WSL2 with no Linux browser installed, but Windows browser launchers are available under `/mnt/c/Windows`; users should not need to install a second Linux browser or keep Chrome already open.
- Status: frontend typecheck passes and Python API syntax compile passes; restart Electron so the main-process URL handling reloads.

### 2026-07-12 - Record live OAuth access blocker and stage Sprint 10

- What changed: staged the Sprint 10 OAuth, local import, frontend, docs, and sprint-tracking changes in git without pushing, and recorded the live Hugging Face device-login result.
- Why: the app successfully connected to Hugging Face, but the selected `meta-llama/Llama-3.2-1B` repo is still pending account approval; stale device-code polls can also return expired/not-found after multiple auth attempts.
- Status: Sprint 10 remains open for live validation; next live gated download should use an approved repo such as the accepted Llama 2 family, while Llama 3.2 must wait for Hugging Face approval.
