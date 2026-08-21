# Current Two-Device Live-Test Issues And Production Roadmap

**Report updated:** 2026-08-22
**Latest physical evidence:** 2026-08-21
**Branch:** `fix/remote-dht-lease-recovery`
**Reviewed source baseline:** `c00bfc26f2591b012a190b97d6d647b4070f9a51`
**Topology:** Windows Electron clients, WSL 2 participant backends, a public VPS bootstrap/circuit relay, and a loopback-only VPS settlement service reached through per-device SSH tunnels
**Incentive rollout:** `shadow`

## 1. Plain-Language Summary

The latest test proved that the two devices can initially find each other and exchange a real tensor through the VPS relay. Device 1 created a generator, discovered Device 2's complete `facebook/opt-125m` worker, validated the `0-12` route, and completed a tensor canary. The system was genuinely ready at that point.

Approximately five minutes later, Device 1 could no longer find Device 2's Hivemind expert UID. Device 1 then stopped seeing Device 2's provider advertisement altogether and correctly suspended inference.

At the same time, Device 2 was still running and reported its RPC server, expert publisher, provider announcement, and lease refreshes as fresh and successful. The current confirmed defect is therefore:

> Device 2 reports successful local publication refreshes, but those refreshed records stop being observable from Device 1 after several minutes.

The health validation did not cause this failure. It detected the vanished route and prevented a prompt from being sent into an unusable provider.

Source inspection identified a concrete false-success path: Hivemind client-mode workers can include their own local DHT storage or cache in a successful store, even though a generator cannot retrieve that local copy. The repair now requires remote-peer acknowledgement for worker records. This is the strongest code-level explanation for the evidence, but it remains a hypothesis until the rebuilt two-device system passes the long soak test.

The first repair set was implemented on `2026-08-22` and is awaiting physical validation. It includes remote-only lease writes, per-peer membership leases, worker transport recovery, generator-start discovery, and coherent Monitoring labels. Passing unit tests does not change the latest physical acceptance result from failed to passed.

## 2. Intended Runtime Flow

```text
Device 2 worker
  -> loads facebook/opt-125m layers 0-12
  -> joins the DistribLLM DHT through the VPS relay
  -> publishes provider metadata and expert RPC records
  -> refreshes those time-limited records before they expire

Device 1 generator
  -> joins the same DHT through the VPS relay
  -> discovers Device 2
  -> probes expert metadata RPC
  -> validates a complete 0-12 route
  -> runs a tensor canary
  -> loads local embeddings and LM head
  -> sends generation work to Device 2

Useful-work path
  -> Device 2 signs a receipt for successful selected work
  -> Device 1 verifies and countersigns it
  -> the participant submits it to the settlement service
  -> shadow mode validates and records evidence without adding credits
```

The intended roles are:

- Device 1: generator only;
- Device 2: complete `0-12` worker;
- VPS port `7001`: public bootstrap and circuit relay;
- VPS settlement service: currently loopback-only on port `7101`;
- participant SSH tunnels: temporary test-only access to settlement.

## 3. Evidence From The Latest Physical Run

### 3.1 Initial discovery was shared across both devices

Independent DHT clients on Device 1 and Device 2 both retrieved the same current worker:

- peer ID `12D3KooWEUttfKx6yjZBhXWMroxpV7VockfStmdbYvmjmeq3J7ky`;
- node ID `e4689d5f4381`;
- model `facebook/opt-125m`;
- layers `0-12`;
- expert UID `distribllm.0.12`;
- receipt UID `distribllm.999999.0.12`;
- relay circuit address through the expected VPS;
- application key `vmyaPTLxoyoBOr1qPjNtjQJOqsghRK6O-78xWKHWCjs`.

The shared members list also contained an older peer whose node metadata was `null`. That stale member did not prevent the current route from being selected, but it demonstrates that membership cleanup is incomplete.

