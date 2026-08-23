# Infrastructure Redundancy And Architecture Acceptance

This is the Sprint 32 operator contract. It separates full DHT storage from
circuit forwarding, preserves coordinator and settlement data in separately
permissioned services, and defines the evidence required before the system can
be called resilient.

## Source-Level Boundary

The implementation is based on the installed Hivemind 1.1.12 source, not on a
newer API assumption:

- `role=dht` starts a full DHT server with `client_mode=False`, local DHT
  storage enabled, and libp2p relay disabled;
- `role=relay` starts with `client_mode=True`, local DHT storage disabled, and
  libp2p relay enabled; it joins through one or more full DHT peers;
- `role=combined` remains available in `bootstrap.py` only as the legacy
  rollback mode.

A process split on one VPS improves fault isolation and logs, but it is not
host-level redundancy. Final acceptance requires two DHT peers and two relays
in independent host or provider failure domains.

## Required Production Topology

| Responsibility | Minimum | State and identity | Public port |
|---|---:|---|---|
| Full DHT/bootstrap | 2 independent hosts | one persistent peer key per host | deployment-defined, commonly 7001 |
| Circuit relay | 2 independent hosts | one different persistent peer key per host | deployment-defined, commonly 7002 |
| Placement coordinator | 1 service | separate Unix account and SQLite state | private 7200 by default |
| Settlement | 1 service | separate Unix account and SQLite state | private 7101 by default |

The coordinator and settlement are intentionally not declared highly
available yet. Their outage behavior is fail-closed and must be tested. Do not
put their SQLite files in the same writable directory or run them under the
same Unix identity.

## Fresh DHT Or Relay Installation

Run these commands in the SSH shell of each target VPS. Do not run them in
Windows PowerShell or participant WSL.

```bash
cd /opt/distribllm
sudo ./deploy/vps/install-infrastructure-service.sh dht /opt/distribllm
sudoedit /etc/distribllm-dht/service.env
sudo ./deploy/vps/install-infrastructure-service.sh dht /opt/distribllm
sudo ./deploy/vps/validate-infrastructure-service.sh dht --restart-test \
  | tee "$HOME/distribllm-dht-validation.json"
```

For the relay host:

```bash
cd /opt/distribllm
sudo ./deploy/vps/install-infrastructure-service.sh relay /opt/distribllm
sudoedit /etc/distribllm-relay/service.env
sudo ./deploy/vps/install-infrastructure-service.sh relay /opt/distribllm
sudo ./deploy/vps/validate-infrastructure-service.sh relay --restart-test \
  | tee "$HOME/distribllm-relay-validation.json"
```

The first installer run places a role-specific environment template and does
not start a service while `REPLACE_` placeholders remain. The second run binds
the deployment commit and starts the validated unit. Preserve every identity
file. The DHT and relay identities must never be copied between hosts.

The validator checks the pinned Hivemind version, deployment commit, peer ID,
public multiaddress, role, failure-domain label, persistent identity continuity,
and effective p2pd flags. A DHT report must show DHT-server and relay-disabled
flags. A relay report must show DHT-client and relay-enabled flags.

## Coordinator And Settlement Permission Migration

The hardened units use these isolated locations:

- coordinator account and state: `distribllm-placement` and
  `/var/lib/distribllm-placement`;
- settlement account and state: `distribllm-settlement` and
  `/var/lib/distribllm-settlement`;
- coordinator config: `/etc/distribllm-placement/service.env`;
- settlement config: `/etc/distribllm-settlement/service.env`.

The installers deliberately do not copy a live legacy SQLite database. Stop
the old unit, make a recoverable database backup, verify it, then install the
new unit and copy the stopped database with ownership restricted to the new
role account. Never copy only one file while SQLite is actively using a write
ahead log.

Both HTTP services now expose unauthenticated, non-secret `/health` documents.
The coordinator reports its component role, service protocol, schema,
deployment commit, and topology revision. Settlement reports its component
role, service and receipt protocols, schema, reward policy, mode, and
deployment commit. Journald remains isolated by unit name.

After migration, validate each service and its independent restart in the VPS
SSH shell:

```bash
sudo /opt/distribllm/deploy/vps/validate-control-service.sh coordinator --restart-test \
  | tee "$HOME/distribllm-coordinator-validation.json"
sudo /opt/distribllm/deploy/vps/validate-control-service.sh settlement --restart-test \
  | tee "$HOME/distribllm-settlement-validation.json"
```

## Participant Configuration

On both physical devices, open the packaged Electron Settings page. Put one
full DHT multiaddress per line in **Bootstrap Peers**, one full relay
multiaddress per line in **Trusted Relays**, choose **Auto**, save, and restart
the managed backend. The launcher owns those three values and overrides
repository `.env` entries for them, so editing only `.env` does not change a
saved packaged configuration.

Use the repository-root `.env` only for backend settings the launcher does not
own, including the first rollout mode:

```dotenv
DISTRIBLLM_INCENTIVES_MODE=off
```

The ordered Settings values must be:

