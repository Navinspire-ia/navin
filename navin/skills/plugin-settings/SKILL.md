---
name: plugin-settings
description: Give a Navin plugin per-project settings stored in .navin/<plugin>.local.md (YAML frontmatter for values, Markdown body for free text) and read them from hooks, commands and agents. Use when the user asks to "make my plugin configurable", "add plugin settings", "store per-project options", "enable or disable a hook per project", "read a .local.md file", "parse YAML frontmatter in bash", or wants a command that creates the settings file.
---

# Plugin Settings

Plugins often need options that differ per project: a strictness level, a
list of protected paths, an on/off switch for a hook, a reviewer name, or the
state of a running loop. The convention is one Markdown file per plugin and
per project:

```
<workspace>/.navin/<plugin-name>.local.md
```

The YAML frontmatter holds structured values; the Markdown body holds free
text (extra instructions, notes, a task prompt) that commands and agents can
read as is.

## File structure

### Template

```markdown
---
enabled: true
setting_one: "value"
setting_two: 42
---

# Extra context

Free text for the plugin: instructions, notes, a task description.
```

### Configuration example

```markdown
---
enabled: true
strict: false
max_file_kb: 512
protected_paths: ["migrations/", "infra/prod/"]
reviewer: "alice"
---

Prefer small, focused commits. Never touch the vendored SDK in third_party/.
```

### State example

The same file can hold state that a hook updates between runs:

```markdown
---
iteration: 3
max_iterations: 10
completion_promise: "All tests pass"
---

Fix the failing tests in tests/api/ one by one.
```

A complete annotated file is in `examples/example-settings.md`.

## Rules of the convention

- One file per plugin: `.navin/<plugin-name>.local.md`, the name matching
  `plugin.json`.
- The file is local to the user and the machine: add `.navin/*.local.md` to
  `.gitignore`. Shared team defaults belong in the plugin itself.
- The file is optional. Every reader treats a missing file as the defaults.
- Values are read on every use, so an edit applies on the next hook run or
  command call; no restart is needed (the hook reads the file itself each
  time it runs).
- No secrets in the file; use environment variables.

## Reading settings

### In hooks (bash)

Navin hooks are `command` hooks only: a shell command per event
(`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`,
`SubagentStop`) that gets the event as JSON on stdin and `NAVIN_PROJECT_DIR`
and, for plugin hooks, `NAVIN_PLUGIN_ROOT` in its environment. Exit 0 lets the
action continue, exit 2 blocks it with stderr as the reason. Hooks run as
separate processes and read the file each time. Use
`scripts/parse-frontmatter.sh` or the inline pattern from
`examples/read-settings-hook.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail
settings="${NAVIN_PROJECT_DIR:-.}/.navin/my-plugin.local.md"
parse="$NAVIN_PLUGIN_ROOT/scripts/parse-frontmatter.sh"

[[ -f "$settings" ]] || exit 0                       # no file: defaults, do nothing special
enabled=$(bash "$parse" "$settings" enabled)
[[ "$enabled" == "false" ]] && exit 0                # quick exit when disabled

strict=$(bash "$parse" "$settings" strict)
strict=${strict:-false}                              # default when the key is missing
max_kb=$(bash "$parse" "$settings" max_file_kb)
[[ "$max_kb" =~ ^[0-9]+$ ]] || max_kb=512

# hook logic using $strict and $max_kb
```

Register it in the plugin `hooks/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {"hooks": [{"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/guard.sh"}]}
    ]
  }
}
```

### In commands

Inline the file with an `@` reference and describe how to use it:

```markdown
---
description: Review with the project settings
---

Project settings (may be missing, then use the defaults strict=false, reviewer=none):
@.navin/my-plugin.local.md

Apply `strict` and `protected_paths` from the frontmatter and follow the
instructions of the body. Then review the staged changes.
```

A missing file is not inlined: the `@` text stays literal, so tell Navin what
the defaults are. To read a single value, use inline shell (requires
`allowed-tools: Bash`):

```markdown
Strictness: !`bash "${NAVIN_PLUGIN_ROOT}/scripts/parse-frontmatter.sh" .navin/my-plugin.local.md strict`
```

### In agents

Tell the agent in its system prompt:

