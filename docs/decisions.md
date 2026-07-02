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
