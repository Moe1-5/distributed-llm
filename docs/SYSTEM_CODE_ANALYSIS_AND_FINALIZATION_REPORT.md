# System Code Analysis and Finalization Report

**Reviewed:** 2026-08-19
**Branch:** `feature/diagnose-sprint14-health-regression`
**Stage:** MVP prototype, not feature-complete and not production-ready
**Primary target:** A Windows Electron application controlling a Linux backend in WSL 2, with project-owned VPS discovery, relay, and settlement services

## 1. Purpose And Review Boundary

This report records the current implementation, known evidence, unresolved defects, security and deployment risks, missing product work, and the shortest defensible path to a final desktop application.

The review covered:

- the FastAPI lifecycle and management API;
- model registration, Hugging Face authorization, local model validation, and loading;
- Hivemind DHT discovery, direct and relayed transport, expert RPC, and metadata publication;
- coverage-aware route planning, provider health, failover, generation, and monitoring;
- RPC admission and tensor validation;
- useful-work identities, signed receipts, settlement, credits, and developer API access;
- Electron main, preload, renderer, managed WSL launcher, packaging, and acceptance evidence;
- VPS bootstrap and settlement service deployment;
- active sprint records, validation documents, tests, and current Git state.

This began as a source and documentation review. After the settlement repair,
all 17 incentives tests passed in one run, including the real Hivemind receipt
RPC. The three focused retry, idempotency, and rollout-mode tests also passed
together, and the Electron TypeScript type check passed. This still did not
constitute a fresh physical two-device test, a penetration test, a dependency
vulnerability scan, a load test, or approval of credit mode.

## 2. Repository Scale And Current Git State

The reviewed repository contains approximately:

- 26,703 lines of backend Python;
- 7,440 lines of frontend, Electron, and frontend-test TypeScript/JavaScript;
- 18 backend test files with 271 test methods;
- 2 frontend test files with 22 Node test cases;
- systemd units, VPS installers, validators, package-audit scripts, local split probes, relay probes, and acceptance-evidence tooling.

The active branch began this review with 33 modified or untracked entries. It includes changes spanning runtime health, Hivemind RPC, node announcements, frontend diagnostics, deployment scripts, sprint documents, two untracked ZIP archives, an untracked deployment guide, and an untracked `frontend/package-lock.json`.

Consequences:

- the current tree is an integration workspace, not a releasable source state;
- a final acceptance executable cannot honestly claim a clean source commit yet;
- unrelated changes must be reconciled instead of discarded;
- generated archives must remain outside the release and version-control boundary;
- the project must choose and commit one frontend package-manager lock contract.

## 3. Current System Architecture

### 3.1 Windows Desktop Layer

Electron owns the desktop window, local settings, acceptance report export, and managed backend lifecycle. The renderer provides Nodes, Network, Inference, Monitoring, Incentives, and Settings views. The preload bridge exposes a small typed IPC API.

The managed launcher currently:

1. checks that `wsl.exe` exists;
2. validates the selected distro and requires WSL version two;
3. validates an absolute backend path inside WSL;
4. optionally runs `uv sync --python 3.12`;
5. launches FastAPI through WSL;
6. polls the loopback backend status endpoint;
7. records launcher state and a backend PID;
8. stops only the backend process it owns;
9. exports sanitized Windows acceptance evidence.

The portable executable and NSIS target already exist. The NSIS configuration already requests a desktop shortcut. The current package does not contain or provision the Python backend.

### 3.2 Participant Backend

FastAPI runs in WSL and owns process-local runtime state:

- a registry of local serving nodes;
- one active generator;
- one generator/client DHT;
- GPU and process monitoring;
- lifecycle jobs and runtime readiness state;
- useful-work identity and submission state;
- local developer API access state.

One backend can host multiple serving slices, including adjacent ranges, different models, and confirmed exact replicas. One generator model can be active per backend process.

### 3.3 P2P Data Plane

