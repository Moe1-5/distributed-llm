# Network Reachability and Relay Review

**Status:** Public circuit and expert metadata RPC verified; two-device inference pending
**Date:** 2026-08-12
**Scope:** Multi-device Hivemind RPC reachability, Petals comparison, temporary testing, and the production DistribLLM network design

## Executive Summary

The two-device failure observed in Sprint 14 is a transport reachability problem, not a model discovery or layer-routing problem. Both Windows/WSL devices join the DistribLLM DHT through the public VPS bootstrap and advertise enough compatible layers to form a complete route. Inference fails because each server advertises a private WSL address and random port that the other physical device cannot dial.

Connecting both devices to the same hotspot may help with a controlled local test, but it does not fix private WSL addressing on its own. Tailscale can provide a useful development overlay, but requiring membership in a private tailnet is not suitable for the intended public/discoverable production swarm.

Petals handles this class of problem by testing direct reachability, using direct addresses where possible, and enabling Hivemind/libp2p automatic relay operation when a server is behind NAT or a firewall. It also validates externally observable reachability before treating a public server as usable.

DistribLLM should follow the same general design:

1. Attempt and verify direct connectivity.
2. Use an explicit fixed port and announce address when the node is publicly reachable.
3. Fall back to an outbound connection through a trusted project-owned circuit relay when direct dialing fails.
4. Validate the actual expert RPC endpoint before declaring the route ready.
5. Record and expose whether every selected hop is direct or relayed.

The VPS should host DistribLLM network infrastructure: bootstrap discovery, trusted relay service, and an external reachability checker. The Electron application, local FastAPI control API, and participant model layers should remain on participant machines unless a VPS is intentionally acting as a compute worker.

## Live Deployment Status - 2026-08-12

The updated bootstrap process is now running on the VPS with its persistent identity and public address:

```text
/ip4/178.156.212.0/tcp/7001/p2p/QmTXjKiMggt92DP4CLbDwMCfLd4L1aNKyBnja5apT2ZZL2
```

That address records the 2026-08-12 deployment observation. It is superseded
for current operation by the peer ID re-derived from the preserved identity on
2026-08-23:

```text
/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y
```

The persistent identity hash was unchanged across the 2026-08-23 restart. The
old expected-peer setting was stale; the restart did not rotate the identity.

Confirmed:

- The VPS firewall allows inbound TCP port `7001`.
- A Windows participant can establish a TCP connection to the public VPS port.
- The bootstrap loads the persistent identity from `/var/lib/distribllm/bootstrap.id`.
- The participant uses the VPS as both its initial peer and trusted relay.
- The participant's direct check selected relay fallback as expected for its private network.
- A Windows/WSL participant using Python `3.12.3` and Hivemind `1.1.12` obtained a complete `/p2p-circuit/` address through the VPS in `1.633` seconds.
- Participant logs confirm static trusted-relay selection with `-relayDiscovery=0`, private reachability, DHT client mode, and successful relay protocol detection.
- A full-layer cached OPT-125M expert reserved a circuit, and a second independent Hivemind peer used only that circuit address to discover the complete route and complete the real `expert.info` readiness RPC in `10.116` seconds total.

Remaining validation:

- Tensor forwarding through the relay has not yet been validated independently of metadata RPC.
- Two Windows/WSL participants have not yet completed distributed inference over the relay fallback.
- The persistent VPS service and recovery tooling are checked in; installation and restart continuity still need live VPS validation.

The original timeout was corrected by selecting the one configured trusted relay statically and polling fresh daemon addresses. Increasing the timeout alone would not have fixed either cause. Sprint 16 now progresses to relayed tensor forwarding and two-device inference.

## Confirmed Sprint 14 Issue

The two-device OPT-125M test established the following:

- Device A served layers `0-6`.
- Device B served layers `6-12`.
- Both devices joined the same project-owned DHT through the public VPS bootstrap.
- DHT metadata discovery found both servers and complete contiguous layer coverage.
- Generator startup reached route validation.
- The expert RPC probe failed with `routing: not found`.

The advertised addresses were private WSL addresses similar to:

```text
Device A: /ip4/172.24.x.x/tcp/<random-port>/p2p/<peer-id>
Device B: /ip4/172.27.x.x/tcp/<random-port>/p2p/<peer-id>
```

The two `172.x` addresses belong to separate WSL virtual networks. They are not mutually routable across the two physical Windows devices.

## Discovery Is Not RPC Reachability

The current topology behaves like this:

```text
Device A ---- DHT discovery ----> VPS bootstrap <---- DHT discovery ---- Device B

Device A ---- expert RPC --------X--------> Device B private WSL address
```

