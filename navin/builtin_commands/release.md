---
description: Prepare a release - bump the version everywhere, update the changelog and commit chore(release)
argument-hint: "<version or patch|minor|major>"
allowed-tools: git, read_file, grep, find_files, edit_file, apply_patch, exec, test_run, ask_user
---

# Release

Requested version or bump: "$ARGUMENTS" (ask with `ask_user` when empty: patch recommended).

## 1. Preconditions

- `git action=status` must be clean and on the default branch; otherwise stop and say what blocks.
- Read the current version from the project's single source of truth (for Navin: `navin/_version.py`; otherwise `pyproject.toml`, `package.json`, `Cargo.toml`...). Compute the new version from the bump keyword, or validate the explicit one (strict semver, greater than the current).

## 2. Bump everywhere

Find every file that pins the version (`grep` for the exact current version string) and update them all in one go. For Navin that is at least: `navin/_version.py`, `pyproject.toml`, `webui/package.json` and `package-lock.json`, `desktop/package.json` and `package-lock.json`, `desktop/src-tauri/Cargo.toml`, `Cargo.lock` and `tauri.conf.json`. Edit the lock files by hand only on the project's own entry (never regenerate them). Re-run the grep to prove no occurrence of the old version is left in those manifests.

## 3. Changelog and checks

- Turn the `## [Unreleased]` section of `CHANGELOG.md` into `## [<version>] - <today>` (run `/changelog` logic first if the section is empty).
- Run the fast tests (`test_run`) and stop on any failure.

## 4. Commit

One commit `chore(release): <version>` with exactly the bumped files (`git action=add paths=[...]`, `git action=commit`). Do not tag or push: show the command the user can run or offer to do it on confirmation.
