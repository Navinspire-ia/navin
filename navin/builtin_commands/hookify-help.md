---
description: Explain how hookify rules work and how to create, list and configure them
allowed-tools: skill, read_file
---

# Hookify help

Load `skill action=read name=hookify-rules`, then explain hookify to the user with the points below, in their language. Keep it practical and show the examples.

## What it is

Hookify catches unwanted agent behaviour without any script: each rule is a Markdown file, `.navin/hookify.<name>.local.md`, whose frontmatter names an event, a pattern and an action, and whose body is the message. Navin reads these files on every action, so a new rule applies immediately.

## When rules are checked

- Before and after each tool call: `bash` rules see shell commands, `file` rules see file writes and edits.
- When the agent is about to end its turn: `stop` rules (completion checks on the turn transcript).
- When the user sends a message: `prompt` rules.
- `all` rules see every one of these.

## Actions

- `warn` (default): the action goes through and the message is shown, and passed to the agent.
- `block`: the tool call is refused, the turn does not end (`stop`), or the message is refused (`prompt`).

## Example rule

```markdown
---
name: block-chmod-777
enabled: true
event: bash
pattern: chmod\s+777
action: block
---

chmod 777 is a security risk. Use precise permissions instead.
```

More examples to show: warn on `console\.log\(` for `file` events, warn when `file_path` matches `\.env$`, and block stopping when the transcript has no test command (`not_regex_match` on `transcript`).

## Commands

- `/hookify <behaviour>`: create a rule from an instruction, for example `/hookify Warn me when the agent uses rm -rf`.
- `/hookify` alone: analyse the conversation (corrections, frustration, repeated mistakes) and propose rules.
- `/hookify-list`: list every rule with its state.
- `/hookify-configure`: switch rules on or off.
- `/hookify-help`: this help.

Each one also answers as `/hookify:list`, `/hookify:configure` and `/hookify:help`.

## Pattern syntax

Python regex, case-insensitive: `\s` whitespace, `\.` literal dot, `|` alternative, `\d` digit. Examples: `rm\s+-rf`, `console\.log\(`, `(eval|exec)\(`, `\.env$`. Write patterns unquoted in YAML.

## Good to know

- No restart: changes apply on the next action.
- Personal rules in `~/.navin/` apply to every project; a project rule with the same name wins.
- `enabled: false` pauses a rule; deleting the file removes it.
- `NAVIN_HOOKIFY=0` switches every rule off.
- A rule that never fires: check the folder, the `hookify.` prefix and `.local.md` suffix, and run `python -m navin.agent.hookify check <workspace>` to see broken rules.

## Getting started

1. `/hookify Warn me when the agent uses rm -rf`
2. Ask the agent to run `rm -rf /tmp/hookify-test`: the warning appears.
3. Adjust the rule file, then add rules as unwanted behaviour shows up.
