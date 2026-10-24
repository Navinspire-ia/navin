# Hook Development Helper Scripts

Three scripts to check, test and lint hooks before you ship them. Run them
with `bash` (or directly once executable).

## validate-hook-schema.sh

Checks a `hooks.json` file for structural mistakes and common problems.

**Usage:**
```bash
./validate-hook-schema.sh path/to/hooks.json
```

**What it checks:**
- The file is valid JSON
- Required fields are present
- Event names are among those Navin dispatches (SessionStart, UserPromptSubmit,
  PreToolUse, PostToolUse, Stop, SubagentStop)
- `matcher` is only used on tool events
- Hook type: only `command` runs in Navin; any other type (for example `prompt`)
  is reported because Navin would skip it
- `timeout` is a number between 1 and 600 seconds
- Hard-coded paths instead of `${NAVIN_PLUGIN_ROOT}`

**Example:**
```bash
cd my-plugin
./validate-hook-schema.sh hooks/hooks.json
```

## test-hook.sh

Runs one hook script with a realistic event, the way Navin would, before you
wire it in.

**Usage:**
```bash
./test-hook.sh <Event> <hook-script> [tool_input-json | prompt-text]
```

**Arguments:**
- `<Event>` - the event to simulate; the script builds the matching JSON
  payload (tool name and `tool_input` for tool events, `prompt` for
  UserPromptSubmit, `source` for SessionStart, `stop_hook_active` for
  Stop/SubagentStop - pass `active` to set it)
- third argument - the `tool_input` JSON for tool events, or the prompt text
- `TOOL_NAME` environment variable - forces the tool name (otherwise guessed:
  `exec`, `write_file` or `edit_file`)

There is no option to write a sample input file: the payload is generated on
the fly and printed in the report.

**Example:**
```bash
# A dangerous shell command
./test-hook.sh PreToolUse ../examples/validate-bash.sh '{"command": "rm -rf /"}'

# A prompt
./test-hook.sh UserPromptSubmit ./check-prompt.sh 'deploy to prod'

# A stop event, with a forced tool name for a tool event
./test-hook.sh Stop ./require-tests.sh
TOOL_NAME=write_file ./test-hook.sh PreToolUse ../examples/validate-write.sh '{"path": "/etc/passwd", "content": "x"}'
```

**What you get:**
- `NAVIN_PROJECT_DIR` and `NAVIN_PLUGIN_ROOT` set as Navin sets them
- The input payload
- The exit code and its meaning (`allow`, `BLOCK` for 2, `hook error` otherwise)
- The run time in milliseconds
- stdout and stderr
- Whether JSON on stdout is valid (invalid JSON is treated as plain text)

## hook-linter.sh

Looks for common mistakes and bad practice in hook scripts.

**Usage:**
```bash
./hook-linter.sh <hook-script.sh> [hook-script2.sh ...]
```

**What it checks:**
- Shebang present
- Executable bit (or call it with `bash`)
- `set -euo pipefail`
- The script actually reads stdin (the event JSON)
- `exit 1` used where a block was meant (only exit 2 blocks)
- exit 2 without a reason on stderr
- Unquoted variable expansions (injection risk)
- Hard-coded home paths instead of `$NAVIN_PLUGIN_ROOT` / `$NAVIN_PROJECT_DIR`

**Example:**
```bash
# One script
./hook-linter.sh ../examples/validate-write.sh

# Several scripts
./hook-linter.sh ../examples/*.sh
```

## Typical Workflow

1. **Write the hook script**
   ```bash
   vim my-plugin/scripts/my-hook.sh
   ```

2. **Lint it**
   ```bash
   ./hook-linter.sh my-plugin/scripts/my-hook.sh
   ```

3. **Pick a test input**
   ```bash
   # Choose the event and the tool_input you want to exercise
   ```

4. **Run the hook**
   ```bash
   ./test-hook.sh PreToolUse my-plugin/scripts/my-hook.sh '{"command": "ls"}'
   ```

5. **Register it in hooks.json**
   ```bash
   # Edit my-plugin/hooks/hooks.json
   ```

6. **Validate the configuration**
   ```bash
   ./validate-hook-schema.sh my-plugin/hooks/hooks.json
   ```

7. **Try it in Navin**
   ```bash
   /pack install ./my-plugin   # then trigger the event in a session
   ```

## Tips

- Test every hook before shipping: a broken hook disrupts the user's work
- Read the full stdout/stderr report of `test-hook.sh` to understand behaviour
- Act on the linter warnings about security and practice
- Re-validate `hooks.json` after each change
- Try several inputs: safe operations, dangerous ones, edge cases

## Common Issues

### The hook never runs

Check that:
- The script has a shebang (`#!/usr/bin/env bash`)
- It is executable (`chmod +x`) or invoked as `bash script.sh`
- The path in `hooks.json` is right (use `${NAVIN_PLUGIN_ROOT}`)
- The type is `command` (other types are not run)
- A project `hooks.json` has been trusted (`python -m navin.agent.user_hooks trust <workspace>`)

### The hook times out

- Lower the `timeout` in `hooks.json`
- Make the script faster
- Drop long-running work

### The hook fails silently

- Check exit codes (0 to allow, 2 to block; anything else is a non-blocking error)
- Send error messages to stderr (`>&2`)
- Validate the JSON you print on stdout

### Injection vulnerabilities

- Quote every variable: `"$variable"`
- Use `set -euo pipefail`
- Validate every input field
- Run the linter to catch issues
