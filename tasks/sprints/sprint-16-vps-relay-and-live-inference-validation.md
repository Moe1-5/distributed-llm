# Sprint 16 - VPS Relay and Live Inference Validation

**Goal:** Make the project-owned VPS provide a verified Hivemind circuit relay, then prove expert RPC and distributed inference between two Windows/WSL participant devices.
**Start:** 2026-08-12
**End:** TBD

---

## Problem Summary

The Sprint 14 implementation could detect that a WSL worker was not directly reachable and select relay mode, but the first public test did not receive a visible `/p2p-circuit/` address within 60 seconds. Investigation found that Hivemind's bundled daemon was left on the multi-candidate relay-discovery path despite having one explicit trusted relay, and project polling reused cached startup addresses.

The participant now selects the configured trusted relay statically and requests fresh visible addresses. A public Windows/WSL probe obtained a complete circuit address through the project VPS in 1.63 seconds. Relay reservation is proven; independent expert RPC and two-device inference remain to be validated.

## Confirmed Live Evidence

- The VPS firewall permits inbound TCP port `7001`.
- The VPS loads its persistent identity from `/var/lib/distribllm/bootstrap.id`.
- The VPS advertises `/ip4/178.156.212.0/tcp/7001/p2p/QmTXjKiMggt92DP4CLbDwMCfLd4L1aNKyBnja5apT2ZZL2`.
- A Windows participant previously confirmed TCP connectivity to `178.156.212.0:7001`.
- Participant `.env` configuration uses the same VPS address for `DISTRIBLLM_INITIAL_PEERS` and `DISTRIBLLM_TRUSTED_RELAYS`, with `DISTRIBLLM_NETWORK_MODE=auto` and automatic relay enabled.
- The participant's direct-reachability decision selected relay mode.
- The original participant attempt exposed no `/p2p-circuit/` address within 60 seconds and stopped before model loading.
- After the relay compatibility fix, a Windows/WSL probe using Python `3.12.3` and Hivemind `1.1.12` obtained a complete circuit address in `1.633` seconds.
- Participant debug output confirms `-autoRelay=1`, `-dhtClient=1`, `-forceReachabilityPrivate=1`, `-relayDiscovery=0`, and the VPS as both bootstrap peer and trusted relay.
- AutoRelay logs confirm that the VPS supports the relay protocol and that the participant added it as a relay.

## Product Decision

- Keep `auto` mode: verified direct connectivity remains preferred and the VPS relay remains the fallback.
- Treat a reachable bootstrap port and an active DHT process as necessary but insufficient relay evidence.
- Do not mark relay transport as verified until the worker exposes a circuit address and a second peer successfully dials its real expert RPC through that route.
- Keep model loading after transport establishment so a failed relay reservation does not waste memory or download time.
- Keep the stable VPS identity file and peer ID unchanged while diagnosing the relay.

## In Progress

- [x] Deploy and start the updated public bootstrap process on the VPS.
- [x] Verify the VPS firewall and participant-to-VPS TCP reachability.
- [x] Capture the first live AutoRelay reservation failure.
- [x] Add a minimal participant-side relay reservation probe.
- [x] Determine why Hivemind `1.1.12` does not expose a circuit address with the current VPS and participant arguments.

## Todo

- [ ] Capture full VPS and participant `p2pd` logs at debug level during one relay attempt.
- [ ] Verify the VPS process is running the expected branch/commit and Hivemind version.
- [ ] Inspect the effective VPS `p2pd` flags and confirm that relay service/hop reservations are enabled, not only relay dialing.
- [x] Inspect the effective participant `p2pd` flags for AutoRelay, trusted relays, reachability state, DHT client mode, and bootstrap peers.
- [x] Build a minimal Hivemind-only reservation probe that excludes FastAPI, model loading, and expert routing.
- [x] Confirm the worker receives a complete relay multiaddress containing `/p2p-circuit/p2p/<worker-peer-id>`.
- [x] Add or correct relay startup configuration based on the minimal probe result.
- [x] Add regression coverage for the participant relay-probe configuration and timeout path.
- [x] Run an independent expert metadata RPC through the relay address.
- [ ] Run two-device OPT-125M inference with non-overlapping `0-6` and `6-12` layer slices.
- [ ] Record connection mode, route trace, RPC latency, generation result, and relevant logs.
- [x] Convert the verified VPS bootstrap/relay command into a persistent `systemd` service.
- [x] Update the operator runbook with the exact deployment, validation, recovery, and rollback commands.

