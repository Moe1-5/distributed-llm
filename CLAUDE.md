# CLAUDE.md

## Session Start

1. Read `INDEX.md` - master file map; look here first instead of guessing or searching.
2. Read `.context/current.md` - project state at a glance.
3. Read `tasks/active.md` - which sprints are currently running.
4. Read each sprint file listed in `tasks/active.md`.
5. Read `tasks/lessons.md` - focus on the `## Active` section first.

## Lookup Rule

Before searching the filesystem for any file, scan `INDEX.md`. The Quick "Where do I look for..." table covers the common case. Only fall back to file search if the index does not list it. If something is missing, update the index in the same task.

## Workflow

- Any task: plan, implement, verify, update the relevant sprint file.
- Non-trivial work: make the plan explicit before broad code edits.
- Sprint implementation and closure records must be made on a purpose-matched branch under `feature/`; do not continue sprint work directly on an integration or base branch.
- After any correction from the user: add an entry to `tasks/lessons.md` under `## Active` immediately.
- Sprint ends: move the sprint file to `tasks/archive/`, remove it from `tasks/active.md`, update `.context/current.md` only when the user explicitly says "close sprint N"; never archive based on checkboxes alone.
- Lesson internalized: when a lesson has not been violated in two or more sprints, move it from `## Active` to `## Internalized`.

## Sprint File Update Rule (mandatory)

After every implementation session, whether a feature, bug fix, refactor, test run, or documentation system update, append a dated entry to the active sprint file before the session ends.

Which sprint is current: the lowest-numbered sprint still listed in `tasks/active.md`. A sprint stays current until the user explicitly closes it, regardless of what later sprints exist or what `.context/current.md` highlights. All session entries go to the current sprint file unless the work is unambiguously part of a specific later sprint.

Append entries under the `## Session Log` section at the bottom of the sprint file:

```markdown
### YYYY-MM-DD - <one-line summary>
- What changed: <bullet list of files/components modified>
- Why: <brief reason or test failure that prompted the work>
- Status: <what is now working / what is still open>
```

When to do it: immediately after verification, before marking the work done. The sprint file is the authoritative record of what was built and why.

Enforcement: Claude Code can enforce this through `.claude/settings.json`. A PostToolUse hook records source edits through `scripts/sprint-log/record-edit.mjs`; a Stop hook blocks session end with `scripts/sprint-log/check-sprint-log.mjs` when code changed but no sprint file was updated. Session state lives in `.claude/sprint-sessions/`, which is gitignored.

## Folder Ownership

One source of truth per concern. Never duplicate. Never add a folder without updating this table.

| Concern | Location |
|---------|----------|
| Master file map | `INDEX.md` |
| Current project focus | `.context/current.md` |
| Active sprint list | `tasks/active.md` |
| Sprint task files | `tasks/sprints/sprint-NN-name.md` |
| Completed sprints | `tasks/archive/` |
| Lessons | `tasks/lessons.md` |
| Docs routing index | `docs/INDEX.md` |
| Full documentation landing page | `docs/README.md` |
| Architecture reference | `docs/CURRENT_ARCHITECTURE.md` and `docs/architecture.md` |
| Decision log | `docs/decisions.md` |
| Env variable template | `.env.example` |
| Build and utility scripts | `scripts/` |
| Backend application source | `backend/` |
| Electron frontend source | `frontend/` |

## Docs / Index Rule

When any file is created, moved, renamed, or changes scope, update `INDEX.md` in the same task. If the change is documentation-only, also update `docs/INDEX.md`.

## Code Rules

- Immutable: return new objects where practical; avoid mutating shared state unless it is the explicit lifecycle state being managed.
- Feature-aware: keep frontend UI work near its page/component domain and backend inference work near `backend/client`, `backend/node`, or `backend/models`.
- No hardcoded deployment values: use constants, settings, or documented environment variables.
- Explicit errors: never silently swallow runtime failures; surface actionable backend and frontend messages.
- Validate at boundaries: API requests, WebSocket messages, DHT metadata, model names, and external service responses.

## Distributed Inference Rules

- Do not trust DHT metadata without validation.
- Do not call overlapping layer ranges as a route; each layer should execute exactly once in order.
- Keep model architecture behavior explicit. Split inference must match HuggingFace forward semantics for the selected architecture.
- Treat bootstrap nodes as discovery only; they do not serve model layers.
- Keep HuggingFace tokens and local identities out of version control.

## Browser / System Testing Protocol (mandatory)

When running a browser test, end-to-end run, or "test the app / system test" task, this is find-and-report, never fix. It is a single linear pass, not a debugging loop.

- Never fix during testing. Record the bug or issue and move on.
- Keep an explicit checklist. Once an item passes or fails, close it.
- On any failure or unexpected behavior, stop and report it as a finding.
- Do not repeatedly rerun the same test hoping for a different result.
- Output a bug/issue list covering what passed, what failed, and what could not be verified.

## Read Aloud Report (mandatory - coding assistants only)

Cross-tool development rule. Mirrored in `AGENTS.md` so any coding assistant honors it while working on this repository.

End every coding-assistant response that completes meaningful repository work, and every development session wrap-up, with a final section headed exactly `## 🔊 Read Aloud`. The user consumes this through a text-to-speech tool, so it must be written for the ear:

- Plain spoken prose only. No bullets, no markdown symbols, no code blocks, no backticks, no emoji inside the spoken text, no raw file paths.
- Spell things out for speech: say "the App dot tsx file" not `App.tsx`, "sprint one" not `sprint-01`.
- Cumulative and self-contained: cover what was asked, what changed and why, what was verified, and what is still open.
- Place it last, after all other output. If a response did no real work, the section may be skipped.

## Before Done

- [ ] Behavior tested where practical.
- [ ] No new lint or syntax errors introduced.
- [ ] No duplicate folders or files created.
- [ ] Relevant sprint file updated.
- [ ] `.context/current.md` updated if sprint or stack changed.
- [ ] `INDEX.md` updated if any file was added, moved, renamed, or changed scope.
- [ ] `docs/INDEX.md` updated if doc files changed.