The VPS bootstrap helps peers enter the same DHT and exchange metadata. Successful bootstrap connectivity proves outbound discovery traffic works. It does not prove that a generator can open an inference RPC stream to every discovered serving peer.

The generator needs one of these data-plane paths for every server:

- A directly reachable LAN address.
- A directly reachable public address and forwarded port.
- A reachable overlay-network address.
- A libp2p circuit-relay address through a public relay.

## Temporary Testing Options

### Same Hotspot or LAN

A shared hotspot is a valid temporary test environment only when:

- The hotspot permits communication between connected devices.
- WSL accepts connections originating from the LAN.
- Windows and Hyper-V firewall rules permit the serving port.
- The serving process uses a predictable port.
- The node advertises an address the other device can reach instead of its private WSL address.

Recent Windows 11 versions can use WSL mirrored networking to improve direct LAN access. Default WSL NAT mode may instead require Windows port forwarding from the Windows LAN address to the WSL virtual-machine address.

The hotspot alone is not a reliable product solution because hotspot client isolation, WSL NAT, address changes, and firewall policy can still prevent inbound RPC.

### Exact Answer: Must Every Device Open a Port?

**No, not when relay fallback works.** Petals does not require every participant to manually configure a router port-forwarding rule.

There are three different meanings of "open a port" that should not be mixed together:

1. **Application listening port:** A serving process always needs a local socket on which Hivemind can receive RPC streams. Petals may choose this port automatically with port `0`, or an operator may choose a fixed port.
2. **Operating-system firewall permission:** Windows, Hyper-V, or Linux must allow the Hivemind process to use its listening socket. This may require an application or inbound firewall rule for direct LAN operation.
3. **Router public port forwarding:** This exposes a device behind a home router to the public Internet. It is needed only for an operator who deliberately wants a directly reachable public server and whose router cannot configure the mapping automatically. It is not required for relay mode.

For two devices on the same normal LAN, no public router port-forwarding rule is needed. The devices still need a mutually reachable LAN path and local firewall permission. With native Linux this is usually straightforward. With WSL 2, the private `172.x` virtual address may still be hidden behind the Windows host, so use one of these paths:

- WSL mirrored networking plus the appropriate Windows/Hyper-V firewall permission.
- A fixed Hivemind port, Windows port forwarding to WSL, and announcement of the Windows LAN address.
- The production relay fallback, which avoids inbound LAN and router configuration by using an outbound connection from WSL to the relay.

For devices on different Internet connections, manual public port forwarding is optional. A directly reachable operator can configure it for lower latency, while ordinary NAT-separated users should be able to use the project relay without changing their router.

### Tailscale or Another Overlay Network

Tailscale can give development devices stable overlay addresses and often establishes a direct encrypted path through NAT. It is useful for reproducing multi-device inference and separating model defects from network defects before the project relay is ready.

It is not the intended final public-swarm transport because every participant would need to join an administratively controlled overlay network.

## How Petals Handles the Problem

The local `PETALS_COMPARISON.md` explains Petals routing, sessions, and fault tolerance, but does not currently describe its direct-versus-relay startup behavior.

### Source-Code Findings

The relevant upstream Petals source establishes these facts:

| Source | Finding |
| --- | --- |
| `src/petals/cli/run_server.py` | The default server listens on IPv4 and IPv6 with port `0`, meaning a random local port is allowed. AutoRelay is enabled unless the operator passes `--no_auto_relay`. |
| `src/petals/cli/run_server.py` | Operators may provide `--port`, `--public_ip`, `--host_maddrs`, or `--announce_maddrs`. A public IP requires a fixed non-zero port. |
| `src/petals/server/server.py` | Petals probes direct reachability with relay disabled, then sets `reachable_via_relay` when that direct test fails. |
| `src/petals/server/server.py` | The real server starts with `use_relay=True`, `use_auto_relay=True`, and client DHT mode when relay reachability is needed. |
| `src/petals/server/server.py` | Petals records `using_relay` in server information and validates public-swarm reachability before advertising the loaded blocks as online. |
| `src/petals/cli/run_dht.py` | Petals' lightweight public DHT/bootstrap process enables circuit-relay functionality by default unless started with `--no_relay`; it also supports explicit host and announce addresses. |

This means Petals supports both kinds of server: directly reachable operators who choose or expose a port, and ordinary NAT-separated operators who depend on automatic relay connectivity. It does not impose manual public port forwarding on every server.

### 1. Direct Reachability Probe

Petals first calls `check_direct_reachability` with relay disabled. This determines whether other peers can dial the server without incorrectly counting a relay path as direct connectivity.