The current inference path is:

```text
prompt
  -> tokenizer and local embeddings
  -> complete adjacent route of remote transformer slices
  -> local final normalization and LM head
  -> token selection and streaming
```

Workers publish model, layer range, RPC UID, transport, loading, safety, health, and optional receipt capability metadata through the project DHT namespace. The route planner validates metadata and selects non-overlapping adjacent ranges from layer zero to model depth.

Participants can use:

- direct reachability when a public or LAN path is verifiably available;
- trusted VPS circuit relay fallback in auto mode;
- explicit relay mode for acceptance and diagnosis.

The bootstrap VPS discovers peers and provides circuit relay service. It does not execute model layers.

### 3.4 Incentive Plane

Each enabled participant owns a local Ed25519 application identity independent of its P2P identity. Signed presence binds the application key to the current peer ID. Selected workers sign useful-work receipts and the generator countersigns accepted output.

The VPS settlement service:

- validates signatures, commitments, identities, route membership, model policy, ranges, counters, timestamps, nonces, and replay constraints;
- writes append-only receipt and policy data to SQLite in WAL mode;
- changes no balance in shadow mode;
- appends integer credits in credit mode;
- exposes policy, account, entry, and receipt endpoints.

Electron chat remains free. The separate OpenAI-compatible developer API can be gated by locally verified credits, but hosted globally atomic spending is not implemented.

## 4. Implemented Capabilities

### 4.1 Model And Authorization

Implemented:

- a supported-model registry for OPT, TinyLlama, Llama-family, and Mistral entries;
- model-specific layer counts, hidden sizes, tuning labels, gating, and generation defaults;
- Hugging Face browser/device OAuth;
- public-model isolation from expired private tokens;
- validated local gated-model imports;
- tokenizer, configuration, architecture, weight-index, shard, layer-count, and hidden-size checks;
- token storage outside the repository with restricted permissions;
- explicit removal and cache-cleanup behavior.

Limits:

- not every registry model has live distributed generation evidence;
- large gated models remain constrained by local RAM, VRAM, download authorization, and checkpoint format;
- reward-eligible workers advertise the immutable commit resolved by the actual layer load; the current settlement allowlist contains only the reviewed OPT-125M checkpoint.

### 4.2 Serving And Selective Loading

Implemented:

- half-open layer ranges such as `0-6` followed by `6-12`;
- architecture-aware selective safetensors loading;
- indexed and single-file checkpoint support;
- meta-device construction and slice-only tensor materialization;
- measured loading diagnostics and memory deltas;
- explicit full-model compatibility fallback for unsupported or binary checkpoints;
- start, turn off, turn on, delete, and cleanup lifecycle states;
- periodic DHT metadata and expert publication refresh;
- multiple local ranges and confirmed exact replicas.

Limits:

- full-model fallback can defeat the memory-saving objective;
- stopped replicas retain loaded memory by design;
- shutdown timeouts can continue cleanup after the API has returned;
- physical Windows cleanup still needs orphan-process acceptance evidence.

### 4.3 Coverage And Routing

Implemented:

- validated DHT node metadata;
- dynamic complete-route selection from adjacent ranges;
- exact-replica grouping and deterministic selection;
- missing-range reporting;
- route and standby visibility;
- capacity-aware range recommendations;
- stale coverage revisions and redundancy confirmation;
- health-aware route candidates and bounded complete-route failover;
- accounting-safe refusal to retry ambiguous outcomes.

Limits:

- public DHT metadata is validated structurally but is not admission-controlled by a trusted membership authority;
- malicious identities can still create Sybil advertisements;
- failover restarts a stateless full forward because distributed KV-cache migration is absent;
- current architecture documentation still contains stale text claiming health-aware failover is absent even though Sprint 21 code exists.

### 4.4 Provider Health And Runtime Validation

Implemented:

