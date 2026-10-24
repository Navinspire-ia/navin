# Advanced command workflows

## Multi-step commands

Number the steps and give each one an observable result. Navin follows the
order and can report where it stopped.

```markdown
---
description: Prepare a release branch
argument-hint: <version>
allowed-tools: Bash(git:*), Read, Edit
---

Prepare release $1.

1. Stop and ask for the version with ask_user if $1 is empty or not semver.
2. Run `git status --porcelain`; stop if the tree is dirty.
3. Update the version in pyproject.toml and package.json.
4. Prepend a CHANGELOG section for $1 built from `git log --oneline <last-tag>..HEAD`.
5. Report the changed files and the next command for the user: `git tag v$1`.
```

## Conditional behaviour from arguments

Branch in plain language; Navin evaluates it:

```markdown
If $1 is "quick", only lint the changed files.
If $1 is "full" or empty, lint, type-check and run the whole test suite.
Any other value: list the two modes and stop.
```

## Pre-computed context

Inline cheap, read-only facts with `` !`cmd` `` so the turn does not spend
tool calls discovering them:

```markdown
Current branch: !`git branch --show-current`
Failing tests from the last run: !`cat .navin/last-failures.txt 2>/dev/null || echo none`
```

## State between runs

Commands are stateless. Keep state in a file the command reads and updates:

- per project: `.navin/<plugin>.local.md` (see the `plugin-settings` skill)
- run logs: `.navin/<command>-history.md`

Inline it with `@.navin/<command>-history.md` and end the body with "Append one
line to .navin/<command>-history.md describing this run".

## Handing work to agents and skills

- Load a playbook first: "Read the skill `hook-development` before writing
  hooks.json".
- Delegate an isolated job: "Use spawn with agent=\"skill-reviewer\" on each
  folder under skills/ and merge the reports".
- Parallel work: "Spawn one subagent per package in the same reply".

Keep the command as the orchestrator: it decides the order, the agents do the
heavy reading.

## Chaining commands

A command cannot invoke another slash command directly. Factor the shared part
into a referenced file (`@${NAVIN_PLUGIN_ROOT}/shared/checklist.md`) or into a
skill both commands load.

## Long checklists

Put them in `references/` of a skill or in a plugin file and inline or cite
them. The command body stays a short procedure; the checklist evolves without
touching it.
