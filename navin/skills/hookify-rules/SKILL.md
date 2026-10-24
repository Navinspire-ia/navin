---
name: hookify-rules
description: Explains how to write hookify rules, the Markdown files in .navin/hookify.<name>.local.md that warn about or block agent actions (shell commands, file edits, stopping, user prompts) by matching regex patterns. Use when the user asks to "create a hookify rule", "write a hook rule", "configure hookify", "add a hookify rule", "block this command from now on", "warn me when the agent edits .env files", or needs help with hookify rule syntax, fields, operators and patterns.
metadata: {"navin":{"category":"development"}}
---

# Writing Hookify Rules

## Overview

A hookify rule is a small Markdown file with a frontmatter block. The frontmatter says what to watch (an event and one or more patterns) and what to do on a match (warn or block). The body is the message delivered when the rule fires. Navin evaluates these files natively on every tool call, at the end of every turn and on every user message: there is no hooks.json to edit, no script to run, and no restart. A new or edited rule applies on the very next action.

Rules only compare text. They never execute anything, so project rules work without the trust step that shell hooks need.

**Locations**

- Project rules: `.navin/hookify.<rule-name>.local.md` at the workspace root.
- Personal rules (every project): `~/.navin/hookify.<rule-name>.local.md`.
- When a personal and a project rule share the same `name`, the project rule wins.
- `NAVIN_HOOKIFY=0` in the environment switches every rule off.

The `.local.md` suffix marks them as personal working files; add `.navin/*.local.md` to `.gitignore` unless the team wants to share them.

## Rule File Format

### Short form: one pattern

```markdown
---
name: block-dangerous-rm
enabled: true
event: bash
pattern: rm\s+-rf
action: block
---

**Dangerous rm command detected.** Check the path and prefer a narrower delete.
```

The short `pattern` is a regex checked against the main field of the event: the command for `bash`, the new text for `file`, the prompt for `prompt` and the final assistant message for `stop`.

### Long form: several conditions

```markdown
---
name: warn-env-api-key
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.env$
  - field: new_text
    operator: contains
    pattern: API_KEY
---

An API key is being written to a .env file. Make sure the file is ignored by git.
```

Every condition must match for the rule to fire.

### Frontmatter fields

| Field | Required | Values |
|---|---|---|
| `name` | yes | kebab-case id starting with a verb: `warn-console-log`, `block-force-push`, `require-tests-run` |
| `enabled` | no (default `true`) | `true` or `false`; switch a rule off without deleting it |
| `event` | yes | `bash`, `file`, `stop`, `prompt` or `all` |
| `action` | no (default `warn`) | `warn` or `block` |
| `pattern` | short form | one regex on the event's main field |
| `conditions` | long form | list of `field`, `operator`, `pattern` |
| `tool_matcher` | no | narrow to tools by name: `exec`, `write_file\|edit_file`, or the common aliases `Bash`, `Write`, `Edit`, `MultiEdit` |

### What warn and block do

| Event | `warn` | `block` |
|---|---|---|
| `bash` / `file` | the user sees the message before the call, the agent gets it with the tool result | the call is refused and the agent reads the message as the reason |
| `stop` | the user sees the message | the agent keeps working with the message as its instruction (at most 3 rounds per turn) |
| `prompt` | the user sees it and the agent receives it as context | the message is refused with the rule text as reason |

Use `block` for things that must never happen and `warn` for reminders.

## Fields and Operators

### Fields by event

- **bash** (the `exec` tool): `command`.
- **file** (`write_file`, `edit_file`, `apply_patch`): `file_path`, `new_text`, `old_text`, `content`. With several files or edits in one call, the values are joined with newlines, so a pattern matches if any file or edit matches.
- **stop**: `transcript` (the whole turn: messages, tool calls and their arguments), `last_message` (the final answer), `content` (same as `last_message`).
- **prompt**: `user_prompt` (also `prompt` and `content`).
- **all**: any of the above, depending on what is happening; other tools (`read_file`, `web_fetch`...) expose their own parameter names, for example `path` or `url`.
- After a tool ran, `output` holds its result, so a rule can react to what a command printed.

A condition on a field that does not exist for the current event never matches.

### Operators

| Operator | Matches when the field |
|---|---|
| `regex_match` | contains a match of the regex (case-insensitive) |
| `not_regex_match` | contains no match of the regex |
| `contains` | contains the literal text |
| `not_contains` | does not contain the literal text |
| `equals` | is exactly the text |
| `starts_with` | starts with the text |
| `ends_with` | ends with the text |

`contains` is literal: `npm test|pytest` searches for that whole string including the bar. For alternatives use `regex_match` or `not_regex_match`.

