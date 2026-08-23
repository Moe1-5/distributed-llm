# Deployment and Live Testing Runbook

**Status:** Active operator runbook
**Last updated:** 2026-08-22

This document is the practical checklist for rebuilding the backend-bundled Windows executable, running the VPS bootstrap relay, and validating the managed WSL runtime.

The single combined VPS procedure is the established baseline. For the Sprint
32 separated full-DHT and relay topology, isolated coordinator/settlement
permissions, and failure injection, use
[Infrastructure Redundancy And Architecture Acceptance](INFRASTRUCTURE_REDUNDANCY_ACCEPTANCE.md).

## Should I Create A New EXE?

Create a new executable when you want to test any latest Electron, renderer, settings, managed WSL launcher, packaging, or UI validation change. That includes changes under:

- `frontend/`
- `frontend/electron-builder.yml`
- package metadata embedded into the executable
- renderer behavior for Nodes, Network, Inference, Monitoring, Settings, or Incentives

Do not rebuild the executable just because the VPS bootstrap service changed. The VPS bootstrap is external infrastructure.

Backend-only Python changes now require a rebuilt executable because the package contains the sanitized backend payload. The build stamps that payload with the exact Git commit and a SHA-256 manifest. Before startup, Electron verifies the manifest and installs it atomically under the matching commit inside WSL. This prevents a new renderer from silently testing an old backend checkout.

For final two-device acceptance, build one fresh executable after all accepted fixes are committed. Both devices should run that same artifact hash.

## Current Packaging Boundary

The current Windows executable packages:

- Electron main process
- React renderer UI
- managed WSL backend launcher
- package audit and executable identity support
- sanitized tracked backend application source with an embedded checksum manifest

The current Windows executable does not package:

- the backend virtual environment
- model caches
- Hugging Face tokens
- p2p identities
- traces
- useful-work receipts

During normal testing, leave the developer backend override off. The package installs the verified source beneath the WSL XDG state directory and synchronizes its frozen environment there. A manual checkout path is only an advanced developer override and cannot produce a passing final acceptance report.

The VPS is used only as the DHT bootstrap and circuit relay. It should not run participant model-serving nodes unless you intentionally set up a separate worker there.

## Rebuild The Windows Executable

Run this from the local project checkout on the Windows build machine. If you are using WSL for source control but Windows for packaging, run the package commands in the environment where the frontend dependencies and Electron builder are installed.

```bash
cd /home/albad/FYP/fyp-projects/frontend
bun install --frozen-lockfile
bun run typecheck
bun run test:launcher
bun run test:backend-runtime
bun run test:renderer-flow
bun run build:win
bun run audit:win-package
```

The tracked `bun.lock` file is authoritative, and `package.json` declares the
matching Bun package manager. Do not add a second npm lockfile: competing lock
files make Electron Builder's dependency-manager detection ambiguous.

The portable Windows artifact is written under:

```text
frontend/dist/
```

On Windows PowerShell, record the artifact hash before sharing it between devices:

```powershell
Get-FileHash .\frontend\dist\DistribLLM-1.0.0-portable.exe -Algorithm SHA256
```

Expected result:

- `bun run typecheck` passes.
- launcher and renderer tests pass.
- package audit reports zero forbidden ASAR and backend-runtime entries and verifies every backend checksum.
- both physical devices use the same executable hash for acceptance.

### Current Identity-Preserving-Recovery Test Artifact

The 2026-08-22 identity-preserving-recovery build is:

- file: `frontend/dist/DistribLLM-1.0.0-portable.exe`
- source commit: `9fec4a8903162d34432bc2b7357b665fda8715c1`
- tracked source: clean
- size: `87,658,063` bytes
- SHA-256: `816893d26e6d12e6aae261a5cc15c574268e612cd1aadf4942a69b27d7a2dbba`
- package audit: 36 ASAR entries and zero forbidden entries

This historical executable is superseded by the next schema-four backend-bundled candidate. Do not use it for new final acceptance evidence.

## Developer Backend Override

Normal packaged testing does not update or point at a local checkout. Use this section only when intentionally debugging source outside the acceptance path: enable the developer override in Settings, provide an absolute WSL backend path, and accept that the exported final report will remain false.

From Windows PowerShell:

```powershell
wsl -d Ubuntu -- bash -lc "cd /home/albad/FYP/fyp-projects && git status --short --branch"
```

