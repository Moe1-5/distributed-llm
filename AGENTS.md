# AGENTS.md

Cross-tool instructions for any AI coding assistant working in this repo, including Claude Code, Codex, Cursor, Aider, and Copilot.

For the full project rules, session start, sprint workflow, folder ownership, code rules, and testing protocol, see [CLAUDE.md](CLAUDE.md). Everything in `CLAUDE.md` applies to every assistant, not just Claude.

## Read Aloud Report (mandatory - coding assistants only)

End every coding-assistant response that completes meaningful repository work, and every development session wrap-up, with a final section headed exactly `## 🔊 Read Aloud`. The user consumes this through a text-to-speech tool, so it must be written for the ear:

- Plain spoken prose only. No bullets, no markdown symbols, no code blocks, no backticks, no emoji inside the spoken text, no raw file paths.
- Spell things out for speech: say "the App dot tsx file" not `App.tsx`, "sprint one" not `sprint-01`.
- Cumulative and self-contained: cover what was asked, what changed and why, what was verified, and what is still open, understandable without seeing the rest of the response.
- Place it last, after all other output. If a response did no real work, the section may be skipped.
