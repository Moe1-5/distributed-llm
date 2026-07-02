// Stop hook: if source code was edited this session but no file under
// tasks/sprints/ was updated, block the stop and tell the assistant to append
// a Session Log entry to the active sprint file first.
import { readFileSync, existsSync, unlinkSync } from "node:fs";
import { join } from "node:path";

const STATE_DIR = join(".claude", "sprint-sessions");

function main() {
  const input = JSON.parse(readFileSync(0, "utf8"));
  const sessionId = input.session_id || "unknown";
  const stateFile = join(STATE_DIR, `${sessionId}.json`);

  if (!existsSync(stateFile)) return 0;

  const state = JSON.parse(readFileSync(stateFile, "utf8"));

  if (state.codeEdited && !state.sprintLogged) {
    if (input.stop_hook_active) return 0;
    process.stderr.write(
      [
        "SPRINT LOG MISSING: code files were modified this session but no sprint file was updated.",
        "Before stopping: read tasks/active.md, open the current sprint file (the lowest-numbered",
        "sprint still listed there), and append a dated entry under its '## Session Log' section.",
        `Files modified this session: ${state.files.join(", ")}`,
      ].join("\n")
    );
    return 2;
  }

  try {
    unlinkSync(stateFile);
  } catch {}
  return 0;
}

let code = 0;
try {
  code = main();
} catch {
  code = 0;
}

process.exit(code);