### 3.2 Generator startup passed

At `2026-08-21 18:07:53 UTC`, Device 1 transitioned to `ready`.

The generator job reported:

- status `ready`;
- route ready `true`;
- startup duration approximately `11.47 seconds`;
- cold component load approximately `2.86 seconds`;
- tensor canary `ok`;
- canary duration approximately `2.59 seconds`;
- canary tensor shape `[1, 1, 768]`;
- one selected Device 2 route covering `0-12`.

Device 2 later reported one accepted and completed RPC request, one served request, and one token position. That request was the startup tensor canary, not a user prompt.

### 3.3 The route failed after approximately five minutes

At `2026-08-21 18:13:11 UTC`, Device 1 transitioned from `ready` to `suspended`.

The first recorded reason was:

```text
Provider probe expert_lookup failed for 12D3KooW (distribllm.0.12):
RuntimeError: Expert distribllm.0.12 was not found
```

Device 1 then reported:

```text
No healthy complete route; unavailable layers 0-12.
Provider 12D3KooW is offline: DHT advertisement expired or disappeared.
```

The final Device 1 snapshot showed:

- generator components still loaded;
- generator state `suspended`;
- route ready `false`;
- provider `dht_present: false`;
- nine consecutive failures;
- health monitor still running;
- no active probes stuck;
- no discovery exception;
- the original successful canary retained;
- no completed user generation.

### 3.4 Device 2 remained locally healthy

After Device 1 had lost the route, Device 2 still reported:

- worker `running: true`;
- RPC `rpc_running: true`;
- relay transport verified;
- RPC server alive;
- RPC runtime ready;
- Hivemind publisher thread alive;
- both expert UIDs present in local publication status;
- RPC publication fresh with no error;
- node announcement thread alive and fresh with no error;
- zero failed, rejected, or timed-out RPC requests.

This correlated evidence rules out a normal Device 2 shutdown. It establishes a divergence between Device 2's local publication result and Device 1's remote DHT view.

## 4. What The Current Screens Mean

### Device 1 Nodes: `0 online`

This is expected. The Nodes page is for local workers on that machine. Device 1 is intentionally generator-only, so it should have no local serving node.

### Device 1 generator: `suspended`

The generator model components remain loaded, but there is no complete RPC-healthy route. The runtime can recover if a valid provider reappears, but inference must remain disabled until then.

### Device 1 Inference: WebSocket closed and route waiting

This is a consequence of the suspended route, not a separate WebSocket defect. The backend refuses inference while the selected transformer route is unavailable.

### Device 2: local publication fresh

This currently means only that Device 2's local DHT operations returned success recently. It does not prove that another peer can retrieve the refreshed records. Production readiness needs a separate remotely observable signal.

### Device 2 Monitoring: `RPC HEALTH UNKNOWN`

Device 2 has no local generator, so it has no generator-side provider health monitor. This should eventually be labeled `NOT PROBED BY A GENERATOR` rather than `UNKNOWN`.

## 5. Confirmed Issue Register