```python
is_reachable = check_direct_reachability(
    initial_peers=initial_peers,
    use_relay=False,
)
```

### 2. Automatic Relay Fallback

If direct reachability fails, Petals treats the server as relay-reachable and starts its DHT peer with relay and automatic relay discovery enabled.

```python
reachable_via_relay = is_reachable is False

dht = hivemind.DHT(
    initial_peers=initial_peers,
    start=True,
    use_relay=True,
    use_auto_relay=True,
    client_mode=reachable_via_relay,
)
```

A private peer maintains an outbound connection to a public circuit relay. Other peers dial the private server through a relay multiaddress rather than attempting its unreachable private address.

### What a Public Circuit Relay Is

A circuit relay is a publicly reachable forwarding peer. "Public" means the relay has an Internet-reachable address and listening port; it does not mean the inference contents are published or sent as unencrypted HTTP.

The relay is useful because outbound connections usually work through WSL NAT, home routers, university networks, and mobile hotspots even when unsolicited inbound connections are blocked.

For a serving Device B behind NAT, the flow is:

```text
1. Device B  ---- outbound connection/reservation ---->  VPS relay
2. Device B  ---- advertises relay route ------------->  DHT
3. Device A  ---- asks VPS relay for Device B -------->  VPS relay
4. VPS relay ---- forwards the stream ---------------->  Device B
5. Device A  <==== Hivemind expert RPC through relay ==> Device B
```

The address advertised for Device B contains both the relay identity and Device B's identity. A complete relay multiaddress has this general shape:

```text
/ip4/<vps-public-ip>/tcp/<relay-port>/p2p/<relay-peer-id>/p2p-circuit/p2p/<device-b-peer-id>
```

When Device A dials that address:

1. Device A connects to the public VPS relay.
2. The relay finds its existing reserved connection to Device B.
3. The relay joins the two ends into one bidirectional libp2p stream.
4. Hivemind sends the normal expert RPC messages through that stream.
5. Device B computes its model layers locally and returns the output through the same stream.

The VPS does not load or execute Device B's model layers. It forwards network bytes. libp2p circuit traffic remains end-to-end secured between the peers, although the relay can observe peer identities, connection timing, and traffic volume. The cost is an extra network hop: relay latency rises, and VPS ingress/egress bandwidth carries the activation tensors in both directions.

In DistribLLM, the current VPS bootstrap and the first relay may be the same process or host, but the roles remain different:

- **Bootstrap role:** tells peers how to enter the DHT and discover other peer identities and addresses.
- **Relay role:** accepts relay reservations and forwards live RPC streams to otherwise unreachable peers.

The bootstrap implementation now enables relay support and hosts the Petals-derived reachability-check protocol. The deployed VPS has accepted a Windows/WSL worker reservation, produced a complete `p2p-circuit` address, and carried an independent expert metadata RPC. Tensor forwarding and distributed inference between two devices remain the final live transport proof.

### 3. Optional Direct Address Configuration

The Petals server CLI supports a serving port, public IP, explicit host multiaddresses, explicit announce multiaddresses, and disabling automatic relay for deliberate direct-only behavior.

When an operator supplies a public IP, Petals requires a fixed non-zero port. This prevents advertising a public address with an unknown random port.

### 4. External Reachability Validation

For its public swarm, Petals performs an external reachability validation before completing server startup. This catches the false-positive state seen in DistribLLM: locally constructed metadata can look correct even though an independent peer cannot dial the server.

### 5. Relay State in Server Metadata

Petals records whether a server is using a relay. Performance and routing logic can therefore distinguish direct servers from relayed servers instead of treating every discovered provider as equivalent.

### Primary References

- Petals server startup: <https://github.com/bigscience-workshop/petals/blob/main/src/petals/server/server.py>
- Petals server CLI: <https://github.com/bigscience-workshop/petals/blob/main/src/petals/cli/run_server.py>
- Petals lightweight DHT/bootstrap CLI: <https://github.com/bigscience-workshop/petals/blob/main/src/petals/cli/run_dht.py>
- Petals repository: <https://github.com/bigscience-workshop/petals>
- Hivemind DHT documentation: <https://learning-at-home.readthedocs.io/en/latest/user/dht.html>
- libp2p circuit relay: <https://docs.libp2p.io/concepts/circuit-relay/>
- libp2p AutoNAT: <https://docs.libp2p.io/concepts/nat/autonat/>
- Microsoft WSL networking: <https://learn.microsoft.com/en-us/windows/wsl/networking>

## What the Installed Hivemind Version Supports

DistribLLM uses Hivemind `1.1.12`. Its P2P configuration exposes:

