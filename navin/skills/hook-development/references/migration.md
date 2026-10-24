# Migrating hooks

## From another agent harness

Most hook files written for other coding agents share this format. To port one:

1. Copy the event table into `hooks/hooks.json` (plugin) or `.navin/hooks.json`.
2. Replace the harness plugin-root variable with `${NAVIN_PLUGIN_ROOT}` and the
   project-dir variable with `$NAVIN_PROJECT_DIR`.
3. Matchers like `Bash`, `Write|Edit`, `Read` keep working through Navin's
   aliases; Navin names (`exec`, `write_file|edit_file|apply_patch`) are more
   precise.
4. In scripts, read `tool_input.path` for Navin file tools (other harnesses use
   `file_path`); support both: `ti.get("path") or ti.get("file_path")`.
5. Shell commands arrive in `tool_input.command` for `exec`.
6. `type: "prompt"` hooks are not run by Navin: rewrite them as a command hook
   or as a rule in a skill.
7. Events Navin does not dispatch (`SessionEnd`, `PreCompact`, `Notification`)
   are rejected by the validator; drop them or move the logic to `Stop` or
   `SessionStart`.
8. Run `scripts/validate-hook-schema.sh` and `scripts/test-hook.sh`.

## From a Python AgentHook

Inside the Navin codebase, `AgentHook` subclasses (`navin/agent/hook.py`) run
in process and can change internal state. User hooks are for rules that the
user or a plugin owns:

| Python AgentHook | User hook |
|---|---|
| `before_execute_tool` that only inspects | `PreToolUse` command |
| `after_execute_tool` that reports | `PostToolUse` command |
| `finalize_content` checks | `Stop` command |

Keep Python hooks for product behaviour; move project or team policy to
hooks.json so it ships with the repository or a plugin.

## From inline instructions

A rule repeated in AGENTS.md ("never edit generated files") is a candidate for
a PreToolUse hook: the hook enforces it on every call, the instruction only
reminds.
