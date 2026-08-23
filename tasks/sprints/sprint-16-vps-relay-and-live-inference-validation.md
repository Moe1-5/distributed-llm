# Sprint 16 - VPS Relay and Live Inference Validation

**Goal:** Make the project-owned VPS provide a verified Hivemind circuit relay, then prove expert RPC and distributed inference between two Windows/WSL participant devices.
**Start:** 2026-08-12
**End:** TBD

---

## Problem Summary

The Sprint 14 implementation could detect that a WSL worker was not directly reachable and select relay mode, but the first public test did not receive a visible `/p2p-circuit/` address within 60 seconds. Investigation found that Hivemind's bundled daemon was left on the multi-candidate relay-discovery path despite having one explicit trusted relay, and project polling reused cached startup addresses.

The participant now selects the configured trusted relay statically and requests fresh visible addresses. A public Windows/WSL probe obtained a complete circuit address through the project VPS in 1.63 seconds, and an independent peer completed the real expert metadata RPC through that circuit. Relay reservation and metadata traffic are proven; tensor inference across two physical devices remains to be validated.

## Confirmed Live Evidence

- The VPS firewall permits inbound TCP port `7001`.
- The VPS loads its persistent identity from `/var/lib/distribllm/bootstrap.id`.
- The VPS advertises `/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y`; the older configured `QmTXjKi...` expectation was disproved by the preserved identity-file hash and repeated service restarts on 2026-08-23.
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
- [x] Verify the VPS process is running the expected branch/commit and Hivemind version.
- [x] Inspect the effective VPS `p2pd` flags and confirm that relay service/hop reservations are enabled, not only relay dialing.
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
- [x] Restarting the persistent VPS service preserves the configured peer ID and restores relay operation.
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

### 2026-08-13 - Make two-device relay evidence reproducible

- What changed: added a command-line capture and validator that combines two participant API snapshots, proves distinct local owners for the executed adjacent route, checks relay or direct transport, and records per-hop timing plus deterministic generation.
- Why: the final live relay test needs one reviewable result with explicit failure reasons rather than manually correlating screenshots and logs from both devices.
- Status: automated evidence behavior passes in the full backend suite. The actual Windows/WSL two-device relay run, direct-mode run, and live persistent-service restart check remain open and no live acceptance checkbox changed.

### 2026-08-13 - Bind final live acceptance artifacts

- What changed: made VPS restart validation emit machine-readable JSON only after relay flags and identity-file continuity pass; added relay-probe timestamps and SHA-256 binding to that exact VPS report; added a private cross-sprint manifest that checks two packaged Windows lifecycles, VPS restart, relay reservation, relay inference, direct inference, application/runtime versions, participant labels, routes, and shadow incentives as one compatible set.
- Why: independent passing files could previously be mixed across app versions, VPS restarts, or participant runs, and the VPS validator emitted its JSON before its final shell checks. Final acceptance needs one internally consistent evidence set without treating automation as proof of separate physical machines or reviewer approval.
- Status: the full backend suite passes 184 tests plus 22 subtests; Python compilation, deployment-script syntax, rendered systemd unit validation, CLI loading, and diff checks also pass. Live VPS restart, bound external probe, two-device relay/direct inference, visual monitoring review, physical-device attestation, and explicit sprint closure approval remain open; no live acceptance checkbox changed.

### 2026-08-13 - Document manual VPS bootstrap launch

- What changed: expanded the VPS relay operations runbook with the exact foreground bootstrap command, flag-by-flag explanation, expected startup evidence, participant multiaddress, and pre-launch port/process checks; updated the root and docs indexes for the expanded runbook scope.
- Why: Sprint 14 and Sprint 16 live testing needs an operator-safe command reference for starting the relay-capable bootstrap without relying on chat history.
- Status: documentation is updated. No runtime behavior changed, and the two-device relay/direct inference gates remain open.

### 2026-08-16 - Document bootstrap identity permission recovery