| ID | Severity | Issue | Effect | Status |
|---|---|---|---|---|
| LT-01 | Critical | Generator-only discovery/start circular dependency | The UI disabled generator start before the generator DHT existed | Fix implemented; physical validation pending |
| LT-02 | Critical | Remote DHT and expert lease persistence failure | A valid route disappeared after approximately five minutes while the worker reported fresh local publication | Repair implemented; soak validation pending |
| LT-03 | High | Local publication success was treated as remote availability | Device 2 could present a healthy worker while Device 1 could not retrieve it | Remote-store acknowledgement implemented; independent observer still open |
| LT-04 | High | Shared members index retained expired peers | Discovery saw member IDs whose per-peer metadata was already gone | Per-peer v2 leases implemented with legacy fallback |
| LT-05 | High | Monitoring combined incompatible snapshots | It could show `100%` raw coverage and `Missing: 0-12` simultaneously | UI calculation fixed; physical validation pending |
| LT-06 | Medium | Worker-only health said `RPC HEALTH UNKNOWN` | Operators could mistake “not probed” for RPC failure | Relabeled as lease state plus `NOT PROBED` |
| LT-07 | High | A complete user generation is not accepted yet | The tensor canary passed, but the route vanished before the prompt | Open acceptance gate |
| LT-08 | High | Latest-run shadow receipt acceptance is not proven | No successful prompt means no final selected-work receipt | Open acceptance gate |
| LT-09 | High | Settlement access uses manual participant SSH tunnels | Closing the tunnel makes local settlement unavailable | Test-only deployment limitation |
| LT-10 | High | Portable EXE does not provision its backend | Every device still needs manual WSL, checkout, dependencies, configuration, and model setup | Packaging limitation |
| LT-11 | Medium | Blank API database configuration can cause HTTP 500 | An empty path can resolve to a directory rather than a SQLite file | Workaround known; code/template fix open |
| LT-12 | Medium | Runtime failure details are split across local and remote views | One machine alone cannot distinguish local success from global visibility | Instrumentation improvement required |

## 6. Confirmed Failure Boundary And Remaining Root-Cause Questions

### Confirmed boundary

The failure is after a successful worker start and before sustained remote observability:

```text
Device 2 RPC and heartbeat threads continue running
  -> local DHT/expert refresh calls return success
  -> Device 1 initially retrieves and calls the worker
  -> Device 1 later cannot resolve the expert UID
  -> Device 1 later cannot retrieve the provider metadata
  -> route is suspended
```

### Why local `fresh` is insufficient

The worker currently marks publication fresh when its local refresh operation finishes without an exception. That confirms neither of these externally important facts:

1. the refreshed record is stored on reachable remote DHT peers; or
2. another independent participant can resolve the exact expert UID and provider record.

In the source used by the failed physical run, [`backend/node/rpc_server.py`](../backend/node/rpc_server.py) treated expert declarations that were not stored as a successful “newer record already exists” condition. The repair no longer accepts that result. Exact routing and receipt UIDs are written with `exclude_self=True`, and every remote write must be acknowledged.

In the failed-run source, [`backend/node/node.py`](../backend/node/node.py) refreshed provider metadata and the members list through the worker's own DHT instance without excluding local storage. The repair requires remote acknowledgement whenever bootstrap peers are configured. Isolated no-bootstrap local development continues to allow local storage.

### Mechanisms that must be tested during the fix

The evidence does not yet select one of these as the sole root cause:

- expert declarations are rejected because another record has a later expiration, but the retained record is stale or unreachable;
- the worker remains connected to a local/partial DHT view after its useful path to the bootstrap network changes;
- provider records are accepted locally but are not replicated to peers Device 1 queries;
- the shared read-modify-write members list loses or retains entries during concurrent refreshes;
- the relay reservation or DHT routing relationship changes while the local process remains alive;
- the health stale threshold is too aggressive for real relayed DHT visibility fluctuations.

Increasing timeouts alone is not an adequate fix because the expert UID became genuinely unresolvable before the provider metadata disappeared.

## 7. Engineering Solution And Implementation State

### 7.1 Make remote observability authoritative

Add an independent publication observer that does not use the worker's own DHT cache. During acceptance this can run on the VPS or through a separate client identity. For every active worker it should verify:

- the member record is present;
- the per-peer provider record is present and current;
- the advertised peer and model/range match the worker;
- both the normal and receipt expert UIDs resolve;
- the expiration horizon is safely beyond the next refresh interval;
- the relay circuit address is still dialable.

Worker status should distinguish:

```text
LOCAL_RPC_RUNNING
LOCAL_PUBLICATION_ATTEMPT_SUCCEEDED
REMOTE_PROVIDER_VISIBLE
REMOTE_EXPERT_VISIBLE
```

