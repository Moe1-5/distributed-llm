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

> Lessons that still need active enforcement.

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

---

## Internalized

> Lessons that are no longer being violated. Kept for reference.

<!-- Lessons migrate here from Active when consistently followed -->