- independent DHT presence, protocol, transport, and expert-health signals;
- selected and standby probe schedules;
- bounded probe timeouts and concurrency;
- failure and recovery hysteresis;
- route revisions and health revisions;
- generator readiness state with suspended state;
- startup tensor canary;
- exact unavailable ranges and provider reasons;
- DHT announcement freshness and heartbeat diagnostics;
- generator dependency cleanup when selected local nodes disappear.

Limits:

- Hivemind 1.1.12 requires compatibility code that binds private `MPFuture` internals to the active event loop;
- that private-field dependency is sensitive to upstream Hivemind changes;
- physical relay stability beyond the DHT expiry window remains the main external proof gate;
- cached UI state must still be checked against authoritative runtime snapshots during failures.

### 4.5 RPC Safety

Implemented:

- rank, shape, hidden-size, dtype, finite-value, mask, position, metadata, and byte-limit validation;
- active and queued request limits;
- nonblocking Hivemind task admission;
- cooperative execution deadlines between layers;
- process-safe counters shown in Monitoring;
- receipt accounting only after successful complete output;
- bounded route attempts and failure classification.

Limits:

- a PyTorch kernel already executing inside one decoder block cannot be force-cancelled safely;
- public experts do not yet have identity quotas, network bandwidth quotas, or proof-of-stake admission;
- adversarial load testing through the real VPS relay remains open.

### 4.6 Generation And Visibility

Implemented:

- OPT and Llama-family architecture adapters;
- local embeddings and output head;
- base versus chat/instruct prompt handling;
- cumulative decoding that preserves streamed spacing;
- exact generation controls;
- prompt cancellation between token steps;
- next-token and generated-output parity probes;
- generation traces and comparison tools;
- startup, model load, route check, first-token, total-time, throughput, and hop metrics;
- independent Monitoring refreshes that preserve successful partial data.

Limits:

- every generated token recomputes the full sequence through the distributed route;
- there is no distributed KV cache;
- one process supports one generator at a time;
- throughput is prototype-grade, particularly through a relay;
- raw generation trace files can contain prompts and outputs and must remain private local state.

### 4.7 Useful-Work Incentives And API Access

Implemented:

- Ed25519 identities and signed presence;
- BLAKE3 input and output commitments;
- worker receipts and generator acceptance;
- route and layer membership checks;
- replay, self-dealing, stale timestamp, altered counter, and policy rejection;
- SQLite WAL settlement and pagination;
- off, shadow, and credit modes;
- hashed local developer API keys;
- signed short-lived inference capabilities;
- local atomic credit reservation and release;
- free Electron chat separated from the developer endpoint.

Limits:

- credit mode is not approved;
- local spending is not globally atomic across participant devices;
- colluding generator and worker identities can still manufacture apparently valid work;
- Sybil resistance and independent result verification remain unsolved;
- no transfer, withdrawal, conversion, or payout system exists;
- settlement schema upgrades currently require a new database rather than migrations;
- submissions discarded before the 2026-08-19 retry hardening cannot be reconstructed;
- current submissions use an identity-bound, bounded SQLite outbox, but live two-device restart recovery still requires physical evidence.

### 4.8 VPS Operations

Implemented:

- systemd bootstrap/relay service and stable identity;
- systemd settlement service in shadow mode;
- hardened service users and restricted state directories;
- bootstrap status and restart validation;
- relay probe with effective runtime details;
- settlement policy endpoint;
- documented backup, rollback, and tunnel operations.

Current observed deployment:

- settlement is enabled and active;
- it listens only on VPS loopback port 7101;
- policy reports shadow mode and protocol version one;
- the displayed process still uses `/opt/distribllm/backend/.venv`, indicating that the VPS has not yet been reinstalled with the latest service-owned settlement environment path;
- participant access therefore requires a WSL-side SSH tunnel until HTTPS is deployed.

## 5. Validation State

### Proven Locally Or In Isolated Integration