- `host_maddrs`: local listening addresses and ports.
- `announce_maddrs`: externally visible advertised addresses.
- `auto_nat`: public-reachability detection.
- `nat_port_map`: attempted automatic NAT port mapping.
- `use_relay`: support for dialing through circuit relays.
- `use_auto_relay`: automatic relay selection behind NAT or a firewall.
- `trusted_relays`: an explicit relay allowlist.
- `tls`: encrypted libp2p transport.

DistribLLM now exposes these settings through typed environment configuration. A serving node performs the Petals-derived direct probe, selects direct or relay mode, waits for a circuit address in relay mode, and publishes its connection mode and verification state. Generators enable relay dialing and still probe the selected expert RPC endpoints before reporting route readiness.

## Recommended Production Architecture

### Connection Strategy

Every serving peer should attempt connection modes in this order:

```text
Verified direct connection
        ↓ if unavailable
Automatic NAT port mapping and address discovery
        ↓ if unavailable
Trusted project-owned circuit relay
```

Direct connections should remain preferred because transformer activations can consume substantial bandwidth and relay traffic adds an extra network hop.

### Is This the Solution?

**Yes: the Petals-style direct-or-relay design is the recommended production solution to the confirmed reachability defect.** It removes the requirement that every participant manually expose a public port while preserving a faster direct path for operators who are reachable.

The architecture is selected and locally implemented. The defect remains open until the following live checks pass:

- Deployment of the updated relay-capable bootstrap to the VPS.
- Correct circuit multiaddress advertisement.
- Remote expert RPC through the relay.
- Failure behavior when the relay disappears.

If the first Hivemind `1.1.12` relay prototype cannot reserve or advertise a working circuit route, the team should stop and reassess the installed transport version or a separate compatible relay implementation. It should not fall back to requiring every end user to configure public router ports as the product default.

### Infrastructure Roles

```text
Public DistribLLM infrastructure
├── Bootstrap peers
│   └── Stable entry points for the project-owned DHT
├── Trusted circuit relays
│   └── Forward encrypted libp2p streams for NAT-separated peers
├── Reachability validators
│   └── Dial serving peers from outside their local network
└── Monitoring
    └── Relay capacity, connection mode, failures, latency, and traffic

Participant device
├── Electron application
├── Local FastAPI control API
├── Hivemind serving peer
└── Locally hosted model layers
```

For the first deployment, one VPS may host bootstrap, relay, validation, and monitoring roles. Mature production should replicate or separate these roles to remove the single point of failure.

### What Should Be Published to the VPS

Publish:

- A persistent Hivemind/libp2p identity stored outside version control.
- One or more stable public bootstrap addresses.
- An explicitly configured circuit-relay service.
- A reachability-validation service.
- Relay and bootstrap metrics.

Do not publish merely to solve this issue:

- The Electron renderer.
- Each participant's normal FastAPI backend.
- FastAPI port `8000` as a public control interface.
- Participant model weights or layers, unless the VPS intentionally joins as a compute worker.

### VPS Relay Deployment Runbook

The maintained service installation, validation, restart, upgrade, rollback, and recovery procedure is in [VPS Relay Operations](VPS_RELAY_OPERATIONS.md). The commands below remain useful for a foreground diagnosis before service installation.

The first production-style deployment should run one public VPS process that provides the stable bootstrap address, relay reservations, and the direct-reachability check protocol. This VPS does not expose the desktop app, the local FastAPI control API, or participant model layers.

1. Choose a stable TCP port, such as `7001`.
2. Open that port in the VPS operating-system firewall and the cloud-provider firewall/security group:

```bash
sudo ufw allow 7001/tcp
```

3. Deploy the same repository commit to the VPS, for example under `/opt/distribllm`, and record it:

```bash
cd /opt/distribllm/backend
git rev-parse --short HEAD
```

4. Install the locked backend environment with the verified Python version and print the effective runtime. `uv run` uses this project environment, so activating `.venv` is optional:

```bash
uv sync --frozen --python 3.12
uv run --python 3.12 python -c "import sys, hivemind; print(sys.version); print(hivemind.__version__)"
```

The expected Hivemind version for the current lockfile is `1.1.12`. Do not launch the VPS with an unrelated global `python`; that can select a different Hivemind package and bundled `p2pd` binary.

5. Create the persistent state directory, check for an older duplicate process, and stop that old process through its process manager before continuing:

```bash
sudo install -d -m 0750 -o "$USER" -g "$USER" /var/lib/distribllm
pgrep -af "bootstrap.py|p2pd"
```

Run exactly one bootstrap process. Preserve the existing identity file if its peer ID is already configured on participants.

