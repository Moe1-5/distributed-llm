# VPS Relay Operations

**Status:** Service implementation complete; live VPS restart validation pending
**Last updated:** 2026-08-13

This runbook manages the project-owned Hivemind DHT bootstrap and circuit relay as a persistent `systemd` service. The service does not run the Electron application, expose the participant FastAPI API, or serve model layers.

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
3. Run the installer again. It performs a locked backend sync, records the new commit, regenerates the unit with the checkout path, and restarts the enabled service.
4. Run normal validation, restart validation, and an external participant relay probe.
5. Keep the previous commit available until the external probe and two-device inference gate pass.

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

## Remaining Acceptance Gates

The checked-in service, validator, and this runbook are implementation evidence. Sprint 16 remains open until an operator runs the live restart check on the project VPS, an external participant obtains a fresh circuit after that restart, two Windows/WSL devices complete relayed OPT-125M inference across non-overlapping layer ranges, monitoring reports relay mode accurately, and direct mode is tested separately.