- What changed: expanded the VPS relay operations runbook with the one-time `/var/lib/distribllm` ownership setup needed when launching the relay manually as the SSH user, plus guidance to prefer the installer for the managed service path.
- Why: the manual foreground command failed on the VPS with `PermissionError: [Errno 13] Permission denied: '/var/lib/distribllm/bootstrap.id'` before Hivemind could create or load the stable relay identity.
- Status: documentation now covers the observed permission failure. The operator still needs to start either the foreground process or the managed service and then rerun relay and two-device acceptance checks.

### 2026-08-16 - Clarify VPS versus Windows shell context

- What changed: added an explicit warning to the VPS relay operations runbook that bootstrap and identity-permission commands must run inside the VPS SSH shell, not local Windows PowerShell; added a matching active lesson for future remote-command guidance.
- Why: after the SSH connection aborted, the recovery commands were pasted into Windows PowerShell, where `sudo`, `chmod`, `$USER:$USER`, and Linux paths do not apply to the VPS.
- Status: docs now identify the expected VPS prompt before the command. Runtime behavior did not change, and live bootstrap restart plus two-device relay acceptance remain open.

### 2026-08-16 - Document bootstrap port-in-use recovery

- What changed: expanded the VPS relay operations runbook with an explicit `bind: address already in use` recovery path, including how to inspect port `7001`, distinguish the managed service from an unmanaged foreground process, validate an already-running service, and stop only the intended process before relaunching.
- Why: the manual bootstrap now loads the stable identity successfully, but Hivemind failed to bind because another process already occupied port `7001`.
- Status: documentation now covers the observed port ownership failure. The next operator step is to identify the process that owns port `7001`, then either use the existing managed service or stop it before running the foreground command.

### 2026-08-23 - Correct stale expected relay peer identity

- What changed: upgraded the live combined VPS service to commit `3e4ad448edb5dbb546389164c06315bd3f57b27f`, captured a restart-validation failure, and compared the configured expected peer with the persistent identity file and service journal. The source default, packaged Windows launcher, active runbooks, and a literal regression assertion now use peer `QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y`.
- Why: `/etc/distribllm/bootstrap.env` expected `QmTXjKi...`, but both managed-service restarts deterministically loaded `QmczTupu...`. The identity-file SHA-256 was `34629a9d7ec3ede4a7b12eb3f49537f172cf6f597bed0425dbd7986007a9eab3` before and after restart, proving stale configuration rather than identity rotation. Changing `12D3KooW...` journal peers are reachability-check clients, not the bootstrap relay.
- Status: the root cause is corrected in commit `b66755fa7e7c7f31fc28af59863517ddecdb87bd`. The focused launcher/runtime/type checks and 12 bootstrap-service tests pass. The exact-commit Windows portable package passes the strict audit with 38 ASAR entries, 65 checksummed backend runtime files, no forbidden entries, manifest SHA-256 `ace61775041c876a967a6e63980c59f72c203055aec2d81ca382552c488cab70`, and acceptance identity bound. Its SHA-256 is `1aab5dc6c57b97adb1fad5cb4b3b7bc94166c6e687577d117c0406b27ac6125e` and its size is `87918472` bytes. The operator must deploy the resulting commit, explicitly reconcile the preserved `/etc/distribllm/bootstrap.env` expected-peer value, rerun restart validation, and then continue the external relay and two-device inference gates.

### 2026-08-23 - Prove managed VPS restart continuity

- What changed: deployed exact runtime commit `b66755fa7e7c7f31fc28af59863517ddecdb87bd` to the single project VPS, reconciled the expected peer to `QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y`, and ran the destructive-to-process restart validator. The report returned `ok: true` with no errors, valid runtime status, observed effective relay flags, and a preserved identity hash.
- Why: the earlier failed report had proved a stale expected-peer setting but could not satisfy the persistent-service acceptance gate. The corrected run proves the combined bootstrap, DHT storage, and circuit-relay service restart on the one available VPS without changing its private identity.
- Status: live VPS restart continuity now passes on Python `3.12.13` and Hivemind `1.1.12`; the service advertises `/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y`, with relay enabled and forced public reachability. The next gate is an external Device 1 relay probe bound to this exact report, followed by the two-device packaged-runtime tests.