6. Start the relay-capable bootstrap in the foreground with focused diagnostics:

```bash
HIVEMIND_LOGLEVEL=DEBUG \
GOLOG_LOG_LEVEL=relay=debug \
uv run --python 3.12 python bootstrap.py \
  --host 0.0.0.0 \
  --port 7001 \
  --identity_path /var/lib/distribllm/bootstrap.id \
  --announce-maddr /ip4/<VPS_PUBLIC_IP>/tcp/7001
```

The startup header must report Python 3.12, Hivemind 1.1.12, relay enabled, and forced public reachability. The Hivemind debug launch line should contain `-relay=1`, `-forceReachabilityPublic=1`, the public announce address, and the persistent identity path. The bundled Hivemind 1.1.12 daemon enables Circuit Relay v2 service by default when relay support is enabled; the deprecated `use_relay_hop` option must not be added.

7. Copy the printed public multiaddress. It should have this shape:

```text
/ip4/<VPS_PUBLIC_IP>/tcp/7001/p2p/<VPS_PEER_ID>
```

8. Configure participant machines with that same address as both bootstrap and trusted relay:

```text
DISTRIBLLM_INITIAL_PEERS=/ip4/<VPS_PUBLIC_IP>/tcp/7001/p2p/<VPS_PEER_ID>
DISTRIBLLM_TRUSTED_RELAYS=/ip4/<VPS_PUBLIC_IP>/tcp/7001/p2p/<VPS_PEER_ID>
DISTRIBLLM_NETWORK_MODE=auto
DISTRIBLLM_P2P_PORT=0
DISTRIBLLM_ANNOUNCE_MADDRS=
DISTRIBLLM_AUTO_RELAY=true
```

9. From each participant's existing backend directory, sync the same locked environment and run the minimal forced-relay probe before starting FastAPI or loading a model:

```bash
git rev-parse --short HEAD
uv sync --frozen --python 3.12
HIVEMIND_LOGLEVEL=DEBUG \
GOLOG_LOG_LEVEL=autorelay=debug,relay=debug \
uv run --python 3.12 python -m relay_probe --timeout 90 --json
```

If the prompt already ends in `.../backend`, do not run `cd backend` again. The participant debug launch line must contain `-autoRelay=1`, `-trustedRelays=...`, `-relayDiscovery=0`, `-dhtClient=1`, and `-forceReachabilityPrivate=1`. The `relayDiscovery=0` compatibility setting is required for the current single trusted VPS: the bundled dynamic discovery path otherwise waits up to three minutes for four candidates, longer than the original 60-second deadline. A successful result contains the same Python/Hivemind versions and at least one complete address shaped like `/ip4/<VPS_PUBLIC_IP>/tcp/7001/p2p/<VPS_PEER_ID>/p2p-circuit/p2p/<WORKER_PEER_ID>`.

The JSON must also include `python_version`, `hivemind_version`, `force_reachability`, and `relay_discovery: false`. Their absence proves that the participant is still running the older probe implementation.

10. Only after the manual probe succeeds, install the checked-in `systemd` service and run its validator. The identity file must remain persistent across restarts; deleting it changes the peer ID and invalidates existing configured addresses.

```bash
sudo /opt/distribllm/deploy/vps/install-bootstrap-service.sh /opt/distribllm
sudo /opt/distribllm/deploy/vps/validate-bootstrap-service.sh --restart-test
```

The restart validator checks service state, runtime versions, deployment commit, public multiaddress, effective relay flags, and identity continuity. Run the participant relay probe again after the restart to prove that the local service evidence corresponds to a usable external reservation.

Minimum VPS validation before testing inference:

- The process starts and prints the expected public multiaddress.
- The VPS and participant report the expected commit, Python 3.12, and Hivemind 1.1.12.
- The VPS port is reachable from a participant machine.
- A participant node in `auto` mode joins the DHT through the VPS address.
- A NAT/WSL-separated participant in relay mode obtains a visible `/p2p-circuit/` address.

### Recommended Test Order

Relay is a fallback in `auto` mode, not the preferred path. A worker first checks direct reachability with relay disabled. If another peer can dial it directly, the worker uses direct mode. If direct dialing fails, the worker uses the trusted VPS relay.

For implementation validation, use this order:

1. Deploy the VPS relay-capable bootstrap first. This gives every test a stable discovery point and a fallback path.
2. Run the two-device test in `auto` mode. This proves the production default works even when WSL/NAT blocks inbound direct dialing.
3. Run a separate forced direct LAN test with `DISTRIBLLM_NETWORK_MODE=direct`, fixed `DISTRIBLLM_P2P_PORT`, and `DISTRIBLLM_ANNOUNCE_MADDRS` set to each Windows LAN address.

