---
description: List the hookify rules of this project and of your personal folder, with their state
allowed-tools: skill, find_files, read_file
---

# Hookify rules

Load the rule format first: `skill action=read name=hookify-rules`.

## Steps

1. Find the rule files: `find_files path=".navin" glob="hookify.*.local.md" include_ignored=true` in the workspace. Personal rules live in `~/.navin/hookify.*.local.md`; read them too when that folder is reachable.
2. Read each file and take from the frontmatter: `name`, `enabled` (true when missing), `event`, `action` (warn when missing), the pattern or the conditions, and the first 100 characters of the message.
3. Show one table:

```
| Name | State | Event | Action | Pattern or conditions | File |
|---|---|---|---|---|---|
| block-dangerous-rm | on | bash | block | rm\s+-rf | .navin/hookify.dangerous-rm.local.md |
| warn-console-log | on | file | warn | console\.log\( | .navin/hookify.console-log.local.md |
| require-tests-run | off | stop | block | transcript not_regex_match npm test|pytest | .navin/hookify.require-tests.local.md |

Total: 3 rules (2 on, 1 off)
```

4. Under the table, one short preview per rule: event, pattern, the start of the message, and the file.
5. Flag rules that will not run and why: unknown event or operator, invalid regex, nested quantifier such as `(a+)+`, empty message, no pattern and no conditions. When a personal and a project rule share a name, say the project one wins.
6. End with:

```
Edit a rule: change its .local.md file
Switch a rule off or on: enabled: false / true, or /hookify-configure
Delete a rule: remove its file
Create a rule: /hookify
Changes apply from the next action, no restart needed.
```

## No rules

When no file exists, say so and suggest:

```
/hookify Warn me when the agent adds console.log
```

or copying an example from the hookify-rules skill (`examples/dangerous-rm.local.md` and others) into `.navin/hookify.<name>.local.md`.
