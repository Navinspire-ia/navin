---
name: command-development
description: Write Navin slash commands as Markdown prompt files (frontmatter, $ARGUMENTS, $1..$9, ${1:-default}, @file inlining, !`cmd` shell output, allowed-tools, namespaces, plugin commands with ${NAVIN_PLUGIN_ROOT}). Use when the user asks to "create a slash command", "add a custom command", "write a command that takes arguments", "inline a file in a command", "run a shell command inside a command", "restrict the tools of a command", "add a command to my plugin", or asks how Markdown commands are discovered and expanded.
---

# Command Development

A slash command is a Markdown file whose body is a prompt. When the user types
`/<name> <arguments>`, Navin expands the file (arguments, referenced files,
shell output) and runs the result as one agent turn. Commands package a
repeatable workflow behind a short name: a review checklist, a release routine,
a scaffold.

## Commands are instructions for Navin

The body is written **for Navin**, not for the user. It gives instructions
("Review the staged diff and report..."), never a message ("This command will
review your code"). The user sees the result of the turn, not the prompt.

Correct:

```markdown
Review the staged changes for security issues: injection, unsafe file access,
secrets in code. Report each finding with file, line and severity.
```

Wrong:

```markdown
This command reviews your code for security issues and you will get a report.
```

## Where commands live

| Scope | Folder | Notes |
|---|---|---|
| Project | `<workspace>/.navin/commands/` | shared with the repository |
| Personal | `~/.navin/commands/` | every workspace of this user |
| Plugin | `<plugin>/commands/` (or the `commands` paths of `plugin.json`) | while the plugin is enabled |
| Shipped | inside Navin (`/create-plugin`) | always available |

Rules:

- The command name is the file stem in lower case: `review-pr.md` gives `/review-pr`.
  The stem must start with a letter or digit (letters, digits, `_`, `.`, `-`).
- Built-in commands of Navin (`/help`, `/new`, `/goal`...) always win; a file
  named after one of them is never reached.
- On a name clash: project, then personal, then plugin, then shipped.
- Plugin commands are also reachable as `/<plugin>:<name>`, which avoids clashes.
- Subfolders are namespaces for humans: `.navin/commands/git/sync.md` is still
  `/sync`. Mention the namespace in the description.
- `README.md` files are ignored, so are files over 128 KB and files with an
  empty body.
- Commands are read when invoked, so an edit takes effect on the next call.

Create a project command:

```bash
mkdir -p .navin/commands
cat > .navin/commands/review.md <<'EOF'
Review the staged diff for bugs, missing tests and unclear names.
EOF
```

## File format

The smallest command is plain Markdown with no frontmatter; the whole file is
the prompt:

```markdown
Run the test suite, then summarize failures grouped by module with the most
likely cause for each.
```

With frontmatter:

```markdown
---
description: Review a pull request branch against the project checklist
argument-hint: <branch> [focus]
allowed-tools: Read, Grep, Bash(git:*)
---

Review the branch $1 against main. Focus on ${2:-correctness and tests}.

Changed files:
!`git diff --stat main...$1`

Apply the checklist in @docs/review-checklist.md and report findings as a
table: file, line, severity, fix.
```

## Frontmatter fields

| Field | Effect in Navin |
|---|---|
| `description` | shown in the command list and in the system prompt index; keep it under 80 characters |
| `argument-hint` | shown next to the name, e.g. `<file> [mode]` |
| `allowed-tools` | restricts the tools of the turn (see below) |
| `model` | accepted for compatibility; the turn runs on the session model |
| `disable-model-invocation` | accepted for compatibility; Markdown commands only run when typed |

### description

```yaml
---
description: Generate release notes from commits since the last tag
---
```

Start with a verb, say what the command produces. Without it the list shows
only the name.

### allowed-tools

```yaml
---
allowed-tools: Read, Grep, Bash(git:*)
---
```

or as a YAML list:

```yaml
---
allowed-tools:
  - read_file
  - grep
  - exec
---
```

Common names from other harnesses are mapped to Navin tools (`Bash` to `exec`,
`Read` to `read_file`, `Write` to `write_file`, `Edit` to `edit_file` and
`apply_patch`, `Grep`, `Glob` to `find_files`, `WebFetch` to `web_fetch`,
`Task` to `spawn`). Navin tool names work directly. Argument filters such as
`Bash(git:*)` are accepted; the filter is documentation, the whole tool is
granted. `skill` and `read_file` are always kept so the command can load
playbooks and read its own references. Omit the field to inherit every tool of
the session.

### model

```yaml
---
model: haiku
---
```

Navin parses the key but ignores it: the command runs on the model of the
current session. Choose the model per session or per subagent
(`spawn model_preset=...`) instead.

### argument-hint

```yaml
---
argument-hint: <environment> [version] [--dry-run]
---
```

Use `<required>` and `[optional]`, in the same order as `$1`, `$2`... The hint
appears in the command list so the user knows what to type.

### disable-model-invocation

```yaml
---
disable-model-invocation: true
---
```

Accepted and ignored. Navin never invokes Markdown commands on its own: they
run only when the user types them, so every command already behaves as if this
flag were set.

See `references/frontmatter-reference.md` for YAML pitfalls and the full
tool-name mapping.

## Dynamic arguments

| Placeholder | Replaced by |
|---|---|
| `$ARGUMENTS` | everything after the command name, verbatim |
| `$1` .. `$9` | positional arguments, split like a shell (quotes group words) |
| `${1:-text}` | positional argument, or `text` when it is missing |

A missing positional becomes an empty string. When the body uses no
placeholder at all, Navin appends the arguments as a final `Arguments: ...`
line, so a command never silently drops what the user typed.

### $ARGUMENTS

```markdown
---
description: Explain a piece of the codebase
argument-hint: <topic>
---

Explain how $ARGUMENTS works in this repository. Cite files and functions.
```

`/explain the retry logic of the gateway` gives "Explain how the retry logic
of the gateway works...".

### Positional arguments

```markdown
---
description: Compare two git refs
argument-hint: <base> <head>
---

Compare $1 with $2. List changed modules, risky edits and missing tests.
```

`/compare main "feature/login form"` sets `$1=main` and
`$2=feature/login form` (quotes group words, as in a shell).

### Defaults and combinations

```markdown
---
argument-hint: <file> [level]
---

Review @$1 at strictness ${2:-normal}. Extra notes from the user: $ARGUMENTS
```

`${N:-default}` is resolved first, then `$ARGUMENTS`, then `$1`..`$9`. Only
digits 1 to 9 are positional; `$10` is not.

## File references

`@path` inlines a file before the turn starts, wrapped in a `<file path="...">`
block. Paths are relative to the workspace; plugin commands may use
`@${NAVIN_PLUGIN_ROOT}/templates/report.md`. Arguments work too: `@$1`.

```markdown
---
argument-hint: <file>
---

Review @$1 for readability and error handling. Suggest concrete edits.
```

Several files at once:

```markdown
Compare the old and new configuration:
@config/settings.old.yaml
@config/settings.yaml
List every changed key and its impact.
```

Static references, always the same file:

```markdown
Check the code against our rules in @docs/coding-standards.md and
@docs/security-checklist.md.
```

Only files inside the workspace or the plugin folder are inlined, up to 20 000
characters each. A reference that cannot be inlined (missing file, outside
those roots) stays as literal text, so the turn can still read the file with
`read_file`. Email addresses and code spans are not treated as references.

## Inline shell output

`` !`command` `` runs a shell command before the turn and inlines its output in a
fenced block. Use it to hand fresh context to the prompt (`git status`, test
lists, versions).

```markdown
---
description: Write a commit message for the staged changes
allowed-tools: Bash(git:*)
---

Staged files:
!`git diff --cached --stat`

Diff:
!`git diff --cached`

Write a conventional commit message for these changes. Do not commit.
```

- It runs **only** when `allowed-tools` grants the shell (`Bash`, `Bash(...)` or
  `exec`). Otherwise the snippet stays as text and nothing executes.
- Working directory: the workspace. Environment: `NAVIN_PROJECT_DIR`, and
  `NAVIN_PLUGIN_ROOT` for plugin commands.
- Timeout 30 seconds; output capped at 20 000 characters. On a non-zero exit
  stderr is appended, so the turn sees the error.
- The snippet must fit on one line and contain no backtick.
- Keep these commands read-only and fast. Anything that changes state belongs
  in the instructions, where the user sees the tool call.

## Organizing commands

Flat, for a handful of commands:

```
.navin/commands/
├── review.md
├── test.md
└── release.md
```

Namespaced, for many commands:

```
.navin/commands/
├── git/
│   ├── sync.md        -> /sync
│   └── changelog.md   -> /changelog
└── docs/
    └── api.md         -> /api
```

The folder does not prefix the name, so keep stems unique across folders and
mention the group in the description ("git: rebase the branch on main").

## Best practices

- One job per command; a clear verb in the name (`/review-pr`, `/bump-version`).
- Always set `description` and, when arguments are expected, `argument-hint`.
- Grant the fewest tools; add the shell only for `!` snippets you need.
- Handle the empty-argument case and give defaults with `${N:-...}`.
- Prefer `@file` for stable context and `!` for live state.
- Keep the body under 150 lines; move long checklists into a referenced file.
- Document non-obvious commands with an HTML comment at the top; it stays in
  the prompt but is harmless:

```markdown
---
description: Deploy to an environment
argument-hint: <staging|production> [version]
---

<!--
Usage: /deploy staging 1.4.2
Requires: a clean working tree, gh authenticated.
-->

Deploy version ${2:-latest} to $1 following docs/deploy.md.
```

## Common patterns

### Review

```markdown
---
description: Review changed files
allowed-tools: Read, Grep, Bash(git:*)
---

Changed files:
!`git diff --name-only HEAD`

Review each file for bugs, security issues and missing tests. Report a table
with file, line, severity and fix.
```

### Testing

```markdown
---
description: Run tests for one file
argument-hint: <test-file>
allowed-tools: Bash(pytest:*)
---

Test output:
!`python -m pytest $1 -q`

Explain each failure and propose the smallest fix.
```

### Documentation

```markdown
---
description: Document a module
argument-hint: <source-file>
---

Write reference documentation for @$1: purpose, public functions with
parameters and return values, one usage example each.
```

### Workflow

```markdown
---
description: Prepare a release
argument-hint: <version>
allowed-tools: Bash(git:*), Read, Edit
---

Current tag: !`git describe --tags --abbrev=0`

1. If $1 is empty, ask the user for the version with ask_user and stop.
2. Update the version in pyproject.toml to $1.
3. Add a CHANGELOG entry from the commits since the current tag.
4. Show the diff and wait for confirmation before tagging.
```

## Interactive commands

When a choice cannot be guessed, tell Navin to ask with `ask_user` instead of
assuming:

```markdown
---
description: Configure the review strictness of this project
---

Ask the user with ask_user: "Which strictness level?" with the options
lenient, normal, strict. Then write the answer to
.navin/review.local.md as `strict: <answer>` and confirm.
```

Ask few, closed questions, and only when the answer changes what happens. More
patterns: `references/interactive-commands.md`.

## Commands in plugins

Put files in `<plugin>/commands/` (next to `plugin.json` at the pack root).
Use `${NAVIN_PLUGIN_ROOT}` for every path inside the plugin: Navin replaces it
in the body, in `@` references and in the environment of inline shell
commands. Never hard-code `~/.navin/plugins/...`.

```markdown
---
description: Analyze the project with the plugin script
allowed-tools: Bash(python:*), Read
---

Analysis:
!`python "${NAVIN_PLUGIN_ROOT}/scripts/analyze.py" .`

Report template: @${NAVIN_PLUGIN_ROOT}/templates/report.md

Fill the template with the analysis above.
```

Typical uses of the plugin root:

```markdown
Run the plugin script: !`bash ${NAVIN_PLUGIN_ROOT}/scripts/check.sh`
Load plugin configuration: @${NAVIN_PLUGIN_ROOT}/config/defaults.json
Use the plugin template: @${NAVIN_PLUGIN_ROOT}/templates/pr.md
Read a plugin resource on demand: ${NAVIN_PLUGIN_ROOT}/docs/rules.md
```

Plugin layout:

```
my-plugin/
├── plugin.json
├── commands/
│   ├── analyze.md       -> /analyze or /my-plugin:analyze
│   └── report.md
├── scripts/
│   └── analyze.py
└── templates/
    └── report.md
```

Users who have another `/analyze` can always type `/my-plugin:analyze`.

### Script and template patterns

```markdown
---
description: Validate the configuration with the plugin validator
argument-hint: <config-file>
allowed-tools: Bash(bash:*)
---

!`bash "${NAVIN_PLUGIN_ROOT}/scripts/validate.sh" "$1"`

Explain each reported error and how to fix it.
```

```markdown
---
description: Generate a report from the plugin template
argument-hint: <topic>
---

Using the structure of @${NAVIN_PLUGIN_ROOT}/templates/report.md, write a
report about $ARGUMENTS and save it to reports/$1.md.
```

Details: `references/plugin-features-reference.md`. Before publishing, read
`references/marketplace-considerations.md`.

## Integration with other components

### Agents

```markdown
---
description: Deep review with the plugin reviewer agent
argument-hint: <path>
---

Use spawn with agent=code-reviewer to review $1. Ask it for findings with file,
line and severity, then summarize them for the user.
```

### Skills

```markdown
---
description: Document the public API
---

Load the technical-writer skill with the skill tool and follow it to document
the public API of this package.
```

### Hooks

Commands and hooks do not call each other directly. Coordinate through a file:
the command writes `.navin/<plugin>.local.md`, a `command` hook (the only hook
type in Navin) reads it on its next event. See the plugin-settings skill.

### Multi-component workflow

```markdown
---
description: Full quality pass
allowed-tools: Bash(bash:*), Read
---

Lint results: !`bash "${NAVIN_PLUGIN_ROOT}/scripts/lint.sh"`

1. Spawn agent=test-writer for uncovered modules.
2. Load the code-reviewer skill and review the result.
3. Write a summary to .navin/quality-report.md.
```

More patterns: `references/advanced-workflows.md`.

## Validation patterns

### Argument validation

```markdown
---
argument-hint: <staging|production>
---

If "$1" is not exactly staging or production, reply with the usage
"/deploy <staging|production>" and stop. Otherwise deploy to $1.
```

### File existence

```markdown
---
argument-hint: <file>
allowed-tools: Bash(test:*)
---

Check: !`test -f "$1" && echo found || echo missing`

If the check says missing, tell the user the file $1 does not exist and stop.
Otherwise review it.
```

### Plugin resource validation

```markdown
---
allowed-tools: Bash(test:*)
---

!`test -x "${NAVIN_PLUGIN_ROOT}/scripts/run.sh" && echo ok || echo "run.sh missing"`

If the line above is not ok, report that the plugin install is broken and stop.
```

### Error handling

```markdown
---
allowed-tools: Bash(make:*)
---

Build output:
!`make build 2>&1 || echo "BUILD FAILED"`

If the output contains BUILD FAILED, explain the first error and suggest a fix.
Do not continue to the deploy step.
```

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `/name` does nothing special | a built-in command has the same name; rename the file |
| Wrong file runs | a project or personal command shadows it; use `/<plugin>:<name>` |
| `!` snippet shown as text | `allowed-tools` does not grant `Bash`/`exec` |
| `@file` not inlined | path outside workspace or plugin root, or file missing |
| Arguments ignored | no placeholder used; they arrive as an `Arguments:` line |
| Command not listed | empty body, file over 128 KB, or named README.md |

## Testing

1. Create the file, then run `/<name>` with no argument, one argument, and a
   quoted multi-word argument.
2. Check the expanded prompt at the top of the turn (`[Command /name ...]`).
3. Confirm `!` snippets ran only with the shell granted.
4. Validate a plugin with the `plugin-validator` agent, or with the validator
   script shipped in the `plugin-structure` skill.

The full checklist is in `references/testing-strategies.md`.

## Additional resources

- `references/frontmatter-reference.md` - every field, YAML rules, tool mapping
- `references/advanced-workflows.md` - multi-step, state, agents, skills
- `references/interactive-commands.md` - asking the user with ask_user
- `references/documentation-patterns.md` - usage blocks, help output
- `references/plugin-features-reference.md` - plugin root, namespacing, components
- `references/marketplace-considerations.md` - portability and distribution
- `references/testing-strategies.md` - test matrix and debugging
- `examples/simple-commands.md` - complete standalone commands
- `examples/plugin-commands.md` - commands that use plugin files and scripts