Testing direct mode first is optional. It is useful for measuring the faster path and proving the Windows/WSL port configuration, but it should not block the relay test. The production requirement is that ordinary participants can complete inference in `auto` mode without manually opening router ports.

### Two-Device Inference Validation

Use a small open model first, such as `facebook/opt-125m`, because this test is about transport reachability rather than model quality.

1. Start the updated VPS relay-capable bootstrap.
2. Configure both Windows/WSL devices with the VPS address as `DISTRIBLLM_INITIAL_PEERS` and `DISTRIBLLM_TRUSTED_RELAYS`.
3. Set both devices to `DISTRIBLLM_NETWORK_MODE=auto`.
4. Start the backend on both devices.
5. On Device A, serve layers `0-6`.
6. On Device B, serve layers `6-12`.
7. Confirm Monitoring shows both peers, their layer ranges, and their transport mode.
8. Start the generator on one device.
9. Confirm route readiness passes only after the selected expert RPC metadata probes pass.
10. Send a short prompt and confirm generated output returns.

The test passes only when all of these are true:

- DHT discovery finds both serving peers.
- Layer coverage is complete and contiguous.
- Every selected expert RPC probe succeeds.
- Inference returns generated output.
- Monitoring reports whether each selected peer is direct or relayed.

If discovery works but the expert RPC probe fails, the issue is still data-plane reachability, not model discovery.

### Implemented Worker Behavior

At startup, a worker should:

1. Load project bootstrap and trusted relay addresses.
2. Run an isolated direct-reachability probe with relay disabled.
3. If directly reachable, start in server mode with verified direct addresses.
4. If not directly reachable, start in client mode with automatic relay and trusted relays enabled.
5. Obtain the final visible direct or circuit multiaddresses.
6. Start the expert RPC handler.
7. Announce model layers with transport state.
8. Require the generator's independent expert RPC metadata probe before route readiness.

The implementation follows this configuration shape:

```python
dht = hivemind.DHT(
    host_maddrs=[f"/ip4/0.0.0.0/tcp/{serving_port}"],
    announce_maddrs=verified_announce_maddrs or None,
    initial_peers=bootstrap_peers,
    start=True,
    use_ipfs=False,
    auto_nat=True,
    nat_port_map=True,
    use_relay=True,
    use_auto_relay=not directly_reachable,
    trusted_relays=trusted_relay_maddrs,
    client_mode=not directly_reachable,
)
```

Configuration selection and lifecycle behavior have automated coverage. Relay reservation and expert RPC behavior still require a live VPS/two-device test against Hivemind `1.1.12`.

## Implemented Configuration

Available environment settings:

```text
DISTRIBLLM_NETWORK_MODE=auto
DISTRIBLLM_P2P_PORT=7100
DISTRIBLLM_ANNOUNCE_MADDRS=
DISTRIBLLM_TRUSTED_RELAYS=/ip4/<relay-ip>/tcp/<port>/p2p/<relay-peer-id>
DISTRIBLLM_AUTO_NAT=true
DISTRIBLLM_AUTO_RELAY=true
DISTRIBLLM_NAT_PORT_MAP=true
DISTRIBLLM_RELAY_WAIT_TIMEOUT=60
```

Rules:

- A direct public announce address must include a fixed non-zero port.
- Private and loopback addresses must not count as public reachability evidence.
- A manual announce address must pass validation before the node becomes online.
- The node must report direct, relay, checking, or unreachable state.
- Identities and administrative credentials must not reach frontend responses or version control.

## Direct Reachability on Windows and WSL

The original direct connection failed because the advertised `172.x` address belonged to WSL's private virtual network. It was valid inside one Windows host, but the other physical device had no route into that private network. A private address can be used directly only when both peers share a route to it. Otherwise, Windows must expose the WSL socket through a reachable Windows address.

### Recommended LAN Setup: WSL Mirrored Networking

For Windows 11 with mirrored networking support:

1. Choose a fixed P2P port, such as `7100`.
2. Add the following to `%UserProfile%\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
```

3. Run `wsl --shutdown` in PowerShell and restart the DistribLLM WSL environment.
4. Permit inbound TCP `7100` for WSL in the Windows/Hyper-V firewall. On systems with the Hyper-V firewall cmdlets, run this in administrator PowerShell:

```powershell
New-NetFirewallHyperVRule -Name "DistribLLM-P2P" -DisplayName "DistribLLM P2P" -Direction Inbound -VMCreatorId "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}" -Protocol TCP -LocalPorts 7100
```