```markdown
Before starting, read `.navin/my-plugin.local.md` if it exists. Apply
`strict` and `protected_paths` from its frontmatter (defaults: false and
none) and follow the instructions of its body.
```

## Parsing techniques

### Frontmatter block

```bash
frontmatter=$(awk 'NR==1 && /^---$/ {in_fm=1; next} in_fm && /^---$/ {exit} in_fm' "$file")
```

### One field

```bash
enabled=$(printf '%s\n' "$frontmatter" | grep '^enabled:' | sed 's/^enabled: *//')
reviewer=$(printf '%s\n' "$frontmatter" | grep '^reviewer:' | sed 's/^reviewer: *//; s/^"\(.*\)"$/\1/')
```

Compare booleans as strings (`[[ "$enabled" == "true" ]]`); check numbers
before arithmetic (`[[ "$max" =~ ^[0-9]+$ ]] && (( max > 100 ))`).

### Body

```bash
body=$(awk '/^---$/ {n++; next} n>=2' "$file")
```

### With the shipped script

```bash
bash scripts/parse-frontmatter.sh .navin/my-plugin.local.md            # key=value for every key
bash scripts/parse-frontmatter.sh .navin/my-plugin.local.md strict     # one value
bash scripts/parse-frontmatter.sh .navin/my-plugin.local.md --body     # the Markdown body
```

The script uses PyYAML when available, prints list items one per line, and
prints nothing (exit 0) for a missing file or key. Prefer it over hand-written
`sed` for lists, quotes and CRLF files. Strategies and edge cases are in
`references/parsing-techniques.md`.

## Common patterns

### Hook switched on and off per project

```bash
settings="${NAVIN_PROJECT_DIR:-.}/.navin/security-scan.local.md"
[[ -f "$settings" ]] || exit 0
[[ "$(bash "$NAVIN_PLUGIN_ROOT/scripts/parse-frontmatter.sh" "$settings" enabled)" == "true" ]] || exit 0
# run the scan
```

The hook stays registered; the file decides whether it acts. No edit of
`hooks.json` is needed.

### Agent state and coordination

```markdown
---
agent_name: auth-agent
task_number: 3.5
coordinator: lead
enabled: true
dependencies: ["Task 3.4"]
---

Implement JWT authentication for the API. Done when endpoints exist, tests
pass and the PR is open.
```

A `SubagentStop` or `Stop` hook reads `agent_name` and `coordinator` and writes
a status line to a file the coordinator watches (or the coordinator uses the
`team` tool). Avoid driving other terminals from hooks.

### Configuration-driven behavior

```markdown
---
validation_level: strict
max_file_kb: 1024
allowed_extensions: [".py", ".ts", ".md"]
---

Also flag TODO comments without an issue number.
```

```bash
level=$(bash "$parse" "$settings" validation_level)
case "${level:-normal}" in
  strict)  limit=1 ;;
  normal)  limit=5 ;;
  *)       limit=10 ;;
esac
```

## Creating the file

Ship a command that asks the few questions that matter and writes the file,
so users never write YAML by hand. A complete command is in
`examples/create-settings-command.md`. It should:

1. Read the existing file if present and show the current values.
2. Ask only for the values to change (ask_user for enumerations).
3. Write the frontmatter with stable key order and the body unchanged.
4. Run `scripts/validate-settings.sh` and report the result.
5. Make sure `.navin/*.local.md` is in `.gitignore`.

```markdown
---
description: Configure my-plugin for this project
allowed-tools: Read, Write, Bash(bash:*)
---

Current settings: @.navin/my-plugin.local.md

Ask the user with ask_user for `strict` (true/false) and `reviewer`. Write
.navin/my-plugin.local.md with those keys in this order, keeping the body.
Then run `bash ${NAVIN_PLUGIN_ROOT}/scripts/validate-settings.sh
.navin/my-plugin.local.md strict` and report the result.
```

Document the file for users in the plugin README:

````markdown
## Configuration

Create `.navin/my-plugin.local.md` (or run `/configure`):

```markdown
---
enabled: true
strict: false
---
```

Changes apply on the next command or hook run.
````

## Validating

