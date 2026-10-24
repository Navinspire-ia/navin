# Documentation patterns for commands

## Self-documenting header

The frontmatter is the first documentation layer: `description` says what,
`argument-hint` says how. Keep both accurate after every change.

## Usage block for empty calls

Make the command explain itself when called without arguments:

```markdown
If $ARGUMENTS is empty, reply only with this usage and stop:

Usage: /deploy-check <environment> [service]
- environment: staging or production
- service: one service name, default all
Example: /deploy-check staging api
```

## Output contract

State the shape of the answer so runs are comparable:

```markdown
Answer with:
1. A one-line verdict (PASS or FAIL).
2. A table: check, status, detail.
3. The exact commands left for the user, in a fenced block.
```

## Comments for maintainers

HTML comments are part of the prompt but cost little; use them sparingly for
maintainers:

```markdown
<!-- Keep step 3 in sync with scripts/release.sh -->
```

Prefer a `README.md` next to the commands for longer notes: Navin ignores
README files when listing commands.

## Plugin documentation

List each command in the plugin README with its arguments and one example.
The `plugin-validator` agent warns when a command has no description.
