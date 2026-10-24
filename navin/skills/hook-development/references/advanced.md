# Advanced hooks

## Parallel execution

All matching hooks of an event run at the same time. Consequences:

- Hooks must not depend on each other's side effects or order.
- Two hooks writing the same file need a lock (`flock`) or separate files.
- One blocking hook is enough to block; reasons of every blocking hook are
  joined.

## Performance

PreToolUse and PostToolUse run on every matching tool call, so their latency
adds to the turn.

- Narrow the matcher instead of filtering inside the script.
- Exit early: read only the fields you need, return before heavy work.
- Cache expensive results under `.navin/cache/<plugin>/`.
- Set a small `timeout` (5 to 15 s) on tool hooks; keep long checks for Stop.

## State between calls

Hooks are separate processes. Keep state in files:

- per session: `.navin/cache/<plugin>/<session_id>.json` (`session_id` is in
  the input)
- per project: `.navin/<plugin>.local.md` settings, read on each call

Clean old session files from a SessionStart hook.

## Avoiding Stop loops

- Check `stop_hook_active` and let the stop pass when it is true.
- Block only on a condition the model can fix (tests red, file missing), and
  say exactly how in the reason.
- Navin caps the number of continuations a Stop hook can force in one turn.

## Output size

Stdout and stderr are capped at 8000 characters each. Summarize long tool
output (first errors, counts) instead of dumping it.

## Exit code discipline

| Want | Do |
|---|---|
| allow silently | exit 0, no output |
| allow with feedback | exit 0, JSON with `systemMessage` or `additionalContext` |
| block | exit 2 with the reason on stderr, or exit 0 with a block decision |
| hook itself broke | any other code; Navin logs it and continues |

Never exit 1 to block: it is treated as a broken hook, not a refusal.

## Combining with agents

A Stop hook can require that a review agent ran: a PostToolUse hook on `spawn`
records the agent name in a session file, and the Stop hook blocks until the
record exists.
