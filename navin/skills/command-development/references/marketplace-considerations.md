# Distributing commands

Commands shared through a plugin run on machines you do not control.

## Portability

- Paths: `${NAVIN_PLUGIN_ROOT}` for plugin files, workspace-relative paths for
  project files. No home directories, no absolute paths.
- Shell: POSIX `sh`/`bash` syntax in `!` snippets; avoid GNU-only flags when a
  portable form exists (`sed -i` differs on macOS). Prefer `python3` for
  anything non-trivial.
- Tools: do not assume `jq`, `gh` or `node` exist; check (`command -v jq`) and
  explain the fallback in the body.

## Naming

- Lower-case, hyphenated, specific: `changelog-draft`, not `cl`.
- Avoid names of Navin built-ins (`help`, `new`, `goal`, `plan`...): they win.
- Document the `/<plugin>:<name>` form in the README for users who have a
  clashing project command.

## Safety

- Never ship `!` snippets that write, delete or push. State-changing steps go
  in the instructions where the user sees each tool call.
- Keep `allowed-tools` minimal; a read-only command should not grant `exec`.
- No secrets in command files. Read them from the environment at run time.

## Versioning

- Bump the plugin version in `plugin.json` when a command changes behaviour.
- Keep old argument forms working for one version, or print a clear message
  explaining the new form.

## Quality bar before publishing

- Description and argument hint on every command.
- Usage message on empty calls when arguments are required.
- Tested with the matrix in `testing-strategies.md`.
- Plugin validator clean.