```text
Bootstrap Peers:
<DHT_ONE_FULL_MULTIADDR>
<DHT_TWO_FULL_MULTIADDR>

Trusted Relays:
<RELAY_ONE_FULL_MULTIADDR>
<RELAY_TWO_FULL_MULTIADDR>
```

`/network/status` exposes the ordered lists, unique counts, duplicate counts,
and one of `unconfigured`, `single_failure_domain`, or
`redundant_configured`. This is configuration evidence only. It never claims
that two addresses are independent or currently healthy; the failure matrix
proves that separately.

## Controlled Failure Matrix

Capture all logs before restoring a stopped component. Hash each evidence
bundle with `sha256sum`; those hashes go into the architecture matrix.

### Baseline and complementary split

1. Use incentives off on both devices.
2. Serve OPT 125M layers zero through six on Device One and layers six through
   twelve on Device Two.
3. Confirm the selected route names those exact peers and ranges.
4. Complete one session generation and capture session prefill/decode metrics.
5. Record this as `complementary_split` with `complete_alternate=false`. It
   proves distributed execution but not worker redundancy.

### Real provider redundancy

1. Add a third provider that supplies a complete alternate for one selected
   range or the full model.
2. Confirm Monitoring shows a selected complete route and a complete alternate.
3. Stop the selected worker during one controlled request.
4. The in-flight request must either complete once or stop as ambiguous; it
   must never be replayed automatically after uncertain execution.
5. A later request must complete on the alternate route.
6. Record at least three providers, `complete_alternate=true`, and the evidence
   hash under `redundant` and `worker_loss`.

### DHT failure

In the SSH shell of DHT host one:

```bash
sudo systemctl stop distribllm-dht.service
```

Keep DHT host two running. Without restarting either participant, verify that
the network supervisor remains connected, provider discovery remains usable,
and a later prompt completes. Capture both participants and host-two logs. Then
restore host one and validate it again:

```bash
sudo systemctl start distribllm-dht.service
sudo ./deploy/vps/validate-infrastructure-service.sh dht
```

### Relay failure

Start one controlled prompt and, after dispatch, stop only the relay currently
carrying it from that relay host's SSH shell:

```bash
sudo systemctl stop distribllm-relay.service
```

The in-flight result must be classified safely as ambiguous if completion is
unknown. Do not retry that request ID. A new prompt must establish transport
through the alternate relay and complete. Restore and validate the stopped
relay afterward.

### Coordinator failure

In the coordinator VPS SSH shell:

```bash
sudo systemctl stop distribllm-placement.service
```

Existing leases may continue only until their local safety deadline. New
Recommended and Custom starts must fail closed. If the coordinator remains
unavailable through the lease deadline, the provider must stop advertising
before its range can be reallocated. Restore the service, run the coordinator
control-service validator, and confirm the same owner can recover without
creating an overlapping lease.

### Generator recovery

Stop the generator from the product control, leaving providers online. Confirm
the generator role and session caches are cleaned, then restart inference and
complete a new prompt without changing worker identities.

## Recovery Objectives

| Failure | Existing lease | New placement | In-flight work | Later request |
|---|---|---|---|---|
| Coordinator | valid only to local safety deadline | fail closed immediately | may finish before provider deadline | succeeds after coordinator recovery and lease validation |
| One DHT peer | unchanged | allowed only if coordinator is healthy | should not be interrupted | discovers through surviving DHT peer |
| Active relay | unchanged | unchanged | fail safely if outcome is uncertain | uses alternate relay |
| Selected worker | lease is lost or expires | alternate reservation remains authoritative | no ambiguous replay | bounded alternate route only |
| Generator | provider leases unchanged | unchanged | cancelled/closed locally | fresh generator identity/session succeeds |

Default provider advertisement expiry is ninety seconds and renewal is every
twenty seconds. Placement heartbeat is twenty seconds; online lease expiry is
three hundred seconds unless deployment values override them. Record the
effective values with the evidence rather than assuming defaults.

## Evidence Validation

Copy `docs/ARCHITECTURE_FAILURE_MATRIX_TEMPLATE.json` into the private evidence
directory and replace every placeholder from the captured reports. The template
fails closed until each test has real evidence. It is a JSON document with kind `distributed_architecture_failure_matrix`,
schema version one, the participant commit, the six component records, pinned
protocol revisions, five failure scenarios, eight topology gates, and rollout
ordering. Every scenario and topology item contains `ok=true` plus the SHA-256
of its evidence bundle. Validate it in participant WSL:

```bash
cd /home/albad/FYP/fyp-projects/backend
uv run --python 3.12 python -m architecture_acceptance \
  --matrix "$HOME/distribllm-evidence/architecture-matrix.json" \
  --expected-source-commit "$(git rev-parse HEAD)" \
  --output "$HOME/distribllm-evidence/architecture-validated.json"
```

Then pass the same physical matrix into the final cross-sprint manifest with
`--architecture-report`. The final manifest validates it again and binds its participant commit
to both Windows artifact reports and will reject missing redundancy, colocated
failure domains, duplicate peer identities, an unproven three-provider route,
protocol drift, missing evidence hashes, or shadow testing performed before
the incentives-off baseline.

Do not enable credit mode after this procedure. Credit still requires explicit
manual review and sprint closure approval.