If the working tree is clean enough to update:

```powershell
wsl -d Ubuntu -- bash -lc "cd /home/albad/FYP/fyp-projects && git fetch origin && git pull --ff-only"
```

Then sync backend dependencies:

```powershell
wsl -d Ubuntu -- bash -lc "cd /home/albad/FYP/fyp-projects/backend && uv sync --frozen --python 3.12"
```

Restart the backend from Electron Settings, or run it manually for debugging:

```powershell
wsl -d Ubuntu -- bash -lc "cd /home/albad/FYP/fyp-projects/backend && uv run --frozen --python 3.12 python main.py"
```

Expected result:

- Electron Settings reports the managed backend as ready.
- `http://127.0.0.1:8000/status` responds from Windows.
- the backend logs reflect the updated source version.

## VPS Bootstrap Relay

Prefer the persistent service for normal testing.

```bash
ssh mohammed@178.156.212.0
sudo systemctl status distribllm-bootstrap.service --no-pager
sudo ss -ltnp 'sport = :7001'
sudo /opt/distribllm/deploy/vps/validate-bootstrap-service.sh
```

Expected result:

- the service is active or the validator reports `ok: true`
- port `7001` is listening
- relay is enabled
- forced reachability is public
- peer ID is stable

The current project relay multiaddress is:

```text
/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y
```

Use that exact value in both local settings:

- bootstrap peers
- trusted relays

### Manual Foreground Bootstrap

Use this only when the service is stopped or you intentionally want a foreground debug process. Run it inside the VPS SSH session, not Windows PowerShell.

```bash
cd ~/fyp/distributed-llm/backend
HIVEMIND_LOGLEVEL=DEBUG \
GOLOG_LOG_LEVEL=relay=debug \
uv run --python 3.12 python bootstrap.py \
  --host 0.0.0.0 \
  --port 7001 \
  --identity_path /var/lib/distribllm/bootstrap.id \
  --announce-maddr /ip4/178.156.212.0/tcp/7001
```

If it prints `bind: address already in use`, the port is already owned by another bootstrap process. Check the service before starting another copy:

```bash
sudo ss -ltnp 'sport = :7001'
sudo systemctl status distribllm-bootstrap.service --no-pager
```

If it prints permission denied for `/var/lib/distribllm/bootstrap.id`, prepare the identity directory from the VPS SSH session:

```bash
sudo mkdir -p /var/lib/distribllm
sudo chown "$USER:$USER" /var/lib/distribllm
chmod 700 /var/lib/distribllm
```

Then rerun the foreground command. After the identity file exists:

```bash
chmod 600 /var/lib/distribllm/bootstrap.id
```

## Relay Probe From A Participant

Run this on a participant backend machine, not on the VPS, when checking whether relay reservation works from outside.

```bash
cd /home/albad/FYP/fyp-projects/backend
RELAY_ADDR="/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y"

DISTRIBLLM_INITIAL_PEERS="$RELAY_ADDR" \
DISTRIBLLM_TRUSTED_RELAYS="$RELAY_ADDR" \
DISTRIBLLM_NETWORK_MODE=relay \
DISTRIBLLM_AUTO_RELAY=true \
DISTRIBLLM_RELAY_WAIT_TIMEOUT=90 \
HIVEMIND_LOGLEVEL=DEBUG \
GOLOG_LOG_LEVEL=autorelay=debug,relay=debug \
uv run --python 3.12 python -m relay_probe --timeout 90 --json
```

Expected result:

- `ok` is `true`
- `circuit_maddrs` contains a `/p2p-circuit/p2p/<participant-peer-id>` address
- `initial_peers` and `trusted_relays` contain the exact VPS relay address

If this passes, bootstrap and relay reservation are working. A later `No healthy complete route` error is then a route, provider health, RPC, model compatibility, or generator readiness problem rather than a basic VPS bootstrap problem.

## VPS Transactional Placement Service

Placement is separate from bootstrap/relay and settlement. It defaults to VPS
loopback port `7200`, stores revisioned leases in SQLite WAL, and must use an
authenticated HTTPS reverse proxy in production. A second SSH loopback tunnel
is acceptable for a controlled two-device test.

On the VPS, copy `deploy/vps/placement.env.example` to
`/etc/distribllm/placement.env`, replace both placeholders with distinct random
values of at least 32 characters, then install and verify:

