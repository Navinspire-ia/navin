---
description: Start a Ralph loop - the same task comes back at every end of turn until a promise or an iteration cap
argument-hint: "PROMPT" [--max-iterations N] [--completion-promise "TEXT"]
allowed-tools: exec, read_file, find_files, grep, apply_patch, edit_file, write_file, test_run, verify, git
---

# Ralph loop

Loop setup output:

!`${NAVIN_PYTHON} -m navin.agent.ralph_loop start "$NAVIN_PROJECT_DIR" $ARGUMENTS`

## What to do

- If the output above starts with `Error:`, show it to the user with the usage line `/ralph-loop "PROMPT" --max-iterations N --completion-promise "TEXT"` and stop: no loop was started.
- Otherwise work on the task now. When you end your turn, Navin gives you the same task again with the iteration number. Read what you already changed (files, `git diff`, test results) and improve on it instead of starting over.
- When a completion promise is set, write `<promise>TEXT</promise>` only when the statement is fully and truthfully done. Never write it to leave the loop early, even when stuck.
- The user can stop the loop at any time with `/cancel-ralph`.
