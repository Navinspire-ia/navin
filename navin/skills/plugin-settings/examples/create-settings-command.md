# Command that creates the settings file

Save as `<plugin>/commands/guard-settings.md`.

```markdown
---
description: Create or update the code-guard settings of this project
argument-hint: [enable|disable]
allowed-tools: Read, Write, Bash, ask_user
---

Manage `.navin/code-guard.local.md` for this workspace.

Current file (may be missing):
@.navin/code-guard.local.md

1. If $1 is "enable" or "disable", set `enabled` accordingly, keep every other
   value and the body, then go to step 4.
2. Otherwise show the current values (or the defaults: enabled true, mode
   block, protected_paths empty, max_file_kb 512) in a short table.
3. Ask with ask_user which change to make: mode (block recommended / warn),
   add a protected path, remove a protected path, change max_file_kb. Apply
   the answer.
4. Write the file with the keys in this order: enabled, mode, protected_paths,
   max_file_kb, owner. Keep the Markdown body unchanged.
5. Run `bash "$NAVIN_PLUGIN_ROOT/scripts/validate-settings.sh" .navin/code-guard.local.md enabled mode`
   and report the result.
6. Add `.navin/*.local.md` to .gitignore when it is missing.
7. Say that the change applies to the next tool call; no restart is needed.
```