## Message Body

The body is what the agent (and the user) read when the rule fires. A good message says what was detected, why it matters and what to do instead:

```markdown
**console.log detected**

Debug output should not ship.
- Use the project logger instead
- Or remove the line before committing
```

Keep it short; it is shown on every match. A rule without a body is refused as broken.

## Event Guide

### bash

```markdown
---
name: block-destructive-ops
event: bash
pattern: rm\s+-rf|dd\s+if=|mkfs|git\s+push\s+.*--force
action: block
---
Destructive command blocked. Ask the user before running it.
```

Common patterns: deletes `rm\s+-rf`, privilege `sudo\s+`, permissions `chmod\s+777`, history rewrites `git\s+reset\s+--hard`.

### file

```markdown
---
name: warn-debug-code
event: file
pattern: console\.log\(|debugger;|print\(
---
Debug statement added. Remove it before committing.
```

Narrow by path with a second condition:

```markdown
---
name: warn-console-log-ts
event: file
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.tsx?$
  - field: new_text
    operator: regex_match
    pattern: console\.log\(
---
console.log in a TypeScript file.
```

Common patterns: security `eval\(|innerHTML\s*=|dangerouslySetInnerHTML`, secrets `(API_KEY|SECRET|TOKEN)\s*=\s*["']`, generated files `node_modules/|dist/|build/` on `file_path`.

### stop

Completion checks run when the agent is about to end its turn:

```markdown
---
name: require-tests-run
event: stop
action: block
conditions:
  - field: transcript
    operator: not_regex_match
    pattern: npm test|pytest|cargo test|go test
---
No test command ran in this turn. Run the tests before finishing.
```

A `stop` rule with `action: warn` is a checklist reminder for the user.

### prompt

```markdown
---
name: warn-production-deploy
event: prompt
conditions:
  - field: user_prompt
    operator: regex_match
    pattern: deploy.*prod
---
Production deploy requested: tests green, change reviewed, rollback ready?
```

## Pattern Tips

Rules use Python regex syntax and match case-insensitively.

- Escape special characters: `\.` for a dot, `\(` for a parenthesis, `\[` for a bracket.
- `\s` whitespace, `\d` digit, `\w` word character, `.` any character, `|` alternative.
- Write patterns unquoted in YAML: `pattern: rm\s+-rf` works as is. Inside double quotes every backslash must be doubled.
- Too broad: `log` also matches `login` and `catalog`; prefer `console\.log\(`.
- Too narrow: `rm -rf /tmp` misses other paths; prefer `rm\s+-rf`.
- Nested quantifiers such as `(a+)+` are refused: they can freeze the matcher. Patterns over 1000 characters are refused too.

Test a pattern before saving it:

```bash
python3 -c "import re; print(bool(re.search(r'rm\s+-rf', 'rm -rf build', re.I)))"
```

## Workflow

1. Name the behaviour to prevent and the tool involved.
2. Pick the event (`bash`, `file`, `stop`, `prompt`) and the action.
3. Write the pattern or the conditions, then the message.
4. Save `.navin/hookify.<name>.local.md` in the workspace (create `.navin/` if needed).
5. Read the file back and check the frontmatter parses: `name`, `event`, `action`, the pattern exactly as intended.
6. The rule is live on the next action. The system prompt lists active rules on the next turn.

To refine a rule, edit the file. To pause it, set `enabled: false`. To remove it, delete the file. `/hookify-list` shows every rule and `/hookify-configure` toggles them.

Broken rules (unknown event or operator, invalid or unsafe regex, empty body, no condition) are skipped silently at run time. `python -m navin.agent.hookify check <workspace>` lists each problem when a rule seems not to fire.

## Examples

Ready-to-copy rule files ship with this skill:

- `examples/dangerous-rm.local.md`: block `rm -rf`.
- `examples/console-log-warning.local.md`: warn when `console.log(` is added.
- `examples/sensitive-files-warning.local.md`: warn on `.env`, credentials and secrets files.
- `examples/require-tests-stop.local.md`: refuse to stop when no test command ran (disabled by default).

Copy one into `.navin/` under a `hookify.` prefix, for example `.navin/hookify.dangerous-rm.local.md`, and adapt it.

## Quick Reference

```markdown
---
name: my-rule
enabled: true
event: bash|file|stop|prompt|all
action: warn|block
pattern: regex
---
Message.
```

- Fields: bash `command`; file `file_path`, `new_text`, `old_text`, `content`; stop `transcript`, `last_message`; prompt `user_prompt`; after a tool `output`.
- Operators: `regex_match`, `not_regex_match`, `contains`, `not_contains`, `equals`, `starts_with`, `ends_with`.
