# VPS Relay Operations

> The combined bootstrap/relay procedure below is retained for the Sprint 16
> baseline and rollback. New resilient deployments must use the separated
> role procedure in [Infrastructure Redundancy And Architecture Acceptance](INFRASTRUCTURE_REDUNDANCY_ACCEPTANCE.md).

**Status:** Service implementation complete; live VPS restart validation pending
**Last updated:** 2026-08-16

This runbook manages the project-owned Hivemind DHT bootstrap and circuit relay as a persistent `systemd` service. The service does not run the Electron application, expose the participant FastAPI API, or serve model layers.

## Manual Foreground Launch

Use this foreground command when validating the relay manually before, during, or instead of the managed `systemd` service. Run it from the backend directory of the deployed repository checkout on the VPS:

These are Linux commands for the VPS SSH session. Do not run them from local Windows PowerShell. Your prompt should look like `mohammed@deli-backend-prod:...$`, not `PS C:\...>`.

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

For a root-owned production checkout, replace the first line with the deployed path, for example:

```bash
cd /opt/distribllm/backend
```

Keep this process running for the whole participant test. If this terminal closes, participant nodes may lose discovery and relay reservation support.

### Identity Permissions For Manual Launch

The identity path is under `/var/lib/distribllm`, so a normal shell user cannot create it unless the directory and file are prepared first. If the foreground command fails with `PermissionError: [Errno 13] Permission denied: '/var/lib/distribllm/bootstrap.id'`, choose one of these operator-safe paths:

Run these commands inside the VPS SSH session only:

For a quick manual test as the current SSH user, create or transfer ownership of only the bootstrap identity directory:

```bash
sudo mkdir -p /var/lib/distribllm
sudo chown "$USER:$USER" /var/lib/distribllm
chmod 700 /var/lib/distribllm
```

Then rerun the foreground command. Hivemind will create `/var/lib/distribllm/bootstrap.id` on first successful start. After it exists, lock down the identity file:

```bash
chmod 600 /var/lib/distribllm/bootstrap.id
```

For the managed production service, prefer the installer instead of changing ownership manually:

```bash
sudo /opt/distribllm/deploy/vps/install-bootstrap-service.sh /opt/distribllm
sudo systemctl restart distribllm-bootstrap.service
```

Do not use `sudo uv run ... python bootstrap.py` as the default manual workflow. It can create a root-owned virtual environment, cache, or identity state inside the checkout and make later non-root operations harder to reason about.

### What The Command Does

- `HIVEMIND_LOGLEVEL=DEBUG` enables detailed Python-side Hivemind logs.
- `GOLOG_LOG_LEVEL=relay=debug` enables focused relay logs from the bundled libp2p daemon.
- `uv run --python 3.12` runs the backend with Python 3.12 and the project dependency lock instead of whichever Python happens to be active in the shell.
- `python bootstrap.py` starts the DistribLLM bootstrap infrastructure process.
- `--host 0.0.0.0` listens on all VPS network interfaces.
- `--port 7001` binds the public TCP port used by participant machines.
- `--identity_path /var/lib/distribllm/bootstrap.id` loads the stable private p2p identity. Preserve this file; changing it changes the peer ID and invalidates saved participant addresses.
- `--announce-maddr /ip4/178.156.212.0/tcp/7001` tells remote participants the public address they should dial. This must use the VPS public IP and the same port that is open in the provider firewall and host firewall.

### Expected Startup Evidence

A healthy foreground launch prints these important facts:

```text
Relay transport/service: enabled
Forced reachability: public
Peer ID: QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y
Bootstrap addresses (share these with your nodes):
  /ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y
```

The exact public participant configuration must include the complete multiaddress, including `/p2p/<peer-id>`, as both the bootstrap peer and trusted relay:

```text
/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y
```

The peer ID above was re-derived from the persistent identity file during the
2026-08-23 managed-service restart test. The identity-file SHA-256 remained
`34629a9d7ec3ede4a7b12eb3f49537f172cf6f597bed0425dbd7986007a9eab3`
before and after restart. Older references to `QmTXjKi...` are stale deployment
configuration, not evidence that the identity rotated. Transient `12D3KooW...`
IDs in the service journal belong to independent reachability-check clients and
are not the bootstrap identity.

### Common Launch Checks

Before launching, confirm that port `7001` is open and not already owned by another bootstrap process:

```bash
sudo ufw allow 7001/tcp
sudo ss -ltnp 'sport = :7001'
pgrep -af 'bootstrap.py|p2pd'
```

Run exactly one active bootstrap with the stable identity. If another copy is already running, stop that copy through its current process manager before starting a replacement.

## Deployment Contract