- OPT-125M local two-provider split route;
- direct versus distributed next-token parity;
- real Hivemind metadata and tensor RPC in local/isolated probes;
- useful-work receipt RPC and settlement round trip;
- TinyLlama local generation baseline;
- selective loading memory reduction;
- route planning, health hysteresis, failover policy, RPC limits, lifecycle jobs, API access, settlement validation, and package auditing through focused tests;
- public VPS circuit reservation;
- same-host independent expert metadata RPC through the public relay;
- persistent VPS bootstrap and settlement process installation.

The 2026-08-19 settlement smoke run also verified bounded transient retry,
stable idempotency keys, exact duplicate acceptance without a second ledger
entry, off/shadow/replay API behavior, the full incentives module, the real
Hivemind receipt RPC, and the updated frontend type contract. The settlement
API tests now use an in-loop HTTPX ASGI transport because Starlette's threaded
`TestClient` deadlocked under the current Python/AnyIO environment.

### Not Yet Proven To Final Acceptance Standard

- one real generated response using two separate physical Windows devices through the VPS relay;
- stable provider advertisement and RPC health beyond the DHT expiry interval during that run;
- controlled physical provider failure followed by successful alternate-route completion;
- direct-LAN two-device inference from the same accepted package;
- two-device shadow receipt acceptance with selected-worker payment and standby non-payment;
- settlement restart continuity bound to the same acceptance run;
- clean Windows installation from an NSIS installer;
- automatic packaged backend provisioning without a manual source checkout;
- distributed TinyLlama across separate peers;
- a larger model generated through a real multi-peer route;
- credit mode, public API billing, or payout behavior.

## 6. Severity-Ranked Findings

### High 1 - Electron Security Is Deliberately Disabled

Resolved in source on 2026-08-23. The desktop window now enables Chromium
sandboxing, context isolation, and web security while disabling renderer Node
integration. The packaged renderer loads from the privileged, path-confined
`distribllm://app` scheme instead of an opaque file origin. Global request and
response header rewriting is removed; CSP no longer permits `unsafe-eval` or
inline scripts; object, base, and frame embedding are disabled. The preload
exports only the application-specific API, every IPC handler validates its
sender URL, navigation is confined to the renderer, and external URLs are
restricted to reviewed Hugging Face HTTPS hosts.

### High 2 - FastAPI Management Surface Uses Wildcard CORS

Resolved in source on 2026-08-23 for the local desktop boundary. FastAPI now
uses an exact configurable allowlist containing the packaged
`distribllm://app` origin and reviewed loopback development origins, explicit
methods and headers, an HTTP middleware that rejects every foreign browser
origin before routing, and the same check before WebSocket acceptance. The
managed backend entry point rejects non-loopback binds; network exposure remains
unsupported until a separately authenticated TLS gateway is implemented.

### High 3 - Final Installer Does Not Include The Backend

The NSIS target and desktop shortcut exist, but normal users must already have WSL 2, Ubuntu, `uv`, Python, a backend checkout, and an absolute backend path.

Required action:

- bundle a sanitized backend payload;
- extract it into a versioned WSL runtime directory;
- provision a locked Python environment;
- preserve mutable state separately;
- automate upgrade and rollback;
- remove backend path entry from the normal first-run flow;
- retain a developer override.

### High 4 - Fundamental Two-Device Generation Is Still Unaccepted

Circuit reservation and metadata RPC do not prove tensor forwarding or autoregressive generation. The project cannot claim its central distributed inference goal is complete until two separate physical participants generate through a complete route.

Required action:

- run both devices from the same clean source/backend and executable hash;
- serve adjacent ranges;
- wait beyond metadata expiry;
- generate text;
- capture route, transport, timing, RPC, monitoring, and cleanup evidence.

### High 5 - Settlement Connectivity Is Not Production-Ready

The VPS listener is intentionally loopback-only. Participants currently need a manually maintained SSH tunnel. Without the tunnel, `http://127.0.0.1:7101` points to the participant WSL namespace and returns connection refused.