5. Configure DistribLLM inside WSL:

```text
DISTRIBLLM_NETWORK_MODE=direct
DISTRIBLLM_P2P_PORT=7100
DISTRIBLLM_ANNOUNCE_MADDRS=/ip4/<WINDOWS-LAN-IP>/tcp/7100
```

6. From the other Windows device, verify the socket before starting inference:

```powershell
Test-NetConnection <WINDOWS-LAN-IP> -Port 7100
```

Both machines require their own fixed listening port and local firewall permission if both serve layers. They do not require router port forwarding while they are on the same LAN.

### Fallback LAN Setup: Default WSL NAT

If mirrored networking is unavailable, forward the Windows host's port to the current WSL address:

```powershell
wsl hostname -I
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=7100 connectaddress=<WSL-IP> connectport=7100
New-NetFirewallRule -DisplayName "DistribLLM P2P 7100" -Direction Inbound -Protocol TCP -LocalPort 7100 -Action Allow
```

Then announce the Windows LAN address, not the WSL `172.x` address:

```text
DISTRIBLLM_NETWORK_MODE=direct
DISTRIBLLM_P2P_PORT=7100
DISTRIBLLM_ANNOUNCE_MADDRS=/ip4/<WINDOWS-LAN-IP>/tcp/7100
```

The WSL address can change after restart, so the `portproxy` target may need updating. This makes NAT mode more fragile than mirrored networking.

For direct connectivity across different Internet connections, the router must also forward the chosen public TCP port to the Windows host unless automatic NAT mapping succeeds. The announce address must then use the public IP. This is an optional performance path, not the normal end-user requirement.

### Production Default: Automatic Relay Fallback

Ordinary Windows desktop users should use:

```text
DISTRIBLLM_NETWORK_MODE=auto
DISTRIBLLM_P2P_PORT=0
DISTRIBLLM_ANNOUNCE_MADDRS=
DISTRIBLLM_TRUSTED_RELAYS=/ip4/<vps-ip>/tcp/<relay-port>/p2p/<relay-peer-id>
```

The WSL backend first tests direct reachability. If the private WSL address cannot be reached, it creates an outbound relay reservation. Outbound connectivity works through most WSL, firewall, and router NAT configurations, so users do not need to expose a public port.

### Windows Desktop Packaging

The Electron interface can run as a Windows desktop application while the dependency-sensitive backend remains in WSL 2. The installer must treat WSL as a managed runtime: verify or install WSL 2, import or provision the Linux environment, launch the backend through `wsl.exe`, and manage upgrades and GPU prerequisites. The Electron installer alone does not convert the Linux Python/Hivemind stack into a native Windows backend.

This packaging choice does not block relay mode. The WSL backend makes the outbound connection to the VPS relay. Direct LAN mode additionally needs mirrored networking or Windows-to-WSL port forwarding as described above.

## Metadata and Monitoring

Current node metadata includes:

```json
{
  "connection_mode": "relay",
  "direct_reachability": false,
  "transport_verified": true
}
```

Monitoring currently displays the connection mode and flags unverified transport. Future monitoring should add:

- Direct, relay, checking, or unreachable state.
- Enough of the peer ID to distinguish peers.
- Relay identity where applicable.
- Last successful RPC probe.
- Per-hop round-trip latency and failed-probe count.
- Route connection mix, such as two direct hops and one relay hop.

The Nodes page should remain limited to locally managed lifecycle controls. DHT-discovered remote peers belong on Monitoring.

## Route Readiness and Selection

DHT metadata coverage alone must never make the generator ready. A route is ready only if:

- Metadata is valid and model-compatible.
- Layer ranges form one exact contiguous chain.
- Every peer resolves to a direct or circuit-relay address.
- Every selected expert answers an RPC metadata probe.
- Probe results are recent enough to remain trustworthy.

Later routing should prefer healthy direct peers, then healthy relayed peers, and retain alternate providers for failover. Relay use must be visible because it affects latency, throughput, and VPS bandwidth cost.

## Security Requirements

libp2p transport identity and TLS do not make arbitrary public compute requests safe. Production also requires:

- Signed and validated DHT records.
- Stable peer identities and secure identity-file storage.
- Model, architecture, dtype, and layer-range validation.
- Tensor shape, sequence-length, and payload-size limits.
- RPC timeouts and bounded concurrency.
- Request authentication or signed inference-session capabilities.
- Rate limits and per-peer quotas.
- Relay connection, duration, and bandwidth limits.
- Resource isolation for serving processes.
- Health and reputation tracking.
- Protection against malicious metadata and false layer claims.
- Privacy-aware logging without credentials or raw secrets.

