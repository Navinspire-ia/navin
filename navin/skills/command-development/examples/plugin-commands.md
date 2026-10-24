# Plugin command examples

Files live in `<plugin>/commands/`. Every plugin path goes through
`${NAVIN_PLUGIN_ROOT}`.

## commands/lint-docs.md - run a bundled script

```markdown
---
description: Lint the Markdown docs with the plugin rules
argument-hint: [folder]
allowed-tools: Bash, Read
---

Lint the documentation in ${1:-docs}.

Result of the bundled linter:
!`python3 "$NAVIN_PLUGIN_ROOT/scripts/lint_docs.py" "${1:-docs}"`

Group the findings by file, fix the ones that are pure formatting, and list
the others for the user.
```

## commands/new-adr.md - template from the plugin

```markdown
---
description: Create an architecture decision record
argument-hint: <title>
allowed-tools: Read, Write, list_dir
---

Create a new ADR titled "$ARGUMENTS".

Template:
@${NAVIN_PLUGIN_ROOT}/templates/adr.md

1. Number it after the highest file in docs/adr/.
2. Fill the template; leave "Consequences" as questions if unknown.
3. Print the new path.
```

## commands/release/notes.md - namespaced, uses an agent

```markdown
---
description: (release) Draft release notes from merged pull requests
argument-hint: <from-tag> [to-ref]
allowed-tools: Bash(git:*), spawn, Read
---

Draft release notes from $1 to ${2:-HEAD}.

Commits:
!`git log --merges --oneline "$1..${2:-HEAD}"`

Use spawn with agent="changelog-writer" (shipped in this plugin's agents/)
to group the changes by area, then format them with
@${NAVIN_PLUGIN_ROOT}/templates/release-notes.md.
```

It is invoked as `/notes v1.2.0` or, to bypass a clash, `/<plugin>:notes v1.2.0`.
