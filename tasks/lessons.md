# Lessons

> Append-only. Never delete entries.
> After any correction from the user, add to `## Active` immediately.
> When a lesson has not been violated in two or more sprints, move it to `## Internalized`.

## Format

```markdown
### [YYYY-MM-DD] Short title
**Problem:** What went wrong
**Rule:** The rule to prevent it
**Why:** The reason this matters
```

---

## Active

### [2026-08-22] Verify session-dependent RPC selection before equating diagnostics
**Problem:** I said Trace exercised the same failing generation path as shadow-mode chat, but source review showed that chat starts a useful-work session and selects the receipt expert while Trace starts no session and selects the legacy expert.
**Rule:** Before treating a canary, trace, parity check, and user generation as equivalent distributed tests, follow their session setup and capability selection through the actual RPC UID used on the wire.
**Why:** Two operations can share `sequential.forward` yet exercise different expert schemas, serialization, accounting, and relay behavior; equating them hides the most useful isolation boundary.

### [2026-08-22] Separate route validation from sustained streamed execution
**Problem:** I treated independent lease visibility, metadata health, and a successful tensor canary as sufficient proof that the physical relay path was ready, but the first real prompt later failed with an ambiguous stream reset while the worker recovered its network transport and changed peer identity.
**Rule:** Report discovery, route validation, tensor canary, and sustained streamed generation as separate acceptance gates. Do not call the system working until at least one real prompt completes and recovery preserves the provider identity used by the selected route.
**Why:** A short health probe can pass immediately before a relay stream fails, and rotating the provider peer ID during recovery invalidates an otherwise healthy selected route.

### [2026-08-19] Leave user-owned physical validation to the requested handoff
**Problem:** I began a real local split probe after the code-side regression test passed, while the user intended to perform runtime validation personally.
**Rule:** When the user takes ownership of live or physical validation, stop runtime probes and limit the handoff to the implemented change, focused code checks already completed, and exact manual expectations.
**Why:** Shared-memory model probes are costly and can overlap with the user's controlled two-device acceptance environment.

### [2026-08-16] Diagnose runtime failures from correlated evidence before prescribing recovery
**Problem:** I inferred that the generator might have started before the serving node and suggested restarting it without first reconciling the UI timestamps, coverage preview, node RPC state, and generator route-validation path.
**Rule:** For distributed runtime failures, correlate timestamps and inspect each producer of displayed state before naming a cause or recommending a restart. Clearly separate confirmed facts, unresolved evidence, and the exact diagnostic needed to close the gap.
**Why:** Coverage discovery, local RPC status, generator health probes, and lifecycle jobs can disagree; an unsupported recovery step wastes a physical acceptance run and can conceal a real implementation defect.

### [2026-08-16] Label remote shell commands explicitly
**Problem:** I gave VPS Linux commands after an SSH session had disconnected, and the user reasonably pasted them into local Windows PowerShell where `sudo`, `$USER:$USER`, `chmod`, and `/var/lib/...` are invalid or mean the wrong machine.
**Rule:** For remote operations, every command block must state the required shell and expected prompt, such as VPS SSH shell versus local Windows PowerShell versus WSL Ubuntu, before the command.
**Why:** Distributed testing uses three shells at once, and an unlabeled command can waste time, fail misleadingly, or modify the wrong environment.

### Relay reservation is not RPC health

A successful `/p2p-circuit/` reservation proves that a peer can reserve transport through the VPS, but it does not prove that expert metadata or tensor RPC completes within the health deadline. Test reservation, metadata latency, and tensor forwarding as separate acceptance gates.

> Lessons that still need active enforcement.

### [2026-08-22] Verify physical-test placeholders before interpreting evidence
**Problem:** An observer command was run with the literal `PASTE_DEVICE_2_PEER_ID`, making every resulting provider-not-visible sample invalid even though it looked like network evidence.
**Rule:** Before interpreting a physical-test command, verify that every placeholder was replaced with the value from the current runtime and echo or inspect that value in the produced evidence.
**Why:** A syntactically successful diagnostic can confidently report the wrong conclusion when it is querying a placeholder or stale peer identity.

### [2026-08-14] Test packaged Windows command transport, not only generated shell text
**Problem:** Launcher unit tests validated the generated multiline Bash script, but the Windows Electron to WSL process boundary did not preserve it reliably and the packaged application executed a malformed command.
**Rule:** Commands sent through `wsl.exe` must use a transport that does not depend on multiline Windows argument preservation, and launcher tests must decode and verify the command at the final process-argument boundary.
**Why:** A shell script can be valid in isolation while failing after Windows argument serialization, so packaged acceptance must cover the actual boundary users run.

### [2026-08-13] Keep sprint implementation on dedicated feature branches
**Problem:** Sprint work and closure preparation must not continue on a shared or default branch while live acceptance is still pending.
**Rule:** Create or switch to a dedicated `feature/*` branch before changing sprint implementation, evidence tooling, or sprint status; push that branch for review, and archive a sprint only after the user explicitly says to close it.
**Why:** Isolated feature branches keep parallel sprint work reviewable and prevent unvalidated reachability or incentive work from being treated as approved.

### [2026-07-06] Separate stop, offline, and unload semantics
**Problem:** I treated stopping a local node as equivalent to deleting the node and unloading its layers, but the user expects stop to make a node offline while preserving loaded layers, with a separate delete/unload action for destructive cleanup.
**Rule:** Model serving lifecycle controls must distinguish pause/offline, resume/online, and delete/unload. UI labels must not hide destructive behavior behind a generic stop button.
**Why:** DistribLLM needs node operators to manage availability without paying reload costs or accidentally losing loaded model state.

