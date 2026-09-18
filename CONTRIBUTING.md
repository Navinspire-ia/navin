# Contributing

Thanks for wanting to improve Navin.

## Before you start

1. Read [Installation](./docs/Installation.md) (source build) and [docs/README.md](./docs/README.md).
2. Run `navin doctor` (or `.venv/bin/navin doctor`) after `make install`.
3. Keep user data out of issues: no API keys, tokens, or `config.json` dumps.

## Local loop

```bash
make install
make -C webui install
sh scripts/start.sh
# gateway: http://127.0.0.1:8765
# WebUI:   http://127.0.0.1:5173
```

```bash
.venv/bin/navin-cli
make logs
sh scripts/stop.sh
```

The product CLI is `navin-cli`. Do not document `navin tui` or `navin agent` as the entry point.

## What we look for

- Agent runtime, CLI, Skills, MCP, providers
- Memory, world model, policy learning, evaluations
- Integrations, UI, documentation, bug fixes

Match existing style. Do not invent extra CLI surface or rewrite `--help` unless the task is the help text itself.

## Docs and copy

- No Unicode em dash (U+2014) or en dash (U+2013). Use `-` or rephrase.
- User-facing install commands must stay the official ones:

```bash
curl https://navin.live/install -fsS | bash
```

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

## Issue intake

Open issues through the GitHub chooser; blank issues are disabled.
Templates auto-apply one kind label (`bug`, `regression`, `enhancement`,
`support`, `documentation`, `security`).

| Template | When |
| --- | --- |
| Bug | Defect on a specific platform or installer |
| Regression | Used to work, then stopped |
| Feature | New capability, mode, or product surface |
| Support | Question, debugging help, workflow advice |
| Docs | Doc fix, clarification, missing coverage |
| Security | Email `security@navinspire.com` and see [SECURITY.md](./SECURITY.md) |

Triagers add the rest of the labels during triage: `area:*`, `platform:*`,
`packaging:*`, plus `severity`, `priority`, and `status`. There is no need
to memorize or paste the full label set.

Never paste API keys, tokens, license files, or `~/.navin/config.json` in a
report. For vulnerabilities, see [SECURITY.md](./SECURITY.md) and email
`security@navinspire.com` first.

Agents (and anyone using the GitHub API) MUST open issues with
`python3 scripts/new-issue.py` instead of `gh issue create`. The browser
forms are not applied over the API. See [`.github/ISSUE_INTAKE.md`](.github/ISSUE_INTAKE.md).


## Pull requests

- Small, reviewable diffs.
- Say why, not only what.
- If you change UI behavior, say how you verified it.
- If you change install or CLI docs, keep [docs/Installation.md](./docs/Installation.md) and [docs/cli/install.md](./docs/cli/install.md) in sync.
- Title the PR as a [Conventional Commit](https://www.conventionalcommits.org/en/v1.0.0/). GitHub squash-merge uses that title as the git subject, and the release bot reads it to bump SemVer. See [Commit messages and releases](#commit-messages-and-releases).

Open the PR against the branch the maintainers use for this repository.

## Commit messages and releases

Format:

```
<type>(<optional-scope>)!: <subject>
```

- **Types:** `feat` `fix` `perf` `refactor` `docs` `test` `build` `ci` `chore` `revert`
- **Scope:** optional, kebab-case (`webui`, `desktop`, `cli`, `gateway`, `session`, `git-import`, `packaging`, ...). Drop it when the change spans areas. Not a closed list.
- **Subject:** imperative, lowercase after the colon, no trailing period. Whole title at most 72 characters. A closing `(#1234)` is allowed.
- WIP commits on a feature branch may be messy. They disappear on squash.

Highest bump in the unreleased window wins:

| Marker | Bump | Example |
| --- | --- | --- |
| `BREAKING CHANGE:` footer, or `type!:` | **major** (`2.5.1` to `3.0.0`) | public contract break |
| `feat:` | **minor** (`2.5.1` to `2.6.0`) | user-visible capability |
| `fix:` or `perf:` | **patch** (`2.5.1` to `2.5.2`) | bug fix or speed win |
| `docs` `test` `ci` `build` `chore` `refactor` `style` `revert` | **none** | no release by themselves |

A window with ten `fix:` commits and one `feat:` is a minor. A window with ten `feat:` commits and one breaking commit is a major. Features never bump major on their own. Non-conventional subjects are ignored (this applies forward only; do not rewrite `main`).

Body (required for `feat`, `fix`, `perf`, and any breaking change):

- Blank line after the subject. Wrap near 72 columns.
- What changed and why.
- For `fix:` include the symptom in the past tense so the changelog can quote it.
- One GitHub keyword per line: `Closes #1234` or `Refs #1234`.

Breaking changes need all three:

1. `!` before the colon (`feat!:`, `fix!:`, `refactor!:`)
2. A footer `BREAKING CHANGE: <what the user must do>`
3. The `semver:major` label on the PR

A breaking change is anything that requires the user or a script to take an action to keep working: removing or renaming a CLI flag, env var, HTTP route, or documented config field; changing a documented default; changing the persisted session format so older Navin cannot read it; changing a public Python / TS / Rust API exported from `navin.*` or `@navin/*`; dropping a previously supported OS, Python version, or install channel. Internal renames with a compat alias in the same commit are not breaking.

The release bot still computes `v(X+1).0.0` from that history. The resulting `chore(release): vX.Y.Z` PR is **never auto-merged**. A maintainer merges it. Desktop files that `scripts/set-version.sh` stamps may need that script if they lag `pyproject.toml`. GitHub's built-in `GITHUB_TOKEN` cannot open pull requests here; the workflow uses repo secret `RELEASE_PLEASE_TOKEN` (a PAT with `contents` and `pull requests`).

Check a title locally:

```bash
python3 scripts/commit_convention.py check-title --title "fix(git-import): run git clone without the bundled libssl"
python3 scripts/commit_convention.py --self-test
python3 scripts/commit_convention.py next-version
```

Agents MUST run `scripts/commit_convention.py check-title` before opening or editing a PR. Contract: [`.github/COMMIT_CONVENTION.md`](.github/COMMIT_CONVENTION.md).

Baseline: tag `v2.0.4` on the already-shipped 2.0.4 commit so the first generated changelog only contains later conventional PRs.