- Repository checkout: `/opt/distribllm` by default.
- Service account: `distribllm`, with no interactive shell.
- Unit: `distribllm-bootstrap.service`.
- Environment: `/etc/distribllm/bootstrap.env`, mode `0640`.
- Stable private identity: `/var/lib/distribllm/bootstrap.id`.
- Non-secret runtime evidence: `/run/distribllm/bootstrap-status.json`.
- Public TCP port: `7001` for the current deployment.
- Locked runtime: Python 3.12 and the Hivemind version in `backend/uv.lock`.

Never replace or delete the identity file during an upgrade. A new identity creates a new peer ID and invalidates every participant address that contains the previous ID.

## First Installation

1. Put the intended feature branch or approved commit at `/opt/distribllm` and confirm the checkout is clean enough to deploy:

```bash
cd /opt/distribllm
git status --short --branch
git rev-parse HEAD
```

2. Install `uv`, open TCP port `7001` in both the provider firewall and the VPS firewall, and ensure no unmanaged bootstrap process is already bound to the port:

```bash
sudo ufw allow 7001/tcp
sudo ss -ltnp 'sport = :7001'
pgrep -af 'bootstrap.py|p2pd'
```

Stop an older process through its existing process manager. Run exactly one project bootstrap identity on the public address.

3. Review `deploy/vps/bootstrap.env.example`. The checked-in values describe the current project VPS. In particular, verify the public IP, port, expected peer ID, identity path, and expected Hivemind version.

4. Install and start the service:

```bash
sudo /opt/distribllm/deploy/vps/install-bootstrap-service.sh /opt/distribllm
```

On first installation, the script creates `/etc/distribllm/bootstrap.env`. Review it before participant testing. On later installations, operator edits are preserved and only `DISTRIBLLM_DEPLOY_COMMIT` is updated to the deployed Git commit.

Because upgrades preserve operator configuration, reconcile an intentionally
changed project peer expectation before reinstalling. For the 2026-08-23
identity correction, run:

```bash
sudo sed -i \
  's/^DISTRIBLLM_EXPECTED_PEER_ID=.*/DISTRIBLLM_EXPECTED_PEER_ID=QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y/' \
  /etc/distribllm/bootstrap.env
sudo grep '^DISTRIBLLM_EXPECTED_PEER_ID=' /etc/distribllm/bootstrap.env
```

This changes validation configuration only. It does not modify the private
identity file or change the running peer ID.

The managed identity must remain under `/var/lib/distribllm` and runtime status under `/run/distribllm`, matching the unit's write restrictions. If the identity already exists from the foreground deployment, the installer keeps its contents and normalizes ownership to the service account with mode `0600`.

5. Inspect service state and the startup evidence:

```bash
sudo systemctl status distribllm-bootstrap.service --no-pager
sudo journalctl -u distribllm-bootstrap.service -b --no-pager
sudo /opt/distribllm/deploy/vps/validate-bootstrap-service.sh
```

The validator checks the active and enabled state, peer ID, public multiaddress, deployed commit, identity path, Python version, Hivemind version, relay state, forced public reachability, and effective p2pd relay flags. It prints a versioned validation report containing non-secret runtime status, but never the private identity.

## Restart Validation

Run the destructive-to-process but identity-preserving restart check during a maintenance window:

```bash
mkdir -p ~/distribllm-evidence
sudo /opt/distribllm/deploy/vps/validate-bootstrap-service.sh --restart-test \
  > ~/distribllm-evidence/vps-restart.json
```

The command records the pre-restart status and identity hash, restarts the unit, waits for fresh status, and requires all of the following:

- The service returns to active state.
- The process ID changes.
- The peer ID and identity path stay unchanged.
- The identity file hash stays unchanged.
- The public multiaddress, deployment commit, runtime versions, and relay flags remain correct.

Afterward, run a participant relay probe. This proves a reservation from outside the VPS rather than only validating local service evidence:

```bash
cd /path/to/distribllm/backend
HIVEMIND_LOGLEVEL=DEBUG \
GOLOG_LOG_LEVEL=autorelay=debug,relay=debug \
uv run --python 3.12 python -m relay_probe --timeout 90 --json \
  --validation-context ~/distribllm-evidence/vps-restart.json \
  > ~/distribllm-evidence/post-restart-relay-probe.json
```

A passing result contains a complete `/p2p-circuit/p2p/<participant-peer-id>` address and reports `relay_discovery` as false for the single configured trusted relay.

The validator writes only its versioned JSON report to standard output after runtime status, effective relay flags, PID replacement, peer identity, identity-file hash continuity, and service state have all passed. Its human success message goes to standard error, so shell redirection keeps the report valid JSON. The relay probe records the SHA-256 of that exact report through `--validation-context`; final acceptance rejects an older probe paired with a newer restart report.

## Upgrade