Only the last two should support a production `ADVERTISED` state.

**State:** implemented for acceptance and partially implemented for production. Worker publication success now requires remote DHT acknowledgement, eliminating local-cache-only success. `backend/lease_observer.py` creates a separate DHT identity and continuously verifies membership, metadata, both expert UIDs, ownership, and expiration horizon. Deploying equivalent always-on observation as a managed production service remains open.

### 7.2 Verify expert declarations instead of assuming success

When expert declaration says a record was not stored because a newer record exists:

1. retrieve the existing record;
2. verify that it belongs to the current peer and current expert endpoint;
3. verify that its expiry safely covers the next heartbeat window;
4. otherwise mark publication unhealthy and recreate the expert registration or DHT session.

Publication state must include the observed peer, record expiration, read-back time, and verification source.

**State:** implemented for the immediate false-success path. The supervised writer publishes each exact expert UID to remote peers and rejects unacknowledged writes. Independent read-back details remain part of the production observer work.

### 7.3 Replace the shared mutable members list

The current whole-list read-modify-write key is vulnerable to stale members and concurrent lost updates. Replace it with a DHT-native per-peer membership representation, such as independently expiring subkeys or model/range-specific provider keys.

Each worker must refresh only its own membership lease. Discovery should ignore expired subkeys without requiring another worker to rewrite a global list. Offline shutdown should revoke or shortly expire only the worker's own entry.

**State:** implemented as `{prefix}.members.v2` with one expiring DHT subkey per peer. Discovery reads v2 first and also accepts the legacy aggregate list during rolling upgrades. The legacy list is now best-effort and no longer authoritative.

### 7.4 Recover the worker network session

Add supervised recovery when remote visibility fails repeatedly:

1. mark the worker degraded without unloading model layers;
2. verify bootstrap connectivity and relay reservation;
3. recreate the DHT/relay session if required;
4. republish provider and expert records;
5. require two independent successful observations before returning online.

This preserves expensive model loading while repairing networking.

**State:** implemented. After two consecutive remote lease failures, subject to a cooldown, the worker recreates DHT and RPC transport handles while retaining loaded model layers. Recovery success and errors are exposed in announcement status.

### 7.5 Align lease and health policy

The policy should tolerate brief eventual-consistency gaps while still failing safely:

- refresh substantially before expiration;
- require multiple missed remote observations before offline;
- keep the stale threshold at least several refresh intervals;
- never extend readiness based only on an old local success timestamp;
- keep expert and provider lease policies aligned;
- record a monotonic publication generation so stale records cannot appear current.

Larger thresholds are a resilience measure, not a substitute for remote verification.

### 7.6 Remove the generator discovery deadlock

Create or lazily retain a lightweight discovery DHT independently of a local worker or loaded generator. The frontend should allow `START GENERATOR` when the backend and model are available, then show `DISCOVERING AND VALIDATING ROUTE` while the backend performs authoritative route, health, and canary checks.

The serving-plan preview should guide the user, not prevent the operation that creates discovery.

**State:** implemented. `START GENERATOR` is available when a model is selected even if the preflight plan is local-only. Generator startup creates the DHT client and still enforces the authoritative route, RPC-health, and tensor-canary gates.

### 7.7 Make the UI report one coherent snapshot

Monitoring should separately show:

- raw DHT coverage;
- remotely observed publication state;
- RPC-probed coverage;
- selected generator route;
- snapshot age and source.

The layer percentage and missing-range label must derive from the same revision. Worker-only pages should say `NOT PROBED` when no local generator exists.

**State:** implemented for the confirmed contradictions. Coverage percentage and missing ranges now use the same live provider set, offline providers do not count toward coverage, and worker-only RPC state distinguishes a fresh lease from an active generator probe.

### 7.8 Add regression and soak coverage

Before another physical release:

- run a lease test for at least ten expiration windows;
- continuously query provider metadata and both expert UIDs from an independent peer;
- execute periodic tensor canaries;
- interrupt and restore relay connectivity;
- restart the bootstrap service;
- verify worker network recovery without model unload;
- verify generator suspension and automatic recovery;
- exercise two simultaneous workers updating membership;
- prove expired peers disappear without removing live peers.

## 8. Why Production Must Not Use Manual SSH Tunnels

Correct: ordinary production participants should not create SSH keys, receive VPS shell accounts, or manually maintain tunnels.

The current SSH tunnel is a controlled-development safety measure. It keeps the unfinished settlement service private while allowing two known participants to test it.

The production flow should be:

```text
Participant Electron
  -> local managed WSL backend
  -> HTTPS on port 443
  -> authenticated reverse proxy or API gateway
  -> loopback/private settlement service

Participant P2P runtime
  -> public bootstrap/circuit relay on port 7001
  -> direct peer traffic when available
  -> relay traffic when direct reachability fails

Administrator only
  -> restricted SSH access for VPS operations
```

### Production settlement endpoint

Deploy a project domain such as `settlement.example.org` with:

- TLS certificates and automatic renewal;
- an HTTPS reverse proxy such as Caddy or Nginx;
- the settlement application still bound to loopback or a private service network;
- request-size limits, rate limits, timeouts, and structured access logs;
- application-level signature verification for every receipt;
- optional mutual TLS or service credentials for privileged operations;
- firewall rules exposing only required public ports;
- database migrations, backups, retention, and restore tests;
- health checks and alerting.

Participants would receive a signed/default configuration containing:

```text
DISTRIBLLM_SETTLEMENT_URL=https://settlement.example.org
```

No participant SSH account or tunnel would be required. SSH would remain an administrator-only maintenance channel.

### Production bootstrap and relay

One VPS is a single point of failure. Production needs:

- at least two independently hosted bootstrap/relay nodes;
- multiple signed bootstrap multiaddresses distributed with the application;
- automated service deployment and restart validation;
- bandwidth, connection, reservation, and latency monitoring;
- abuse controls and capacity limits;
- a documented relay rotation and key-replacement procedure.

## 9. Remaining Work To Finish The System

### Phase 0 — close the current correctness blockers

1. **Done in code:** require remote acknowledgement for expert, provider, and membership leases.
2. **Done in code:** replace the authoritative mutable members list with per-peer v2 leases.
3. **Done in code:** recreate worker DHT and RPC handles after repeated lease failures without unloading layers.
4. **Done in code:** remove the generator-only disabled-start circular dependency.
5. **Done in code:** reconcile the confirmed Monitoring coverage and probe-label contradictions.
6. **Done in code:** add a standalone independent remote read-back observer and eleven-window simulated lease regression.
7. **Open:** run the observer for at least ten real lease windows through the VPS relay.
8. **Open:** give the developer API database a safe concrete default.
9. **Done in documentation:** preserve the latest physical evidence as a failed stability acceptance result.

### Phase 1 — prove sustained distributed inference

1. Pass automated multi-peer lease and recovery tests.
2. Build one clean Windows artifact from one committed source revision.
3. Run Device 1 as generator-only and Device 2 as worker-only.
4. Keep the route healthy for a soak period longer than ten DHT expiry windows.
5. Complete multiple prompts rather than only a startup canary.
6. Confirm remote RPC counters and generated output.
7. Interrupt and restore a provider and prove suspension and recovery.
8. Repeat through explicit relay mode and realistic auto/direct selection.

### Phase 2 — finish shadow incentives

1. Prove every selected successful worker receives exactly one valid receipt.
2. Prove standby, failed, rejected, duplicate, and self-dealing work receives no reward.
3. Prove retries are idempotent across temporary settlement outages.
4. Keep credits unchanged while reviewing shadow evidence.
5. Add settlement dashboards, reconciliation, backup, and audit procedures.
6. Enable credit mode only after the physical evidence and abuse review pass.