### [2026-07-06] Split oversized sprint scopes before coding
**Problem:** User-review findings were documented inside Sprint 04 even though several items are product workflow, monitoring, and incentive-design work beyond local validation.
**Rule:** Keep the current sprint focused on its acceptance criteria; when new work would overload it, create later sprint plans before implementation and continue from the lowest active sprint.
**Why:** DistribLLM needs validation, UI workflow, monitoring, and incentive design to move in order instead of becoming one untestable sprint.

### [2026-07-05] Keep bootstrap infrastructure out of client workflows
**Problem:** Bootstrap setup was shown as a client-facing Network tab even though users need serving, inference control, and monitoring instead.
**Rule:** Treat bootstrap nodes as internal discovery infrastructure; client UI should expose model serving, inference control, network visibility, and monitoring without making users manage bootstrap commands directly.
**Why:** DistribLLM is meant to feel like a usable P2P inference system, not a developer-only control panel, and bootstrap confusion hides the actual serving and inference workflow.

### [2026-07-03] System-owned public swarm, not private-only network
**Problem:** I described the network goal as private, which made it sound local or closed-only rather than Petals-like public participation for this project.
**Rule:** Describe the target network as a system-owned public/discoverable Hivemind-style swarm: public enough for external devices to join and serve resources, but isolated from public Petals/IPFS infrastructure by this project's bootstrap, protocol, metadata, model registry, and rules.
**Why:** The long-term goal includes broad participation, incentives, monitoring, inference access, and eventually distributed training/resource requests, so the architecture should not be framed as private-only.

<!-- Add new lessons here -->

### [2026-07-22] Explain unfamiliar network mechanisms as concrete traffic flows
**Problem:** I recommended a public circuit relay without first explaining what the relay is, which machine opens each connection, whether participant ports must be opened, or how inference RPC traffic moves through it.
**Rule:** When proposing unfamiliar transport infrastructure, document the direct and fallback packet flows, operator requirements, security boundary, bandwidth tradeoff, and an explicit yes-or-no answer to the user's deployment question.
**Why:** Correct architecture terminology is not enough for review; the user must be able to understand and operate the proposed system without already knowing libp2p networking.

### [2026-07-14] Keep polled UI contracts compatible during backend restarts
**Problem:** I made Monitoring call `toFixed()` on a newly added runtime-metric field, so an Electron renderer connected to an older already-running backend could throw and show a black screen.
**Rule:** New fields in polled backend responses must be treated as optional at runtime until both processes are restarted, with unavailable or legacy payloads rendered safely instead of dereferenced directly.
**Why:** Electron and FastAPI restart independently during development; a temporarily mixed frontend/backend version must degrade visibly without crashing the entire renderer.

### [2026-07-13] Match smoke-test topology to the application lifecycle
**Problem:** I reused a serving node's DHT object for a generator smoke test, triggered Hivemind's valid self-dial rejection, and initially described it as a system limitation even though the real generator endpoint creates a separate client DHT identity.
**Rule:** Distributed smoke tests must use the same identity and process boundaries as the production API path before classifying a transport failure as a product bug.
**Why:** Physical-machine co-location is not the same as peer-identity reuse; an inaccurate harness can falsely invalidate a workflow that the application already supports.

### [2026-07-13] Keep public model loading independent from OAuth state
**Problem:** The backend passed a stored expired OAuth token to a public TinyLlama request, turning anonymous public access into a 401 failure that Transformers mislabeled as an invalid repository.
**Rule:** Pass Hugging Face credentials only when the selected operation and repository require them; public model loading must remain anonymous, and rejected credentials must map to an explicit reconnect action.
**Why:** Optional account state must not reduce public model availability or hide authentication expiry behind misleading model errors.

### [2026-07-10] Distinguish pasted tokens from OAuth-held tokens
**Problem:** I planned Sprint 10 as manual local import only, but the user wanted a seamless Hugging Face login button where DistribLLM can download approved gated models without the user pasting a token.
**Rule:** For gated model UX, distinguish "do not make the user paste a token or password" from "the app will never hold auth material"; if DistribLLM downloads gated files itself, plan for browser/device OAuth, least-privilege scopes, secure local storage, logout, and a no-auth local import fallback.
**Why:** A seamless account connection can be both easier and safer than token paste, but it must still be honest about the scoped access token required for gated downloads.

### [2026-07-09] Audit completed checklist items for edge cases
**Problem:** I marked Sprint 09 complete after tests passed, but a follow-up review found a real duplicate-replica edge case: after deleting the base replica, the next same-range replica could reuse an already-live RPC UID suffix.
**Rule:** Before calling a sprint checklist complete, review the implementation for lifecycle edge cases such as deletion, restart, non-contiguous state, and stale registry entries, then add regression tests for any discovered gap.
**Why:** A checked box is only useful if it reflects robust behavior, not just the first happy path that satisfied the wording.

### [2026-07-09] Prefer local gated-model import over credential ownership
**Problem:** I treated Hugging Face token validation/OAuth as the next solution for gated repos, but the user wants the simpler flow where approved users provide local model files and DistribLLM does not own their Hugging Face login or token lifecycle.
**Rule:** For gated model access, prefer validated local model import after external Hugging Face approval before adding token paste, OAuth, SSO, automatic download, or credential storage flows.
**Why:** Local import is simpler, avoids unnecessary credential risk, and keeps Sprint 10 focused on reliable model access instead of account integration.

---

## Internalized

> Lessons that are no longer being violated. Kept for reference.

<!-- Lessons migrate here from Active when consistently followed -->