```bash
cd /opt/distribllm
UV_PATH="$(command -v uv)"
sudo env UV_BIN="$UV_PATH" \
  /opt/distribllm/deploy/vps/install-placement-service.sh /opt/distribllm
sudo systemctl is-active distribllm-placement.service
curl -fsS http://127.0.0.1:7200/health
sudo journalctl -u distribllm-placement.service -n 100 --no-pager
```

For a loopback-only physical test, keep this tunnel open in each participant's
backend WSL distro:

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:7200:127.0.0.1:7200 \
  mohammed@178.156.212.0
```

Configure the same auth token and pinned logical model revision on both devices:

```text
DISTRIBLLM_PLACEMENT_URL=http://127.0.0.1:7200
DISTRIBLLM_PLACEMENT_AUTH_TOKEN=<same coordinator bearer token>
DISTRIBLLM_PLACEMENT_MODEL_REVISION=main
DISTRIBLLM_PLACEMENT_HEARTBEAT_INTERVAL_SECONDS=20
```

Do not copy `DISTRIBLLM_PLACEMENT_TOKEN_SECRET` to participants; only the
coordinator uses it. `placement_unavailable` on a plan or new start is an
intentional fail-closed result. Existing online workers continue serving during
a short outage, while diagnostics show missed renewal. Each participant also
uses a monotonic local safety deadline, shortened by one heartbeat interval, so
it stops serving before the coordinator can reallocate an expired lease. A
coordinator rejection likewise stops the affected worker and suspends any
dependent local generator.

## VPS Incentive Settlement In Shadow Mode

The bootstrap relay and incentive settlement are separate services. The relay listens publicly on TCP port `7001`. Settlement defaults to `127.0.0.1:7101` and must remain in shadow mode until the two-device receipt evidence is reviewed. Do not enable credit mode yet.

### 1. Install The Settlement Service

Run this inside the VPS SSH shell. The checkout must contain `deploy/vps/install-settlement-service.sh` and should be on the reviewed source commit used for the test.

```bash
cd /opt/distribllm
git status --short --branch
test -x deploy/vps/install-settlement-service.sh

UV_PATH="$(command -v uv)"
sudo env UV_BIN="$UV_PATH" \
  /opt/distribllm/deploy/vps/install-settlement-service.sh \
  /opt/distribllm
```

The installer creates `/etc/distribllm/settlement.env` from the locked example when it does not already exist, synchronizes a service-owned virtual environment at `/var/lib/distribllm/settlement-venv`, installs the systemd unit, enables it at boot, and starts it. Keeping this environment under the service state directory prevents Python or package links from pointing into `/root` or a deployment user's home. The default service configuration is:

```text
DISTRIBLLM_INCENTIVES_MODE=shadow
DISTRIBLLM_SETTLEMENT_HOST=127.0.0.1
DISTRIBLLM_SETTLEMENT_PORT=7101
DISTRIBLLM_SETTLEMENT_DB=/var/lib/distribllm/settlement.sqlite3
DISTRIBLLM_RECEIPT_TIMESTAMP_WINDOW=300
```

### 2. Validate The VPS Service

Keep using the VPS SSH shell:

```bash
sudo systemctl is-enabled distribllm-settlement.service
sudo systemctl is-active distribllm-settlement.service
sudo systemctl status distribllm-settlement.service --no-pager
sudo ss -ltnp 'sport = :7101'
curl -fsS http://127.0.0.1:7101/v1/policy
sudo journalctl -u distribllm-settlement.service -n 100 --no-pager
```

Expected result: the service is enabled and active, only the loopback listener owns port `7101`, and `/v1/policy` reports shadow mode and protocol version one.

### 3. Reach Settlement Safely During Physical Testing

Until an HTTPS reverse proxy and project domain are configured, create an SSH tunnel inside the WSL distro that runs each participant backend. Run this in a separate WSL Ubuntu terminal on each device and keep it open:

```bash
ssh -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -L 127.0.0.1:7101:127.0.0.1:7101 \
  mohammed@178.156.212.0