## Test Order

1. Keep the VPS bootstrap/relay process running with its existing identity.
2. Prove a minimal participant receives a circuit address.
3. Prove a second independent peer can dial the participant through that circuit.
4. Prove the real DistribLLM expert metadata RPC works through the circuit.
5. Start the two model-serving devices and prove complete route discovery.
6. Run distributed inference and record timing and transport evidence.
7. Test direct LAN mode separately after the relay fallback is proven.

## Acceptance Criteria

- [x] A NAT-separated WSL worker obtains a complete `/p2p-circuit/` address through the project VPS.
- [x] The relay address appears before the configured startup deadline without manual participant router forwarding.
- [x] A second peer can invoke the worker's expert metadata RPC through the relay.
- [ ] Two Windows/WSL devices complete OPT-125M distributed inference across the VPS relay fallback.
- [ ] Monitoring and node metadata report relay mode accurately.
- [ ] Directly reachable workers still select direct mode in `auto` mode.
- [ ] Restarting the persistent VPS service preserves the configured peer ID and restores relay operation.
- [x] Automated tests cover the live-discovered configuration or lifecycle defect.
- [x] Documentation contains the deployment, validation, and recovery procedure, with live-only evidence clearly identified.

---

## Session Log

### 2026-08-12 - Record VPS bootstrap deployment and first relay reservation failure

- What changed: created Sprint 16; recorded the VPS firewall rule, stable bootstrap identity and public multiaddress, participant relay configuration, successful public TCP reachability, and the worker's 60-second failure to obtain a `/p2p-circuit/` address; updated the network review from deployment-pending to active live diagnosis.
- Why: the first real participant test proved that bootstrap reachability does not by itself prove circuit-relay reservation capability, so relay deployment and two-device inference need a focused evidence-driven validation sprint.
- Status: the public bootstrap is running and reachable, but the relay is not yet working or tested for inference; the next step is a minimal Hivemind reservation probe with VPS and participant debug logs.

### 2026-08-12 - Add minimal relay reservation probe

- What changed: added `backend/relay_probe.py`, documented `python -m relay_probe --json` in debugging guidance, added node startup logs for effective DHT relay arguments and visible addresses during reservation wait, and added backend regression tests for relay probe DHT arguments, success, timeout, and missing-peer handling.
- Why: the live failure needs a cheap Hivemind-only diagnostic that can prove or disprove circuit reservation before FastAPI startup, model loading, expert RPC, or two-device inference are involved.
- Status: participant-side relay diagnostics are available and covered by tests; the VPS `p2pd` debug logs, relay service flags, successful circuit address, expert RPC, and two-device inference validation remain open.

### 2026-08-12 - Reproduce reservation timeout and tighten relay intent

- What changed: reproduced the no-circuit result against an isolated Hivemind 1.1.12 bootstrap, inspected the bundled `p2pd` arguments and upstream daemon source, confirmed Circuit Relay v2 service defaults on when `use_relay=True`, forced public reachability for announced bootstrap peers, forced private reachability for relay participants, and added Python/Hivemind runtime details to bootstrap and probe output.
- Why: the original participant reached the VPS but did not reserve a circuit. An activated virtual environment is not required when using `uv run`, but running plain `python` from another environment can silently use a different Hivemind binary; explicit reachability and runtime evidence remove both ambiguities.
- Status: the source-side configuration gap is corrected and covered by focused tests. A fresh public-VPS probe with matching commit, Python 3.12, Hivemind 1.1.12, and debug logs is still required before expert RPC or inference testing.

### 2026-08-12 - Fix trusted-relay selection and stale address polling

