# hookify

Stop unwanted agent behaviour with small Markdown rules instead of hook scripts. Describe the behaviour, or let Navin find it in the conversation, and a rule file catches it from the next action on.

- Rules are plain files: `.navin/hookify.<name>.local.md` in the project, or `~/.navin/` for every project.
- Regex or literal matching on shell commands, file writes and edits, the end of a turn, or user messages.
- `warn` shows a message and lets the action run; `block` refuses it.
- Checked natively by Navin on every action: no script, no restart, no trust step (rules never execute code).

## Commands and agent

| Entry | What it does |
|---|---|
| `/hookify [behaviour]` | Creates rules from your instruction, or analyses the conversation when called alone |
| `/hookify-list` | Lists every rule with state, event, action and pattern |
| `/hookify-configure` | Switches rules on or off through a choice card |
| `/hookify-help` | Explains rules, events, operators and patterns |
| `conversation-analyzer` agent | Finds corrections, frustration and repeated mistakes in conversation excerpts and proposes rules |

The three helpers also answer as `/hookify:list`, `/hookify:configure` and `/hookify:help`. The `hookify-rules` skill documents the format and ships the example rules.

## Quick start

```
/hookify Warn me when the agent uses rm -rf
```

This writes `.navin/hookify.warn-rm.local.md`. Ask the agent to run `rm -rf /tmp/hookify-test` and the warning appears right away.

```
/hookify
```

Alone, it passes the recent conversation to the `conversation-analyzer` agent and asks you which of the detected behaviours become rules, and whether each one warns or blocks.

## Rule format

Simple rule (one pattern on the event's main field):

```markdown
---
name: block-dangerous-rm
enabled: true
event: bash
pattern: rm\s+-rf
action: block
---

**Dangerous rm command detected.** Check the path first.
```

Several conditions, all required:

```markdown
---
name: warn-sensitive-files
enabled: true
event: file
action: warn
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.env$|credentials|secrets
  - field: new_text
    operator: contains
    pattern: KEY
---

**Sensitive file edit.** Do not hardcode credentials and keep the file out of git.
```

| Event | Checked | Fields |
|---|---|---|
| `bash` | shell commands (`exec`) | `command` |
| `file` | `write_file`, `edit_file`, `apply_patch` | `file_path`, `new_text`, `old_text`, `content` |
| `stop` | when the agent is about to finish | `transcript`, `last_message` |
| `prompt` | each user message | `user_prompt` |
| `all` | all of the above | any; `output` after a tool ran |

Operators: `regex_match` (case-insensitive), `not_regex_match`, `contains`, `not_contains`, `equals`, `starts_with`, `ends_with`. `tool_matcher` narrows a rule to tools such as `exec` or `Edit|Write`.

## Examples

**Example 1: block destructive commands**

```markdown
---
name: block-destructive-ops
enabled: true
event: bash
pattern: rm\s+-rf|dd\s+if=|mkfs
action: block
---

Destructive operation blocked. Ask the user and use a safer approach.
```

The agent is not allowed to run these commands and reads the message as the reason.

**Example 2: warn about debug code**

```markdown
---
name: warn-debug-code
enabled: true
event: file
pattern: console\.log\(|debugger;|print\(
action: warn
---

Debug code detected. Remove it before committing.
```

The edit goes through; you see the warning and the agent gets it with the tool result.

**Example 3: require tests before finishing**

```markdown
---
name: require-tests-run
enabled: false
event: stop
action: block
conditions:
  - field: transcript
    operator: not_regex_match
    pattern: npm test|pytest|cargo test
---

No test command ran in this turn. Run the tests before finishing.
```

When enabled, the agent keeps working until a test command appears in the turn (at most 3 extra rounds).

**Example 4: hardcoded credential in TypeScript**

```markdown
---
name: api-key-in-typescript
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.tsx?$
  - field: new_text
    operator: regex_match
    pattern: (API_KEY|SECRET|TOKEN)\s*=\s*["']
---

Hardcoded credential. Read it from an environment variable instead.
```

Ready-to-copy files: `navin/skills/hookify-rules/examples/` (dangerous-rm, console-log-warning, sensitive-files-warning, require-tests-stop).

## Management

- Pause a rule: `enabled: false` in its file, or `/hookify-configure`.
- Delete a rule: remove its file.
- See every rule: `/hookify-list`.
- Switch all rules off: `NAVIN_HOOKIFY=0` in the environment.
- Check broken rules: `python -m navin.agent.hookify check <workspace>`.

When personal and project rules share a name, the project rule wins.

## Troubleshooting

- Rule does not fire: the file must be in the workspace `.navin/` folder (or `~/.navin/`), named `hookify.<name>.local.md`, with `enabled` not false and a pattern that matches the field of its event.
- Pattern does not match: test it with `python3 -c "import re; print(re.search(r'rm\s+-rf', 'rm -rf x', re.I))"`, and write it unquoted in YAML.
- Rule refused: invalid regex, nested quantifier such as `(a+)+`, empty message, unknown event or operator. The check command above names the reason.
- Too noisy: narrow the pattern, add a `file_path` condition, or switch `block` to `warn`.