Required action:

- keep the tunnel for controlled shadow testing;
- deploy an authenticated HTTPS reverse proxy and project domain for final multi-user operation;
- add reverse-proxy body limits, request timeouts, and rate limits;
- never expose plain settlement HTTP publicly.

### High 6 - Source And Package Reproducibility Is Not Clean

Resolved in source and packaging procedure on 2026-08-23. Bun and its tracked
lockfile are the declared dependency authority, generated archives remain
outside the packaged allowlist, and acceptance builds require a committed
tracked-clean tree plus an explicit full source commit. The packaged launcher
also checks the external WSL checkout against that embedded commit before sync
or startup. Every release candidate is rebuilt only after its source commit and
must pass the ASAR/package audit before distribution. User-owned untracked
archives in a development checkout are not release inputs.

### High 7 - Receipt Policy Uses Mutable Model Revisions

Resolved in source on 2026-08-23. Receipt RPC startup now reads the checkpoint
commit from the loaded layer diagnostics and refuses incentives when that value
is missing, mutable, or different from the optional operator assertion. Worker
metadata and signed requests therefore use the exact loaded commit. Settlement
reward policy version two replaces `main` with an explicit immutable allowlist,
currently limited to the reviewed OPT-125M checkpoint. Existing databases keep
historical version-one rows while automatically activating version two.

### Medium 1 - Settlement Outbox Durability

Resolved in source on 2026-08-23. The participant now commits canonical signed
submissions to a private, application-identity-bound SQLite WAL outbox before
network delivery. Pending and retrying entries recover after restart with their
stable idempotency key and attempt count. Entries that cannot be retried safely
inside the receipt timestamp window become permanently rejected with structured
reasons. Active rows are capacity-limited and terminal history is pruned to a
configured bound. The remaining gate is physical settlement-outage and restart
evidence on the two participant devices.

### Medium 2 - Hivemind Compatibility Depends On Private Internals

The Python 3.12 repair assigns private `MPFuture` fields. This is necessary for the pinned Hivemind version but fragile across upgrades.

Required action:

- pin Hivemind exactly;
- isolate the compatibility shim;
- add a version guard and fail closed for unknown versions;
- replace the shim when upstream exposes a supported event-loop binding API.

### Medium 3 - No Settlement Schema Migration System

The service refuses incompatible schemas and asks operators to select a new database. That protects data but is not an upgrade path.

Required action:

- add explicit ordered migrations;
- back up before migration;
- validate row counts and ledger totals after migration;
- provide rollback documentation.

### Medium 4 - Local Credit Spending Is Not Globally Atomic

Two machines using the same logical account can each make local reservations without a global authority.

Required action:

- keep API access mode off for the final core-inference build;
- later move reservation and spend decisions to an authenticated hosted gateway.

### Medium 5 - No Strong Public-Swarm Abuse Resistance

Signatures prove key ownership, not honest computation or unique human/device identity. DHT and settlement endpoints can be flooded by many identities.

Required action:

- rate-limit relay, DHT, RPC, and settlement traffic;
- define identity admission and revocation;
- add reputation and anomaly review;
- do not describe current credits as collusion-resistant proof of work.

### Medium 6 - Prototype Generation Is Computationally Inefficient

The full token sequence is forwarded for each generated token and there is no distributed KV cache. Relay latency multiplies this cost.

Required action:

- keep OPT-125M as the reliability acceptance model;
- add session-aware KV cache ownership only after correctness acceptance;
- later add batching, scheduling, and multiple generator sessions.

### Medium 7 - Installer And Runtime Updates Are Incomplete

The project has no automatic managed-backend extraction, dependency rollback, state migration, code signing, or update channel.

Required action:

- implement versioned WSL runtime installation first;
- produce the NSIS setup and uninstall behavior;
- add signing and updates after the examiner-ready build is stable.