```

On the first connection, SSH asks whether to trust the VPS host key. Verify the
displayed fingerprint from a trusted VPS console before entering `yes`. After
authentication, a successful `ssh -N` command intentionally prints nothing and
keeps the terminal occupied. That means the forward is running; it is not a
hang. The VPS systemd settlement service persists across SSH disconnects and
reboots, but this participant-side tunnel does not. Restart the tunnel whenever
that WSL session or SSH process ends.

Verify the tunnel from that same WSL distro:

```bash
curl -fsS http://127.0.0.1:7101/v1/policy
```

For a durable public deployment, replace the tunnel with an authenticated HTTPS reverse proxy to `127.0.0.1:7101`. Do not expose the plain HTTP settlement listener directly to the internet. Ordinary participants must not receive VPS shell accounts or maintain SSH tunnels. The current incident, implemented publication-persistence repair, production service shape, and remaining release phases are recorded in [Current Two-Device Live-Test Issues And Production Roadmap](CURRENT_TWO_DEVICE_LIVE_TEST_ISSUES.md).

### 4. Configure Every Participant Backend

For a normal packaged run, add these values to
`${XDG_CONFIG_HOME:-$HOME/.config}/distribllm/backend.env` inside each selected
WSL distro and set the file mode to `0600`. A developer checkout may continue
to use its repository-root `.env` fallback. Keep a separate identity file per
device and do not copy identities between participants.

```text
DISTRIBLLM_INCENTIVES_MODE=shadow
DISTRIBLLM_SETTLEMENT_URL=http://127.0.0.1:7101
DISTRIBLLM_IDENTITY_PATH=
DISTRIBLLM_MODEL_REVISION=
DISTRIBLLM_API_ACCESS_MODE=off
DISTRIBLLM_DHT_EXPIRY_SECONDS=90
DISTRIBLLM_ANNOUNCE_INTERVAL_SECONDS=20
DISTRIBLLM_DHT_OPERATION_TIMEOUT=10
DISTRIBLLM_DHT_RECOVERY_FAILURE_THRESHOLD=2
DISTRIBLLM_DHT_RECOVERY_COOLDOWN_SECONDS=60
DISTRIBLLM_P2P_IDENTITY_DIR=
```

Leave `DISTRIBLLM_P2P_IDENTITY_DIR` empty to use the private WSL-local default at `~/.distribllm/p2p-identities`. Every local worker receives a separate key file. Do not copy this directory between devices, do not put it in Git, and do not confuse it with `DISTRIBLLM_IDENTITY_PATH`, which is the application key used to sign useful-work receipts.

Leave `DISTRIBLLM_MODEL_REVISION` blank so the worker uses the immutable commit
resolved by the loader. It may instead be the exact reviewed lowercase
forty-character checkpoint hash as an additional assertion, but it must never
be `main`. Restart the managed backend through the EXE after changing the
managed environment file. Both serving workers and the generator must run in
shadow mode for receipt-capable RPC and countersigned acceptance to be
exercised.

Deploy the matching settlement retry/idempotency source to both sides before
testing an outage: the participant backend supplies bounded retries and the VPS
settlement endpoint makes an exact repeated signed submission safe. A temporary
connection failure then appears as `RETRYING`, with pending and retry counters,
instead of immediately increasing the permanent rejection count. HTTP protocol
or signature errors remain permanent rejections. The retry queue is currently
process-local, so do not restart the backend while submissions are pending.

Expected Incentives page state after restart and a successful distributed generation:

- receipt service shows `SHADOW`;
- settlement connectivity becomes `CONNECTED`;
- temporary tunnel failures show `RETRYING` and recover after the tunnel returns;
- each device shows a distinct application public key;
- accepted receipts and useful positions increase for selected successful workers;
- verified credits remain zero because shadow mode validates evidence without changing balances;
- standby, failed, rejected, and idle providers earn nothing;
- developer API remains off until credit rollout is explicitly approved.

`RETRYING` with `Connection refused` proves the retry-capable build is running,
but it does not prove settlement connectivity. It means signed submissions are
being retained while the participant WSL cannot open local port `7101`. Start or
repair the WSL SSH tunnel, confirm `/v1/policy` from that same distro, and leave
the backend running so pending submissions can drain. Rebuilding the executable
cannot replace the tunnel because the current package has no SSH credential or
tunnel manager.

## Two-Device Live Test Checklist

Use one branch, one backend version, and one executable hash across both devices.

1. Validate the VPS bootstrap service.
2. Confirm both Windows devices can reach `http://127.0.0.1:8000/status` after starting their local managed backend.
3. In Electron Settings on both devices, use the same bootstrap peer and trusted relay multiaddress.
4. Use `Network Mode` as `Auto` for realistic testing, or `Relay` when specifically testing VPS circuit routing.
5. Start provider nodes first.
6. Wait for provider cards to show online and RPC active.
7. Treat Network -> Run Inference as a preflight preview; on a generator-only backend it may remain local-only before startup.
8. Start the independent lease observer shown below after the provider is visible.
9. Start the generator. Its newly created DHT client performs the authoritative route, RPC, and tensor-canary validation.
10. Open Inference and generate several short responses.
11. Check Monitoring for route chain, layer coverage, RPC counters, and timeout messages.
12. Keep the worker, generator, and observer running for at least ten DHT expiry windows.

