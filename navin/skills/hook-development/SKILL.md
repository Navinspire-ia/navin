---
name: hook-development
description: Write Navin lifecycle hooks (hooks.json, PreToolUse, PostToolUse, UserPromptSubmit, SessionStart, Stop, SubagentStop) as shell commands that read the event JSON on stdin and block, warn or add context through exit codes and JSON output. Use when the user asks to "create a hook", "block dangerous commands", "validate file writes", "run a check after every edit", "load context at session start", "stop the agent from finishing too early", "add hooks to my plugin", or needs the hook event schema and debugging steps.
---

# Hook Development

A hook is a shell command that Navin runs on a lifecycle event. It receives the
event as JSON on stdin and answers through its exit code and stdout. Hooks
enforce rules that must hold on every turn, without relying on the model to
remember them: refuse `rm -rf /`, lint every written file, inject the sprint
goal at session start, refuse to stop while tests are red.

Navin only runs **command** hooks. There is no prompt-based (LLM-evaluated)
hook type: an entry with `"type": "prompt"` or any other type is silently
skipped. When a rule needs judgment, write a script that applies explicit
checks, or move the judgment into a skill or an agent instead.

## Where hooks are configured

| Scope | File | Runs when |
|---|---|---|
| Personal | `~/.navin/hooks.json` | always |
| Plugin | `<plugin>/hooks/hooks.json`, or `"hooks"` in `plugin.json` (object or relative path) | the plugin is enabled |
| Project | `<workspace>/.navin/hooks.json` | once the user trusted this exact file |

All sources merge; every matching hook runs, in parallel. A project file can
come from a cloned repository, so Navin skips it until the user approves it:

```bash
python -m navin.agent.user_hooks trust /path/to/workspace
python -m navin.agent.user_hooks list /path/to/workspace
```

Trust is stored as a SHA-256 of the file content in
`~/.navin/trusted_hooks.json`. Any edit to the project file changes the digest,
so the file is untrusted again until it is approved again. `list` prints one
line per event with its hook count and warns when a project file exists but is
not trusted.

### Plugin layout

```
my-plugin/
  plugin.json
  hooks/
    hooks.json
    scripts/
      validate-bash.sh
      lint-after-write.sh
```

The manifest may instead embed the table or point to another file. A path must
be relative and must not contain `..`:

```json
{
  "name": "my-plugin",
  "hooks": "config/hooks.json"
}
```

### Personal or project layout

`~/.navin/hooks.json` and `<workspace>/.navin/hooks.json` use the same format.
They have no plugin root, so reference scripts by absolute path or relative to
the workspace (the hook runs with the workspace as working directory):

```json
{
  "hooks": {
    "PostToolUse": [
      {"matcher": "write_file|edit_file|apply_patch",
       "hooks": [{"type": "command", "command": "bash .navin/hooks/lint.sh"}]}
    ]
  }
}
```

