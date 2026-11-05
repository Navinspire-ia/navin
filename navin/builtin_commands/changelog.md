---
description: Write the changelog entry for the commits since the last tag (or a given ref)
argument-hint: "[base ref or tag, default: the latest tag]"
allowed-tools: git, read_file, edit_file, apply_patch, write_file, Bash(git describe:*), Bash(git log:*), Bash(git tag:*)
---

## Context

- Latest tag: !`git describe --tags --abbrev=0 2>/dev/null || echo "(no tag)"`
- Commits since the latest tag: !`git log --no-merges --pretty=format:'%h %s' $(git describe --tags --abbrev=0 2>/dev/null)..HEAD 2>/dev/null || git log --no-merges --pretty=format:'%h %s' -50`
- Existing changelog head: @CHANGELOG.md

## Task

Base requested by the user (may be empty, then use the latest tag): "$ARGUMENTS"

1. Group the commits by their conventional prefix: `feat` -> **Added**, `fix` -> **Fixed**, `perf` / `refactor` -> **Changed**, `docs` -> **Documentation**, breaking changes (`!` or `BREAKING CHANGE`) -> **Breaking** first. Drop `chore(release)`, merge commits and pure CI noise.
2. Rewrite each line for a user, not a developer: what changed and why it matters, one line, no hash, no scope prefix. Merge commits that describe the same change.
3. Insert the entry at the top of `CHANGELOG.md` under `## [Unreleased]` (Keep a Changelog format, create the file if it does not exist). Do not change the previous entries.
4. Show the entry in the answer. Do not commit.