1. Fetch and check out the approved feature branch or commit under `/opt/distribllm`.
2. Record the previous commit with `git rev-parse HEAD` before changing it.
3. Compare the checked-in environment example with `/etc/distribllm/bootstrap.env` and explicitly reconcile reviewed project defaults such as the expected peer ID. The installer preserves operator configuration.
4. Run the installer again. It performs a locked backend sync, records the new commit, regenerates the unit with the checkout path, and restarts the enabled service.
5. Run normal validation, restart validation, and an external participant relay probe.
6. Keep the previous commit available until the external probe and two-device inference gate pass.

Do not copy `.env`, model caches, participant tokens, or desktop application state to the VPS relay checkout.

## Recovery

### Service Does Not Start

```bash
sudo systemctl status distribllm-bootstrap.service --no-pager
sudo journalctl -u distribllm-bootstrap.service -n 200 --no-pager
sudo systemctl cat distribllm-bootstrap.service
sudo cat /etc/distribllm/bootstrap.env
```

Check that the checkout still exists, the backend virtual environment is executable, all required environment values are non-empty, the announce multiaddress ends with the configured TCP port, and no other process owns that port. Correct the deployment or environment and then run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart distribllm-bootstrap.service
sudo /opt/distribllm/deploy/vps/validate-bootstrap-service.sh
```

### Port 7001 Is Already In Use

If the foreground command fails with `bind: address already in use`, the identity is loading but another process already owns the public relay port. First inspect the owner from the VPS SSH shell:

```bash
sudo ss -ltnp 'sport = :7001'
pgrep -af 'bootstrap.py|p2pd'
sudo systemctl status distribllm-bootstrap.service --no-pager
```

If `distribllm-bootstrap.service` is active and listening on port `7001`, do not start a second foreground bootstrap. Use the service and validate it:

```bash
sudo /opt/distribllm/deploy/vps/validate-bootstrap-service.sh
```

If you intentionally want the manual foreground process instead, stop the service first:

```bash
sudo systemctl stop distribllm-bootstrap.service
sudo ss -ltnp 'sport = :7001'
```

If `ss` or `pgrep` shows an older unmanaged manual process, stop that exact PID with normal `kill <pid>`, confirm the port is free, then rerun the foreground command. Avoid `kill -9` unless a normal termination fails and the stale process is clearly identified.

### Peer ID Changes

Stop participant rollout immediately. Confirm that `DISTRIBLLM_BOOTSTRAP_IDENTITY_PATH` still points to `/var/lib/distribllm/bootstrap.id` and restore the backed-up identity file with owner `distribllm`, group `distribllm`, and restrictive permissions. Restart and validate before publishing any new peer address. Do not silently update participants to an unexplained identity.

### Port Is Reachable but Reservations Fail

Confirm recent startup logs include `-relay=1` and `-forceReachabilityPublic=1`, then run the service validator. From the participant, verify the same complete VPS address is configured for `DISTRIBLLM_INITIAL_PEERS` and `DISTRIBLLM_TRUSTED_RELAYS`, and run the minimal relay probe with debug logging. A successful TCP connection alone does not prove circuit reservation.

### Roll Back

Check out the previously recorded commit, rerun the installer, and repeat restart validation plus the external probe. Preserve `/var/lib/distribllm/bootstrap.id` throughout rollback.

## Backup and Monitoring

- Back up `/var/lib/distribllm/bootstrap.id` encrypted and access-controlled. Test restoration without publishing a second active copy of the same identity.
- Alert when the unit is inactive, repeatedly restarting, missing runtime status, or no longer listening on the expected port.
- Retain enough service journal history to diagnose relay reservation failures while avoiding participant prompts, tokens, or model data.
- Treat the JSON status file as operational metadata. It is non-secret but should remain readable only to the service group and administrators.

## Transport Quotas Remain External

Worker RPC safety limits bound model inputs, admission, and execution on participant devices. They do not impose per-peer connection, duration, ingress, egress, or bandwidth quotas on the VPS circuit relay. Those controls remain an infrastructure task: monitor provider traffic and cost, retain relay connection metrics, and apply reviewed host/provider limits only after measuring successful two-device inference. Do not infer a 128 KiB relay quota from a log interval ending at 131,072 bytes; that value can be a flow-control window when an endpoint resets.

Authenticated API access and per-identity relay quotas are also deferred. The current firewall should expose only the required relay port and administrative access, but a firewall port rule is not a bandwidth policy.

## Remaining Acceptance Gates

The checked-in service, validator, and this runbook are implementation evidence. Sprint 16 remains open until an operator runs the live restart check on the project VPS, an external participant obtains a fresh circuit after that restart, two Windows/WSL devices complete relayed OPT-125M inference across non-overlapping layer ranges, monitoring reports relay mode accurately, and direct mode is tested separately.