### Phase 3 — deliver a self-contained desktop installation

The current EXE contains Electron but not a provisioned Python backend. The final installer should:

1. detect or enable WSL 2 with explicit user consent;
2. install a versioned managed DistribLLM WSL runtime;
3. include or securely download the matching backend release;
4. install locked dependencies automatically;
5. create state, cache, identity, and log directories outside the immutable runtime;
6. start and stop only its owned backend service;
7. preserve models and identities across upgrades;
8. verify backend and executable version compatibility;
9. provide repair, uninstall, and rollback flows;
10. retain an advanced source-checkout mode only for developers.

Users should not need Git, `uv`, manual `.env` editing, shell commands, or a pre-existing repository checkout.

### Phase 4 — replace test infrastructure with production services

1. Deploy HTTPS settlement and remove participant SSH tunnels.
2. Deploy redundant bootstrap/relay nodes.
3. Publish signed endpoint and protocol configuration.
4. Add centralized metrics, logs, alerts, backups, and incident procedures.
5. Harden firewall, SSH administration, secrets, TLS, rate limits, and dependency updates.
6. Load-test relay bandwidth, settlement concurrency, and DHT churn.

### Phase 5 — release validation

1. Test installation on clean Windows machines with no development checkout.
2. Test first run, restart, sleep/resume, network change, upgrade, rollback, and uninstall.
3. Validate supported GPU and CPU fallback environments.
4. Complete direct, relayed, split-route, full-provider, failover, and incentive matrices.
5. Audit the release artifact for source identity, forbidden secrets, and dependency integrity.
6. Publish operator, participant, privacy, security, and recovery documentation.

## 10. Acceptance Gates And Current Result

| Gate | Latest result |
|---|---|
| Device 2 loads full `0-12` worker | Passed |
| Both devices initially retrieve the same provider metadata | Passed |
| Device 1 initially resolves and probes Device 2 RPC | Passed |
| Relay tensor canary | Passed |
| Device 1 generator reaches ready | Passed |
| Provider remains remotely visible across sustained lease refreshes | **Failed** |
| Generator remains ready for the soak interval | **Failed** |
| User prompt completes through Device 2 | Not reached |
| Device 2 useful-work receipt is accepted | Not reached |
| Shadow mode leaves credits unchanged | Expected, but latest prompt path not reached |
| Provider failure suspends unsafe inference | Passed |
| Provider automatically recovers without model reload | Not proven |
| Clean-machine packaged installation | Not implemented |
| Participant operation without SSH tunnel | Not implemented |

## 11. Current Stopping Point

The latest physical run should be retained as a failed stability acceptance result. Repeating the same generator startup is not the next useful action: startup already passed and the failure appeared only after the route had been healthy for several minutes.

The immediate implementation and focused unit regression work is complete. The next useful action is to commit one clean revision, build matching backend and Windows artifacts, and repeat the physical test. Keep Device 2's full worker and Device 1's generator alive for at least ten lease windows while `backend/lease_observer.py` repeatedly checks the member lease, provider record, and both expert UIDs from its own DHT identity. Only that result can close LT-02.

## 12. Related Documentation

- [Deployment and live testing](DEPLOYMENT_AND_LIVE_TESTING.md)
- [System code analysis and finalization report](SYSTEM_CODE_ANALYSIS_AND_FINALIZATION_REPORT.md)
- [Current architecture](CURRENT_ARCHITECTURE.md)
- [Useful-work incentives](USEFUL_WORK_INCENTIVES.md)
- [Two-device acceptance evidence](TWO_DEVICE_ACCEPTANCE_EVIDENCE.md)
- [Windows managed WSL packaging](WINDOWS_MANAGED_WSL_PACKAGING.md)
- [Errors and debugging](ERRORS_AND_DEBUGGING.md)