On Device 2, copy the full worker `peer_id` from:

```bash
curl -fsS http://127.0.0.1:8000/nodes/local | python3 -m json.tool
```

Then run the independent observer in a separate Device 1 WSL terminal. With the documented 90-second expiry, 1,000 seconds covers more than ten expiry windows:

```bash
cd "$HOME/FYP/fyp-projects/backend"
RELAY_ADDR="/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y"
set -o pipefail
uv run --python 3.12 python -m lease_observer \
  --initial-peer "$RELAY_ADDR" \
  --expected-peer "PASTE_FULL_DEVICE_2_PEER_ID" \
  --require-receipt \
  --interval 10 \
  --duration 1000 \
  | tee "$HOME/distribllm-lease-soak.jsonl"
```

Every JSON line must contain `"ok": true`. The command exits nonzero if any observation loses the member lease, provider metadata, normal expert UID, receipt expert UID, or safe expiration horizon. Preserve the JSON Lines file with the runtime snapshots and artifact hashes.

If Device 2 reports a network recovery, verify identity continuity from its local node status:

```bash
curl -fsS http://127.0.0.1:8000/nodes/local | python3 -m json.tool
```

The `announcement` object must show the same `last_network_recovery_peer_id_before` and `last_network_recovery_peer_id_after`, with `last_network_recovery_identity_preserved` equal to `true`. A changed peer ID, a false value, or a recovery error fails the run even if a new provider later appears.

For a split OPT-125M route, the expected layer ranges are half-open:

```text
Device A: 0-6
Device B: 6-12
```

A single full provider is:

```text
0-12
```

Expected healthy states:

- Nodes page: serving nodes are online and RPC active.
- Network page: route says complete through one provider or complete through multiple providers.
- Inference page: generator route becomes ready and the prompt input unlocks.
- Monitoring page: layer coverage is `12/12` for OPT-125M and the route chain names selected providers.

## When The UI Says No Healthy Complete Route

Do not rebuild the executable as the first reaction. Capture the state first:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/runtime/snapshot
Invoke-RestMethod http://127.0.0.1:8000/nodes
Invoke-RestMethod "http://127.0.0.1:8000/models/facebook%2Fopt-125m/serving-plan?layer_count=6"
```

Then unload the suspended generator before retrying:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8000/generator/unload
```

Interpretation:

- If provider cards are online but RPC is offline, wait for health recovery or inspect provider RPC logs.
- If serving-plan is complete but runtime snapshot is suspended, generator readiness rejected the route after deeper health checks.
- If `/models` or serving-plan times out, the backend is overloaded or blocked and needs backend logs before another UI retry.
- If relay probe passes but generator route fails, focus on RPC health and route selection, not the VPS bootstrap.

## Backend-Bundled Runtime Behavior

The implemented package removes the normal backend-checkout dependency:

1. The build copies only the explicit tracked runtime allowlist and excludes tests and mutable state.
2. It writes the exact source commit and a checksum manifest; Electron embeds the manifest digest.
3. First launch verifies and atomically installs the payload into `${XDG_STATE_HOME:-$HOME/.local/state}/distribllm/runtimes/<commit>/backend`.
4. `uv sync --frozen --python 3.12` provisions `${XDG_STATE_HOME:-$HOME/.local/state}/distribllm/environments/<commit>`.
5. Traces, local-model registration, model cache, OAuth tokens, identities, receipts, and API keys remain outside the read-only source.
6. Older commit directories remain available for rollback by launching the older reviewed package.
7. The explicit developer override remains available for debugging but fails the packaged-runtime acceptance check.

The package still requires WSL 2, an Ubuntu distro, `uv`, and network access for the first locked dependency sync. Fully offline dependency images and administrator-level WSL installation remain deferred Sprint 15 work, not hidden package behavior.