## Format

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "exec",
        "hooks": [
          {"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/validate-bash.sh", "timeout": 10}
        ]
      }
    ]
  }
}
```

- The top-level `"hooks"` wrapper is optional; a bare event table works too:

  ```json
  {"Stop": [{"hooks": [{"type": "command", "command": "bash .navin/hooks/check-tests.sh"}]}]}
  ```

- `type`: `"command"` (the default when omitted). Other types are skipped.
- `command`: run through the system shell; empty commands are ignored.
- `timeout`: seconds, default 60, clamped between 1 and 600. A timed-out hook
  is killed and counts as a non-blocking error.
- `${NAVIN_PLUGIN_ROOT}` is replaced in plugin commands before they run.
- Unknown event names are ignored; malformed JSON files are skipped with a
  warning in the log.

## Matchers

`matcher` applies to `PreToolUse` and `PostToolUse` only; other events ignore
it. It is a Python regular expression that must match the **whole** tool name
(full match, not substring). `""`, `"*"` or a missing matcher match every tool.
An invalid regex matches nothing.

```json
{"matcher": "exec"}
{"matcher": "write_file|edit_file|apply_patch"}
{"matcher": "mcp_.*"}
{"matcher": "mcp_github-.*_create_.*"}
{"matcher": "*"}
```

Common names from other harnesses also match, so shared hook files keep
working: `Bash` (exec), `Write` (write_file), `Edit` and `MultiEdit`
(edit_file, apply_patch), `Read` (read_file), `Grep`, `Glob` (find_files),
`LS` (list_dir), `WebFetch`, `WebSearch`, `Task` and `Agent` (spawn). Example:
`"Write|Edit"` matches write_file, edit_file and apply_patch. Matching is case
sensitive.

## Events

| Event | When | Blocking means |
|---|---|---|
| `SessionStart` | first message of a session | - (stdout becomes context) |
| `UserPromptSubmit` | each user message, before the turn | the message is refused with the reason |
| `PreToolUse` | before a tool runs | the tool does not run; the reason is returned to the model as the tool result |
| `PostToolUse` | after a tool ran | the reason is appended to the tool result so the model corrects itself |
| `Stop` | the main agent is about to give its final answer | Navin keeps working, with the reason as the next instruction |
| `SubagentStop` | a subagent is about to finish | same, for that subagent |

Navin has no `PreCompact`, `SessionEnd` or `Notification` events; hooks
registered under those names never run.

A Stop hook gets `stop_hook_active: true` when the turn already continued
because of a Stop hook; let it pass then, to avoid loops. Navin also stops
forcing continuations after 3 rounds.

### One example per event

PreToolUse - refuse writes to secrets files:

```json
{"PreToolUse": [{"matcher": "write_file|edit_file|apply_patch",
  "hooks": [{"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/validate-write.sh", "timeout": 10}]}]}
```

PostToolUse - lint after every edit:

```json
{"PostToolUse": [{"matcher": "write_file|edit_file|apply_patch",
  "hooks": [{"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/lint-after-write.sh", "timeout": 60}]}]}
