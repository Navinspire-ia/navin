---
description: Switch existing hookify rules on or off through a choice card
allowed-tools: skill, find_files, read_file, edit_file, ask_user
---

# Configure hookify rules

Load the rule format first: `skill action=read name=hookify-rules`.

## 1. Find the rules

`find_files path=".navin" glob="hookify.*.local.md" include_ignored=true` in the workspace. If there is none, answer: "No hookify rules yet. Create one with /hookify." and stop.

## 2. Read their state

Read each file and note `name` and `enabled` (a missing `enabled` means on), plus a one-line description from the message or the pattern.

## 3. Ask which rules to toggle

Call `ask_user` with one option per rule (up to 4 per question), for example:

- `warn-dangerous-rm (currently on)`: warns about rm -rf commands
- `warn-console-log (currently off)`: warns about console.log in code
- `require-tests-run (currently on)`: refuses to stop without a test run

Recommend the rule the user most likely wants to change (one switched off recently, or the strictest blocking rule). With more than 4 rules, ask again for the next group after the answer. The free-text answer may name several rules at once; toggle each of them.

## 4. Toggle

For each chosen rule, flip its state with `edit_file` on its file:

- on to off: `enabled: true` becomes `enabled: false`
- off to on: `enabled: false` becomes `enabled: true`
- no `enabled` line: add `enabled: false` right after the `name:` line

Handle quoted values too (`enabled: "true"`). Read the file back to confirm the change.

## 5. Confirm

```
Hookify rules updated

Switched on:
- warn-console-log

Switched off:
- warn-dangerous-rm

Unchanged:
- require-tests-run

Changes apply from the next action.
```

## Edge cases

- The user picks nothing or skips: say nothing was changed.
- A file cannot be read or edited: name the file and the error, and give the exact line to change by hand.
- To remove a rule for good, delete its file. `/hookify-list` shows the current state.