### 2026-08-23 - Revalidate VPS against the isolated packaged runtime

- What changed: deployed and restart-validated exact commit `b468185d87cb2884497b177e84f761478406b9af` after the packaged Windows launcher was corrected to use a separate mutable virtual environment. The new report returned `ok: true`, no errors, observed relay flags, valid runtime status, and preserved identity hash.
- Why: cross-sprint acceptance rejects a VPS report whose deployment commit differs from the reviewed packaged Windows runtime. The earlier valid VPS report for `b66755f...` could not be bound to the replacement package.
- Status: the one project VPS now provides acceptance-compatible evidence on the same runtime commit as the Device 1 package, with Hivemind `1.1.12`, relay enabled, forced public reachability, and stable peer `QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y`. The external bound relay probe is next.

### 2026-08-23 - Prove external Device 1 circuit reservation against the validated VPS

- What changed: copied the exact `b468185...` VPS restart report to Device 1 and ran the packaged `relay_probe` through its isolated Python `3.12.3` environment with forced relay mode, the corrected bootstrap/trusted-relay multiaddress, and validation-context binding.
- Why: service-local restart success is insufficient until a real external participant reserves a circuit through that exact restarted relay. The bound report prevents mixing a probe with an older VPS state.
- Status: passed. Device 1 obtained `/ip4/178.156.212.0/tcp/7001/p2p/QmczTupuZhH2WfL7H1P1vHZnicjaEFPfBCPpN5hoZVUS1y/p2p-circuit/p2p/12D3KooWJPAiJqqjHQP7ooMGWZfkVDNb5ebEJoW4fPGFLetgnpsL` in `2.507` seconds using Hivemind `1.1.12`; the result has `ok: true`, static trusted-relay selection, private reachability, and validation-context SHA-256 `16cb2b7f7a0c85c14388d82717eea3ddaad66292ae4e86588f399100760a0863`. The next gate is adjacent `0-6` and `6-12` package-hosted workers across the two devices.

### 2026-08-23 - Start the Device 1 lower split worker

- What changed: started the packaged Device 1 OPT-125M worker with the manual adjacent range `0-6` on CUDA.
- Why: the split test must use explicit disjoint ranges rather than Recommended placement, whose discovery snapshot can still lag physical node publication.
- Status: passed. Peer `QmUZTRtPJHGubgrR82F5sJRym96EzD2Z6euwNB4XXdXXRJ` serves `0-6`, has layers loaded, an active RPC server, fresh Hivemind publication, verified relay transport, and a visible circuit multiaddress. The node has accepted no forwards yet. The next gate is Device 2 serving the complementary `6-12` range.

### 2026-08-23 - Establish the adjacent two-device relay split

- What changed: started complementary CUDA workers on the two physical devices: Device 1 peer `QmV1JC6v2C3vUwbYBK8anRpRoor57sWj5abubatyw6BSuZ` serves `0-6`, and Device 2 peer `QmUUHDX97bymRja9NtMQxdpK2iZ393GoxgSrFJSSmYmm5p` serves `6-12`.
- Why: this is the controlled topology required to distinguish split-route tensor forwarding from the previously tested single full-model provider path.
- Status: both nodes report exactly one running CUDA worker with layers loaded, RPC active, fresh publication, verified relay transport, public circuit multiaddresses, zero failed forwards, and zero forwarded requests before generation. The next gate is Device 1 generator route validation and one controlled prompt while both workers remain running.

### 2026-08-23 - Validate the Device 1 generator against the adjacent relay route

- What changed: started the Device 1 OPT-125M generator while retaining its local `0-6` worker and Device 2's remote `6-12` worker.
- Why: generator readiness must prove the authoritative selected route and per-provider RPC health before a real prompt is allowed to exercise sustained tensor forwarding.
- Status: passed. The generator is ready with route trace `QmV1JC... (0-6)` then `QmUUHD... (6-12)`, both selected providers are healthy and relay-verified, and startup produced the expected tensor shape `[1, 1, 768]`. The remote upper worker has not processed a tensor forward yet; one controlled prompt is the next terminal test.
