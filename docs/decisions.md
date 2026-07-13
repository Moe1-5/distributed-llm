# Decision Log

> Append-only. Each entry is an Architecture Decision Record.
> Never edit past entries; add new ones when decisions change.

## Format

```markdown
### [YYYY-MM-DD] Decision title
**Context:** Why this choice was needed
**Decision:** What was chosen
**Alternatives:** What was ruled out and why
**Consequences:** What this enables or constrains
```

---

### [2026-06-30] Adopt project operating system from Project-Starter

**Context:** The repository had source code and substantial technical documentation, but lacked the Project-Starter workflow system for assistant session start, file routing, sprint tracking, lessons, and decision history.

**Decision:** Add the Project-Starter operating system files and adapt them to DistribLLM's existing Electron, FastAPI, Hivemind, and Transformers architecture.

**Alternatives:** Copy the template files unchanged, or keep the current docs-only structure. Copying unchanged would leave placeholders that do not reflect this project; keeping docs only would not provide sprint/session continuity.

**Consequences:** Future assistant sessions have a stable onboarding path through `INDEX.md`, `.context/current.md`, and `tasks/active.md`. Source edits should be logged in the active sprint, and architecture decisions have a dedicated append-only place.

### [2026-07-10] Use browser OAuth plus validated local runtime for gated models

**Context:** Gated Hugging Face models require authenticated downloads, but asking users to paste personal access tokens or passwords is a poor desktop workflow.

**Decision:** Use Hugging Face device OAuth for managed downloads, store the scoped token outside the repository, validate downloaded snapshots, and run gated models from the validated local path with offline semantics. Keep manual folder import as a no-auth fallback.

**Alternatives:** Manual token paste, collecting account passwords, or manual-only import. Password collection was rejected; token paste is retained only as diagnostic legacy support; manual-only import remains available but is not the primary connected UX.

**Consequences:** DistribLLM can download repositories already approved for the connected account without seeing the password. OAuth expiry affects new downloads, while validated snapshots remain runnable offline.

### [2026-07-12] Use an environment-configured public bootstrap peer

**Context:** Laptop, VPS, and Colab participants need one stable project-owned discovery point. Loopback/private addresses and hardcoded development peers do not work across machines.

**Decision:** Run a persistent VPS bootstrap with a preserved private identity and configure clients through `DISTRIBLLM_INITIAL_PEERS`. Remote clients use the public multiaddress; VPS-local processes may use loopback.

**Alternatives:** Public IPFS/Petals discovery, source-code edits per deployment, or local-only bootstrap. These conflict with project isolation, deployment ergonomics, or remote participation.

**Consequences:** Bootstrap identity, port, and firewall become operator responsibilities. Discovery reachability does not by itself solve worker RPC NAT/firewall reachability.

### [2026-07-12] Prefer publisher-provided chat and instruction models

**Context:** Small base completion models are useful for transport/parity checks but produce weak user-facing instruction behavior.

**Decision:** Keep base models labeled as test targets and add explicitly labeled chat/instruction models such as TinyLlama chat and Llama 2 chat. Do not treat prompt wrappers as a substitute for instruction tuning.

**Alternatives:** Fine-tune the baseline during this sprint or hide base-model limitations with prompts. Both expand scope and weaken reproducibility.

**Consequences:** Every added model needs registry metadata, architecture compatibility, local-import rules when gated, and parity/live validation before being claimed as user-facing.

### [2026-07-13] Isolate public loading from optional authentication

**Context:** An expired OAuth token was attached to a public TinyLlama request, causing Hugging Face to return 401 and Transformers to misreport the repository as invalid.

**Decision:** Public models explicitly load with anonymous `token=False`; gated runtime uses validated local files without a token; authenticated failures map to a reconnect action; failed startup releases partial resources.

**Alternatives:** Require users to reconnect before every public load or silently retry with different credential sources. Both make optional account state reduce public availability or hide lifecycle bugs.

**Consequences:** Public model startup is independent from OAuth state, while authenticated downloads retain explicit expiry handling and cleanup contracts.

### [2026-07-13] Manage model cache by repository and preferred weight format

**Context:** A Llama 2 download retained both PyTorch bin and safetensors shards, consuming 27 GB, while deletion targeted one registered snapshot and could not recover an already-unregistered cache.

**Decision:** Prefer safetensors when the repository provides them, delete all cached revisions for a managed model repository, and allow cache deletion by model identity even when no registry record remains.

**Alternatives:** Download every framework format or delete only the exact registered snapshot. Both waste disk or leave recovery gaps.

**Consequences:** Managed downloads use less disk, deletion reports registry and cache outcomes separately, and arbitrary manual folders remain protected from recursive deletion.
