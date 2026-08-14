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

### Relay reservation is not RPC health

A successful `/p2p-circuit/` reservation proves that a peer can reserve transport through the VPS, but it does not prove that expert metadata or tensor RPC completes within the health deadline. Test reservation, metadata latency, and tensor forwarding as separate acceptance gates.

> Lessons that still need active enforcement.

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
