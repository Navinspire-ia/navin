---
description: Create hookify rules that warn about or block unwanted agent behaviour, from your instructions or from the conversation
argument-hint: "[behaviour to prevent]"
allowed-tools: skill, spawn, ask_user, read_file, write_file, edit_file, find_files, grep, manage_files, board
---

# Hookify: turn unwanted behaviour into rules

First load the rule format: `skill action=read name=hookify-rules`.

Behaviour named by the user (may be empty): "$ARGUMENTS"

You create `.navin/hookify.<rule-name>.local.md` files in the current workspace so the behaviour is caught automatically from the next action on.

## Step 1: find the behaviours

**With a behaviour above:** that is the main request. Still look at the last 10 to 15 user messages of this conversation for extra context and concrete examples of it happening (the exact command, the file, the code pattern).

**Without one:** analyse the conversation. Collect the last 20 to 30 user messages of this conversation, verbatim, with the tool calls they react to (tool name and the command, path or code involved). Subagents cannot see this conversation, so put that material in the task itself, then call:

```
spawn agent="conversation-analyzer" label="hookify analysis" task="Find behaviours the user wants to prevent. Conversation excerpts, most recent first: <messages and tool calls>"
```

It returns a structured list of issues with tool, pattern, severity and a suggested rule. If it finds nothing worth a rule, say so and stop.

## Step 2: let the user choose

Ask with `ask_user`, one decision per call, and wait for each answer:

1. **Which behaviours become rules?** Up to 4 options, one per detected behaviour (label: short description like "Block rm -rf"; detail: why it is a problem). Recommend the most severe one. When several deserve a rule, run this question again for the next one after the first is done, rather than guessing.
2. **Warn or block?** For each chosen behaviour: "Just warn" (the action runs, the message is shown) or "Block" (the action is refused). Recommend block for destructive or security issues, warn otherwise.
3. **Pattern check.** Show the detected pattern and offer: keep it, widen it, or narrow it (with the concrete regex for each option).

If the user skips a question, take the recommended option and say so.

## Step 3: write the rule files

For each confirmed behaviour, write `.navin/hookify.<rule-name>.local.md` at the workspace root (create `.navin/` when missing), never inside Navin's own install folder.

- Name in kebab-case starting with a verb: `block-dangerous-rm`, `warn-console-log`, `require-tests-run`.
- Simple rule:

```markdown
---
name: <rule-name>
enabled: true
event: <bash|file|stop|prompt|all>
pattern: <regex>
action: <warn|block>
---

<Message: what was detected, why it matters, what to do instead>
```

- Several conditions (all must match):

```markdown
---
name: <rule-name>
enabled: true
event: file
conditions:
  - field: file_path
    operator: regex_match
    pattern: \.env$
  - field: new_text
    operator: contains
    pattern: API_KEY
---

<Message>
```

Write patterns unquoted in YAML. Avoid nested quantifiers such as `(a+)+`: such rules are refused.

## Step 4: confirm

1. Read every file back and check the frontmatter: name, event, action and the pattern exactly as intended.
2. Report what was created, for example:

```
Created 2 hookify rules:
- .navin/hookify.dangerous-rm.local.md: blocks shell commands matching rm\s+-rf
- .navin/hookify.console-log.local.md: warns when an edit adds console.log(
```

3. Tell the user the rules apply from the next action, with no restart, and how to manage them: `/hookify-list` to see them, `/hookify-configure` to switch them on or off, delete the file to remove one.

## Example

User: `/hookify Don't use rm -rf without asking me first`

1. Behaviour: `rm -rf` in shell commands.
2. `ask_user`: "Block this command or just warn?" -> the user picks "Just warn".
3. Write `.navin/hookify.dangerous-rm.local.md` with `event: bash`, `pattern: rm\s+-rf`, `action: warn` and the message "You asked to be warned before rm -rf. Check the path first."
4. Confirm the file and that it is live now.

## Troubleshooting

- Rule does not fire: check the file is in the workspace `.navin/` folder, the name starts with `hookify.` and ends with `.local.md`, `enabled` is not false, and the pattern matches the field of that event (bash checks `command`, file checks `new_text`).
- Too strict: change `action: block` to `action: warn`, or narrow the pattern.
- `python -m navin.agent.hookify check <workspace>` lists broken rules with the reason.