### Medium 8 - Documentation And Sprint State Have Drifted

All sprints from 13 through 26 remain active because they have not been explicitly closed. Several early session entries say implementation is only proposed, while later entries record completion. Some architecture text still describes features that now exist as absent.

Required action:

- use this report and physical evidence to review each sprint;
- close only with explicit user approval;
- update architecture statements after integration is accepted;
- avoid counting an active sprint as wholly unimplemented.

### Low 1 - Incentives Refresh Couples Independent Endpoints

The Incentives page uses one `Promise.all` for accounting and developer-access status. A failure in either request prevents the other successful response from being applied.

Required action:

- use the existing independent refresh primitive;
- preserve the last successful subsection and show per-section errors.

### Low 2 - Some API Models Use Mutable List Defaults

Pydantic currently copies non-hashable defaults, but `initial_peers: list[str] = []` is less explicit and easier to misuse outside Pydantic.

Required action:

- use `Field(default_factory=list)` consistently.

### Low 3 - UI Test Coverage Is Narrow

Frontend tests concentrate on launcher and independent refresh logic. Most page workflows depend on manual screenshots and physical acceptance.

Required action:

- add component tests for runtime state transitions, route loss, incentives retrying, and setup provisioning;
- add packaged first-run automation on a disposable Windows runner when CI is available.

## 7. Settlement Connection-Refused Incident

### Symptom

```text
Settlement: ERROR
Settlement is unreachable: <urlopen error [Errno 111] Connection refused>
0 accepted, 58 rejected
```

### Root Cause

The configured URL is `http://127.0.0.1:7101`. From the participant backend, that address means participant WSL, not the VPS. The VPS settlement service listens on VPS loopback by design. A tunnel must therefore be running inside the same WSL distro as the backend:

```text
participant WSL 127.0.0.1:7101
  -> SSH tunnel
  -> VPS 127.0.0.1:7101
```

The failed attempts in the screenshot occurred while no WSL-side listener/tunnel was available. The previous runtime removed every failed submission from its in-memory queue, incremented `rejected_submissions`, and never retried it. That is why pending was zero and rejected reached 58.

### Code Repair Added On 2026-08-19

- connection failures, timeouts, HTTP 408, HTTP 425, HTTP 429, and server HTTP 5xx responses use bounded exponential retries;
- retry defaults are eight attempts with one-to-thirty-second backoff;
- the queue is bounded to prevent unbounded memory growth;
- retry status is distinct from permanent rejection;
- loopback failures instruct the operator to verify the WSL SSH tunnel;
- every receipt POST carries a BLAKE3 idempotency key;
- the settlement service returns an existing result only for an exact previously committed signed submission with matching request ID, receipt hash, nonce, and canonical payload;
- altered collisions and ordinary replays remain rejected;
- one exact retry cannot create a second ledger entry.

### Remaining Limitation

The already lost 58 submissions cannot be reconstructed because the older
process discarded their signed payloads before the durable outbox existed.
New pending submissions survive backend restart, but that recovery still needs
live two-device outage evidence before settlement rollout is approved.

### Operator Recovery

1. Verify the VPS service and policy locally on the VPS.
2. Verify the VPS SSH host fingerprint from a trusted VPS session.
3. Start the SSH tunnel inside participant WSL, not PowerShell and not the VPS.
4. Keep the tunnel terminal open.
5. From a second participant WSL terminal, request `/v1/policy` through local port 7101.
6. Restart the managed backend so it loads the new environment and clears old process-local counters.
7. Run new distributed inference and observe pending, retry, accepted, and settlement account counters.

Both the participant backend and VPS settlement source must be updated to the commit containing this repair. The participant needs the retrying client; the VPS needs exact idempotent duplicate handling.

## 8. What Must Be Developed Before The Final Desktop App

### Release-Critical Core

