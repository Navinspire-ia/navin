# Commit convention (humans and agents)

Pull request **titles** are Conventional Commits. GitHub squash-merge uses
the PR title as the git subject, and that subject is what the release bot
reads to bump SemVer.

WIP commits on a branch may be messy. They disappear on squash.

The human guide is [CONTRIBUTING.md](../CONTRIBUTING.md). The enforcer is
`scripts/commit_convention.py`.

## Humans

Set the PR title to:

```
<type>(<optional-scope>)!: <subject>
```

Use the pull request template. If the title has `!`, fill `BREAKING CHANGE:`
and **Migration**, and apply the `semver:major` label.

## Agents

MUST run before `gh pr create` or `gh pr edit --title`:

```bash
python3 scripts/commit_convention.py check-title \
  --title "$TITLE" \
  --body-file /tmp/pr-body.md \
  --labels "$LABELS"
```

MUST NOT:

- open a PR whose title is not a Conventional Commit
- mark a PR breaking (`type!:`) without a `BREAKING CHANGE:` footer, a
  Migration section, and the `semver:major` label
- rewrite history on `main` to make old commits conventional

Discover the next version from this branch:

```bash
python3 scripts/commit_convention.py next-version
```

Contract checks (no git, no GitHub):

```bash
python3 scripts/commit_convention.py --self-test
```

## Title

- Types: `feat` `fix` `perf` `refactor` `docs` `test` `build` `ci` `chore` `revert`
- Scope is optional, kebab-case (`webui`, `desktop`, `git-import`, ...). Not a closed list.
- Subject is imperative, lowercase after the colon, no trailing period.
- Whole title is at most 72 characters. A closing `(#1234)` is allowed.
- `!` before the colon marks a breaking change.

## Version mapping

Highest bump in the unreleased window wins.

| Marker | Bump |
| --- | --- |
| `BREAKING CHANGE:` footer or `type!:` | major |
| `feat:` | minor |
| `fix:` or `perf:` | patch |
| anything else conventional | none |
| non-conventional subjects | ignored |

Features never bump major on their own.

## Breaking PRs

All three are required:

1. Title uses `type!:`
2. Body footer `BREAKING CHANGE: <what the user must do>`
3. Label `semver:major`

The release bot still computes `v(X+1).0.0`. The `chore(release): vX.Y.Z`
PR is never auto-merged.