- What changed: traced Hivemind 1.1.12 to its bundled go-libp2p-daemon and go-libp2p 0.32.1 sources; reproduced that discovery mode recognizes the trusted VPS but waits up to three minutes for four candidates, while static-relay mode immediately reserves with the single configured relay; added a compatibility shim that launches p2pd with relay discovery disabled for trusted relays, and changed node/probe polling to request fresh daemon addresses.
- Why: the 60-second timeout had two independent causes in project code: one trusted relay was incorrectly left on the multi-candidate discovery path, and repeated address checks returned Hivemind's cached startup addresses instead of asking p2pd for addresses added after reservation.
- Status: an isolated Hivemind test now reaches the relay reservation request and the regression suite covers static selection plus refreshed polling. The public VPS probe is the remaining proof because libp2p intentionally does not advertise a circuit address through a loopback-only local relay.

### 2026-08-12 - Remove obsolete machine-local bootstrap fallback

- What changed: removed the old hardcoded loopback and WSL bootstrap addresses from `backend/constants.py` and corrected `backend/bootstrap.py` to direct operators to the environment-based bootstrap and trusted-relay settings.
- Why: the project already has a public VPS and deployment-specific addresses must not silently fall back to an obsolete developer machine when `.env` is missing or incomplete.
- Status: local and production participants now use the same explicit environment contract; the relay probe returns a clear missing-peer error when no bootstrap address is configured.

### 2026-08-12 - Prove public VPS circuit reservation

- What changed: ran the minimal probe from Windows/WSL against the project VPS using Python 3.12.3 and Hivemind 1.1.12; it selected the configured trusted relay statically, confirmed relay protocol support, and returned a complete `/p2p-circuit/p2p/<worker-peer-id>` address in 1.633 seconds.
- Why: TCP reachability and local daemon tests could not prove that the deployed public VPS would accept a real participant reservation. This result verifies the corrected trusted-relay selection and refreshed-address polling against the production relay.
- Status: public relay reservation is verified within the startup deadline. Independent expert metadata RPC, two-device OPT-125M inference, persistent VPS service validation, and the final operator runbook remain open.

### 2026-08-12 - Prove expert metadata RPC through the public circuit

- What changed: started a full-layer cached OPT-125M expert in forced relay mode, then started a second independent Hivemind peer whose only initial address was the expert's public circuit multiaddress; `RemoteSequential.validate_reachable_route()` discovered the complete `0-12` route and completed the real `expert.info` RPC in 10.116 seconds total.
- Why: a circuit address proves reservation but not that the relay can carry DistribLLM expert traffic. The independent caller exercised the existing production readiness RPC instead of relying on DHT metadata alone.
- Status: same-host independent-peer expert metadata RPC through the public VPS is verified, and both temporary peers shut down with no remaining p2pd processes. Hivemind emitted harmless late destructor warnings after successful shutdown. The backend suite passes 114 tests plus 19 subtests, changed Python files compile, and `git diff --check` is clean. Tensor forwarding, two-device distributed inference, persistent service restart, and final operator validation remain open.

### 2026-08-12 - Add persistent VPS relay service and validation tooling

- What changed: added a hardened `systemd` unit, root installer, validated launcher, environment template, atomic non-secret runtime status, automated service and restart validator, and a dedicated operations runbook covering installation, upgrades, recovery, rollback, backup, and external probing.
- Why: the relay command had been proven manually, but a production-style bootstrap needs reproducible deployment evidence, stable identity continuity, automatic restart, and an operator-safe way to detect a wrong commit, runtime, address, or relay configuration.
- Status: service implementation is complete on the dedicated feature branch. Six focused relay/service tests and the full backend suite of 126 tests plus 19 subtests pass; changed Python files compile, all deployment scripts pass `bash -n`, the generated unit passes `systemd-analyze verify`, and `git diff --check` is clean. Live installation on the project VPS, restart continuity there, a fresh external probe after restart, monitoring-mode accuracy, direct-mode validation, and two-device OPT-125M inference remain open user-device acceptance gates.