1. Integrate and commit the runtime, health, replica, settlement, and documentation changes into one reviewed clean branch.
2. Resolve the frontend package-manager lock contract.
3. Restore Electron sandbox, web security, scoped CSP, and scoped CORS.
4. Restrict FastAPI management origins and preserve loopback-only operation.
5. Build a sanitized versioned backend archive.
6. Add first-run extraction into WSL.
7. Provision `uv`, Python 3.12, and locked dependencies automatically.
8. Separate immutable runtime files from models, identities, tokens, traces, receipts, and API keys.
9. Add runtime upgrade, rollback, and uninstall retention behavior.
10. Build the NSIS installer and verify its desktop shortcut.
11. Run clean-Windows first-install and restart tests.
12. Run the complete two-device relay and direct acceptance matrix.

### Incentive Demonstration Requirements

1. Keep settlement in shadow mode.
2. Use a WSL SSH tunnel for immediate controlled testing or HTTPS for the polished final demo.
3. Deploy retry/idempotency code to both clients and VPS.
4. Prove accepted receipts increase only for selected workers.
5. Prove standby providers receive no receipt credit.
6. Restart settlement and verify account continuity.
7. Keep developer API access off until approval.

### Production Work That Can Follow The FYP Demonstration

- durable local receipt outbox;
- HTTPS domain, authentication, and reverse-proxy rate limits;
- schema migrations and automated backups;
- hosted globally atomic API credit spending;
- stronger anti-Sybil and anti-collusion controls;
- code signing and automatic updates;
- offline managed WSL image;
- distributed KV cache, batching, and multi-session scheduling;
- larger-model multi-peer performance validation;
- transferable or redeemable incentives, if separately approved.

## 9. Recommended Finalization Sequence

### Gate A - Restore A Reproducible Source Baseline

- review every current modification;
- remove generated archives from the candidate release state;
- choose the package manager and lockfile;
- merge the required feature branches;
- commit a clean integration source identity.

### Gate B - Complete Core Physical Acceptance

- update both participant backends to the same commit;
- run the persistent VPS relay from its reviewed commit;
- serve OPT-125M as `0-6` and `6-12` on two devices;
- hold the route beyond 90 seconds;
- start the generator and produce text;
- capture Monitoring, RPC counters, route identity, relay mode, and cleanup;
- inject one controlled provider failure when an alternate route is available.

### Gate C - Complete Shadow Incentive Acceptance

- deploy the settlement retry/idempotency version;
- connect both WSL participants through tunnel or HTTPS;
- generate useful work;
- confirm exact selected-worker receipt deltas;
- validate persistence across settlement restart;
- retain shadow mode.

### Gate D - Build The Managed Backend Installer

- package backend source and lockfile as an Electron resource;
- provision a versioned WSL runtime automatically;
- preserve mutable state separately;
- remove the normal backend-path requirement;
- build NSIS setup with desktop shortcut;
- test install, launch, restart, upgrade, and uninstall on clean Windows.

### Gate E - Security And Release Review

- restore Electron security boundaries;
- narrow backend origins;
- verify no secrets or mutable runtime state enter the installer;
- run dependency and package audits;
- rebuild from the clean source commit;
- record installer hash and acceptance reports from both devices.

## 10. Final Assessment

DistribLLM is no longer a minimal mock-up. It has real distributed model execution, route planning, health supervision, bounded failover, selective loading, RPC safety, useful-work cryptography, settlement, monitoring, and a Windows launcher. Its strongest evidence is local and isolated integration evidence.

The project is still correctly classified as an MVP prototype because its central multi-device relay generation gate remains open, the incentive plane is only in shadow deployment, Electron security is development-oriented, and the desktop installer does not yet provision its backend.

Nothing prevents beginning final desktop packaging now. The recommended first final package is an online managed-WSL installer: Electron and a sanitized backend payload ship together, while Python dependencies and model files are provisioned into WSL and mutable user state remains outside the executable. A fully offline WSL image, native Windows backend, global credit economy, and distributed KV cache should not block that release.