```

UserPromptSubmit - add a reminder when the prompt mentions production:

```json
{"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/prod-guard.sh"}]}]}
```

SessionStart - inject project context:

```json
{"SessionStart": [{"hooks": [{"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/load-context.sh", "timeout": 10}]}]}
```

Stop - refuse to finish while tests fail:

```json
{"Stop": [{"hooks": [{"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/require-tests.sh", "timeout": 300}]}]}
```

SubagentStop - require a summary from subagents:

```json
{"SubagentStop": [{"hooks": [{"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/require-summary.sh"}]}]}
```

## Input (stdin)

Common fields: `hook_event_name`, `session_id`, `cwd`. Then per event:

| Event | Extra fields |
|---|---|
| PreToolUse | `tool_name`, `tool_input` (the tool arguments) |
| PostToolUse | `tool_name`, `tool_input`, `tool_response` (text, truncated to 8000 characters) |
| UserPromptSubmit | `prompt` |
| SessionStart | `source` (always `"startup"`) |
| Stop, SubagentStop | `stop_hook_active`, `last_assistant_message` (truncated to 8000 characters) |

There is no `transcript_path` or `permission_mode` field. A PreToolUse payload
for the exec tool looks like:

```json
{
  "hook_event_name": "PreToolUse",
  "cwd": "/home/me/project",
  "session_id": "cli:direct",
  "tool_name": "exec",
  "tool_input": {"command": "rm -rf build", "timeout": 60}
}
```

Read fields in bash with `python3` (always present with Navin) or `jq`:

```bash
input=$(cat)
tool=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_name",""))')
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
```

Tool argument names are Navin's: `command` for exec, `path` and `content` for
write_file, `path`, `old_text`, `new_text` or `edits` for edit_file. Check the
real arguments with `test-hook.sh` before relying on them.

## Environment variables

| Variable | Value |
|---|---|
| `NAVIN_PROJECT_DIR` | the workspace (or Navin's working directory when there is none) |
| `NAVIN_PLUGIN_ROOT` | the installed plugin folder; set only for plugin hooks |

The rest of Navin's environment is inherited. The working directory is the
workspace when it exists. Navin does not expose an env-file mechanism for
hooks to persist variables into the session; write state to a file instead.

```bash
cd "$NAVIN_PROJECT_DIR"
source "${NAVIN_PLUGIN_ROOT}/hooks/lib/common.sh"
```

## Output

Exit codes:

- `0`: success. Stdout may hold a JSON decision. For `SessionStart` and
  `UserPromptSubmit`, plain stdout is added as context; for other events plain
  stdout is ignored.
- `2`: block. Stderr (or stdout when stderr is empty) is the reason.
- anything else: non-blocking error, logged with the source file, the action
  goes on.

JSON on stdout (exit 0, the text must start with `{`):

```json
{"decision": "block", "reason": "Run the tests before finishing"}
{"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "Writes to .env are forbidden"}}
{"hookSpecificOutput": {"additionalContext": "Sprint goal: ship the export API"}}
{"additionalContext": "Remember: staging only"}
{"continue": false, "stopReason": "Maintenance window"}
{"systemMessage": "Lint passed"}
```

How Navin reads it:

- `decision` `"block"`/`"deny"`, `permissionDecision` `"deny"` or
  `continue: false` block. The reason comes from `reason`, then
  `permissionDecisionReason`, then `stopReason`.
- `permissionDecision: "allow"` or `"ask"` does not bypass Navin's own
  permission checks; it simply means "do not block".
- A `reason` without a block on `PostToolUse` is still appended to the tool
  result as feedback.
- `additionalContext` (top level or in `hookSpecificOutput`) is added to the
  user message (UserPromptSubmit, SessionStart) or to the tool result
  (PostToolUse).
- `systemMessage` is shown to the user as its own activity row (CLI and
  desktop) for every event; it is never sent to the model. When a
  UserPromptSubmit hook refuses the message, the text is added to the refusal.
- Hooks cannot rewrite `tool_input` (no `updatedInput`); block with a precise
  reason instead.
- Invalid JSON is treated as plain text.

With several hooks on one event, any block wins and all reasons are joined.

## Writing a hook script

1. Start with `#!/usr/bin/env bash` and `set -euo pipefail`.
2. Read stdin once: `input=$(cat)`.
3. Parse with `python3 -c` or `jq` when available.
4. Exit `0` fast when the event does not concern the script.
5. Write the reason to stderr and exit `2` to block.
6. Quote every variable; never `eval` input.

PreToolUse - block dangerous shell commands:

```bash
#!/usr/bin/env bash
set -euo pipefail
input=$(cat)
cmd=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')
if printf '%s' "$cmd" | grep -Eq 'rm -rf (/|~)( |$)|mkfs|dd if=.* of=/dev/'; then
  echo "Destructive command refused: $cmd" >&2
  exit 2
fi
exit 0
```

PreToolUse - deny with JSON instead of exit 2:

```bash
#!/usr/bin/env bash
set -euo pipefail
path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("path",""))')
case "$path" in
  *.env|*/.git/*|*id_rsa*)
    printf '{"hookSpecificOutput":{"permissionDecision":"deny","permissionDecisionReason":"%s is protected"}}\n' "$path" ;;
esac
exit 0
```

PostToolUse - lint the file that was written:

```bash
#!/usr/bin/env bash
set -euo pipefail
path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("path",""))')
[[ "$path" == *.py ]] || exit 0
if ! out=$(ruff check "$path" 2>&1); then
  python3 -c 'import json,sys; print(json.dumps({"reason": "ruff found problems:\n" + sys.argv[1]}))' "$out"
fi
exit 0
```

UserPromptSubmit - add context when production is mentioned:

```bash
#!/usr/bin/env bash
set -euo pipefail
prompt=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("prompt",""))')
if printf '%s' "$prompt" | grep -qi 'prod'; then
  echo "Production changes need a rollback plan and a ticket number."
fi
exit 0
```

SessionStart - print context (plain stdout is enough):

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$NAVIN_PROJECT_DIR"
echo "Branch: $(git branch --show-current 2>/dev/null || echo none)"
[[ -f package.json ]] && echo "Node project: use npm scripts for tests."
exit 0
```

Stop - keep working while tests fail, without looping:

```bash
#!/usr/bin/env bash
set -euo pipefail
input=$(cat)
active=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("stop_hook_active", False))')
[[ "$active" == "True" ]] && exit 0
cd "$NAVIN_PROJECT_DIR"
if ! python -m pytest -q >/dev/null 2>&1; then
  echo '{"decision": "block", "reason": "Tests fail. Fix them before finishing."}'
fi
exit 0
```

SubagentStop - require a summary in the final message:

```bash
#!/usr/bin/env bash
set -euo pipefail
msg=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("last_assistant_message",""))')
if ! printf '%s' "$msg" | grep -qi 'summary'; then
  echo "End with a 'Summary' section listing the files you changed." >&2
  exit 2