`scripts/validate-settings.sh <file> [required-key ...]` checks the frontmatter
is present, closed and valid YAML, that keys are simple names, that required
keys exist, that booleans are real booleans, and that nothing looks like a
secret.

```bash
bash scripts/validate-settings.sh .navin/my-plugin.local.md enabled strict
```

Inside a hook, validate values you depend on:

```bash
if ! [[ "$max_kb" =~ ^[0-9]+$ ]] || (( max_kb < 1 || max_kb > 100000 )); then
  echo "my-plugin: max_file_kb invalid, using 512" >&2
  max_kb=512
fi
```

## Field design

| Kind | Write as | Read in bash |
|---|---|---|
| switch | `enabled: true` | compare to `true` / `false` |
| number | `max_file_kb: 512` | `[[ "$v" =~ ^[0-9]+$ ]]` before arithmetic |
| string | `reviewer: "alice"` | quotes are removed by the parser |
| list | `paths: ["a/", "b/"]` | parser prints one item per line |

Keep keys lower case with underscores, keep the set small, and give every key a
default that is applied when the file or the key is missing.

## Best practices

### Naming and gitignore

Use `.navin/<plugin-name>.local.md`, never a generic name. Ignore the files:

```gitignore
.navin/*.local.md
```

### Defaults

```bash
strict=$(bash "$parse" "$settings" strict); strict=${strict:-false}
reviewer=$(bash "$parse" "$settings" reviewer); reviewer=${reviewer:-none}
```

### Changing settings

Edit the file by hand or rerun the configure command; the next hook run or
command reads the new values. A hook that updates state (for example an
`iteration` counter) should rewrite the file atomically:

```bash
tmp=$(mktemp)
sed "s/^iteration: .*/iteration: $((iteration + 1))/" "$settings" > "$tmp" && mv "$tmp" "$settings"
```

## Security

- Escape user input before writing YAML: wrap strings in double quotes and
  escape `"` and `\`.

```bash
safe=${answer//\\/\\\\}; safe=${safe//\"/\\\"}
printf 'reviewer: "%s"\n' "$safe"
```

- Validate paths read from the file before using them:

```bash
case "$path" in
  /*|*..*) echo "my-plugin: rejected path $path" >&2; exit 2 ;;
esac
```

- Restrict permissions when the file holds anything personal:
  `chmod 600 .navin/my-plugin.local.md`. Never store tokens in it.

## Real-world patterns

Complete plugins built on this convention (guard plugin with protected paths,
review plugin with persona and strictness, loop plugin whose `Stop` hook is
driven by a state file, context plugin with a `SessionStart` summary) are in
`references/real-world-examples.md`.

A loop state file, for instance:

```markdown
---
iteration: 1
max_iterations: 10
completion_promise: "Lint clean and build green"
---

Fix every lint error, keeping tests green after each fix.
```

Its `Stop` hook exits 0 when the file is missing or `iteration` reached
`max_iterations`, otherwise bumps `iteration` and exits 2 with the body on
stderr so Navin continues with that prompt.

## Quick reference

```bash
file="${NAVIN_PROJECT_DIR:-.}/.navin/my-plugin.local.md"
[[ -f "$file" ]] || exit 0                                            # quick exit
value=$(bash "$NAVIN_PLUGIN_ROOT/scripts/parse-frontmatter.sh" "$file" key)  # one field
body=$(bash "$NAVIN_PLUGIN_ROOT/scripts/parse-frontmatter.sh" "$file" --body) # body
```

## Implementation workflow

1. Choose the keys and their defaults.
2. Write a sample in the plugin README and in `examples/`.
3. Read the file in hooks with a quick exit when missing.
4. Add a configure command that writes and validates the file.
5. Add `.navin/*.local.md` to `.gitignore`.
6. Test with no file, with defaults, and with every key set.

## Additional resources

- `references/parsing-techniques.md` - robust frontmatter parsing in shell and Python
- `references/real-world-examples.md` - full plugins built on settings files
- `examples/example-settings.md` - annotated settings file
- `examples/read-settings-hook.sh` - hook that obeys the settings
- `examples/create-settings-command.md` - command that writes the settings
- `scripts/parse-frontmatter.sh` - print one value, all values, or the body
- `scripts/validate-settings.sh` - check a settings file