The public FastAPI control API must not be exposed as part of the P2P transport solution.

## Availability and Capacity

One VPS is acceptable for development or an initial production-capable deployment, but it remains a single point of failure. Mature production should use:

- At least two or three stable bootstrap peers.
- Multiple trusted relays in different failure domains or regions.
- Backed-up persistent peer identities.
- Worker configuration containing multiple bootstrap and relay addresses.
- Health-based removal of unavailable infrastructure addresses.
- Relay connection and bandwidth monitoring.
- Capacity planning based on activation payload size, tokens per second, and concurrent sessions.

Circuit relay traffic is end-to-end encrypted, but the relay still pays the bandwidth and latency cost of forwarding it. Direct routes should therefore be preferred whenever safely reachable.

## Implementation Plan

### Phase 1: Focused Transport Prototype

- [x] Add typed settings for serving port, announce addresses, relay behavior, and trusted relays.
- [x] Pass those settings into serving-node DHT construction.
- [x] Add unit tests for default, direct, and relay configurations.
- [ ] Run one VPS as an explicitly configured bootstrap and relay.
- [ ] Confirm a worker advertises a circuit address when direct reachability fails.
- [ ] Confirm a remote generator invokes expert metadata through that circuit.

### Phase 2: Petals-Style Reachability Lifecycle

- [x] Add a direct reachability probe with relay disabled.
- [x] Select direct or relay mode from the result.
- [x] Require an independent generator-side probe of the real expert RPC before route readiness.
- [x] Keep route readiness false until validation succeeds.
- [ ] Periodically revalidate reachability and publish offline state after repeated failure.
- [x] Expose connection mode and validation state through APIs and Monitoring.

### Phase 3: Routing and Performance

- Record direct-versus-relay latency per hop.
- Prefer direct routes while retaining relay coverage.
- Add alternate-route failover.
- Measure relay bandwidth per request and generated token.
- Add distributed session and key/value cache support to reduce repeated activation traffic.

### Phase 4: Production Hardening

- Deploy redundant bootstrap and relay nodes.
- Add relay quotas, limits, alerting, and operational dashboards.
- Add signed session authorization, abuse protection, and peer reputation.
- Perform load, failure, restart, malicious-input, and infrastructure-failover testing.
- Document deployment and incident-response procedures.

## Validation Matrix

| Scenario | Expected connection | Required result |
| --- | --- | --- |
| Same process, separate peer identities | Direct local | Expert RPC succeeds |
| Same LAN with reachable networking | Direct LAN | Expert RPC succeeds without relay |
| Separate WSL NAT networks | Relay unless forwarding is configured | Circuit address is advertised and RPC succeeds |
| Separate home or mobile networks | Direct if traversal works, otherwise relay | RPC succeeds and mode is accurate |
| Direct port blocked after startup | Relay or unavailable | Route does not remain falsely ready |
| Relay unavailable | Direct, alternate relay, or unavailable | Clear failover or actionable state |
| Malformed transport metadata | Rejected | Peer is not selected |
| Discovered peer with failed RPC probe | Unreachable | Route readiness remains false |

## Acceptance Criteria

The transport design is implemented when:

- Two devices complete inference across different external networks without Tailscale.
- A NAT-separated WSL worker becomes reachable through a project-owned relay.
- A publicly reachable worker uses a direct path rather than unnecessary relay.
- Monitoring accurately identifies direct and relayed peers.
- Route readiness depends on successful expert RPC probes.
- Relay loss does not leave stale routes marked ready.
- Bootstrap and relay roles are documented and observable.
- Public control APIs remain unexposed.
- Automated tests cover configuration and direct/relay transitions.
- Live tests record latency and bandwidth for direct and relayed inference.

## Decisions for Review

1. Use Petals-style automatic direct-versus-relay selection as the production strategy.
2. Configure the existing VPS as bootstrap and trusted relay for the first deployment.
3. Keep Tailscale as an optional development tool, not a production dependency.
4. Add fixed-port and announce-address support for direct operators.
5. Require independent expert RPC validation before announcing a server online.
6. Expose connection mode and relay use in Monitoring and route scoring.
7. Add redundant bootstrap and relay infrastructure after the first successful relay test.

## Recommended Decision

Adopt the Petals-derived hybrid design:

> Prefer verified direct peer-to-peer RPC, automatically fall back to a trusted project-owned libp2p circuit relay for NAT-separated peers, and require an external expert RPC probe before a server or route is considered ready.

This preserves DistribLLM's project-owned public/discoverable swarm, avoids requiring router configuration in the common case, does not require a private overlay network, and keeps model computation on participant devices.