fi
exit 0
```

Ready-to-use scripts: `examples/validate-bash.sh` (dangerous shell commands),
`examples/validate-write.sh` (protected paths and secrets),
`examples/load-context.sh` (SessionStart context), `examples/shell-advice.py`
(Python, refuses `grep` / `find -name` with ripgrep advice, exit 2). Patterns for common rules
are in `references/patterns.md`; parallelism, state and performance in
`references/advanced.md`.

## Execution model

- Matching hooks of one event run in parallel; do not depend on order between
  them, and give each its own temp or state file.
- Stdout and stderr are each truncated to 8000 characters.
- Hooks are read from disk on every event, so edits to personal and plugin
  files apply on the next event without a restart. A changed project file
  needs a new `trust` first.
- A crash or failure in one hook never breaks the turn.
- Keep PreToolUse hooks fast (well under a second): they run before every
  matching tool call. Put slow checks on Stop with a generous timeout.

## Security

- Hooks run with the user's rights. Treat `tool_input` as untrusted text.
- Validate and quote every value: `"$path"`, never `$path`; never `eval`.
- Reject path traversal and sensitive targets explicitly:

  ```bash
  if [[ "$path" == *..* || "$path" == /etc/* ]]; then
    echo "Path outside the workspace: $path" >&2; exit 2
  fi
  ```

- Keep project hooks short and readable: the user must review them to trust.
- Never send event data to the network from a hook without telling the user.
- Prefer blocking with a precise reason over silently rewriting input.
- Set a `timeout` matching the work, so a hung script cannot stall a turn.

## Testing and debugging

```bash
bash scripts/validate-hook-schema.sh hooks/hooks.json
bash scripts/hook-linter.sh hooks/scripts/*.sh
bash scripts/test-hook.sh PreToolUse hooks/scripts/validate-bash.sh '{"command": "rm -rf /"}'
bash scripts/test-hook.sh UserPromptSubmit hooks/scripts/prod-guard.sh 'deploy to prod'
bash scripts/test-hook.sh Stop hooks/scripts/require-tests.sh active
TOOL_NAME=write_file bash scripts/test-hook.sh PreToolUse hooks/scripts/validate-write.sh '{"path": ".env", "content": "x"}'
```

- `scripts/validate-hook-schema.sh <hooks.json>` checks events, matchers,
  types and timeouts.
- `scripts/hook-linter.sh <script.sh> [more.sh ...]` checks the shell scripts
  (shebang, strict mode, stdin, quoting, exit codes).
- `scripts/test-hook.sh <Event> <script> [tool_input-json | prompt]` builds a
  realistic payload, runs the script and reports exit code (allow, BLOCK or
  hook error), duration, stdout, stderr and whether JSON output is valid. The
  tool name is guessed from the input (`command` gives exec, `content` gives
  write_file); set `TOOL_NAME` to force it. For Stop events pass `active` to
  simulate `stop_hook_active: true`.

Manual test without the helper:

```bash
echo '{"hook_event_name":"PreToolUse","tool_name":"exec","tool_input":{"command":"ls"}}' \
  | NAVIN_PROJECT_DIR=$PWD bash hooks/scripts/validate-bash.sh; echo "exit=$?"
```

When a hook never runs: the project file is not trusted (`list` says so), the
plugin is disabled, the matcher does not fully match the tool name, or the
JSON file failed to parse. Navin logs failures and timeouts with the source
file (`Hook <file> for <event> failed (<code>)`).

Porting hooks from another harness or from a Python `AgentHook`:
`references/migration.md`.

## Additional resources

- `references/patterns.md` - ten proven hook patterns
- `references/advanced.md` - parallel runs, state, performance, Stop loops
- `references/migration.md` - from other formats and from Python hooks
- `examples/validate-bash.sh`, `examples/validate-write.sh`, `examples/load-context.sh`
- `scripts/validate-hook-schema.sh`, `scripts/hook-linter.sh`, `scripts/test-hook.sh`
- `scripts/README.md` - usage notes for the bundled scripts
