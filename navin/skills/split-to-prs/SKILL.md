---
name: split-to-prs
description: "Split a large branch or a pile of uncommitted work into small, reviewable pull requests on GitHub, GitLab or Forgejo: snapshot to a backup ref first, propose slices, wait for approval, then branch, stage named files or hunks, commit, push and open PRs with forge pr_create. Use for 'split this into PRs', 'break this branch up', 'découpe en plusieurs PR'."
metadata: {"navin":{"emoji":"🪚","category":"devops","requires":{"bins":["git"]}}}
---

# Split work into PRs

Turn one big change (a long branch, uncommitted edits, or both) into a few pull
requests that each make sense on their own.

Tools: `git` (status, diff, log, switch, add, commit, push, stash), `exec` only for
what the git tool does not model (backup ref, hunk staging), and `forge` action
`pr_create` for the requests.

## Hard rules

- **No branch, commit, push or PR until the user approves the plan.** Inspecting is fine.
- Never discard work. No `reset --hard`, `checkout -- .`, `clean`, `branch -D`,
  force push or `stash drop` without the user's explicit approval for that exact command.
- Recoverable snapshot first, before the first write:
  ```bash
  ts=$(date +%Y%m%d-%H%M%S)
  snap=$(git stash create)            # empty output means the tree is clean
  git update-ref "refs/backup/pre-split-$ts" "${snap:-HEAD}"
  ```
  `git stash create` records the uncommitted work as a commit without touching the
  tree or the stash list. Tell the user the ref name; it is how they get everything back
  (`git stash apply refs/backup/pre-split-<ts>` or `git switch -c rescue refs/backup/pre-split-<ts>`).
- Stage only the files or hunks named in the approved slice. Never `git add -A`,
  `git add .` or `commit -a`.
- Keep the backup ref at the end. Only the user deletes it.

## 1. Check the state

1. Default branch: `forge` reads it for PRs; locally `git symbolic-ref refs/remotes/origin/HEAD`
   (fall back to `main` / `master`). `git fetch` first so the comparison is fresh.
2. What differs from it: `git log --oneline <default>..HEAD`, `git diff --stat <default>...HEAD`,
   plus uncommitted work (git action=status, git action=diff, staged and unstaged).
3. Ownership signals: `CODEOWNERS` (`.github/`, `.gitlab/`, root, `docs/`), top-level
   folders, packages in a monorepo. Changes owned by different reviewers usually belong
   in different PRs.

## 2. Propose slices

- One line per slice: a PR title, and a one-line scope when the title alone is unclear.
- List the files (and, when a file is shared, which hunks) in each slice.
- Default: **independent PRs**, each based on the default branch and mergeable in any order.
- Stack (PR 2 based on PR 1's branch) only for a real dependency (PR 2 does not build or
  pass tests without PR 1). Say why.
- Several slices: add a small diagram of the slices and their dependencies (archify
  HTML+SVG when the user wants a visual, otherwise a short mermaid block).
- Name what stays out (debug files, unrelated edits) as "leftovers".
- Ask: "OK to go with this plan?" and wait.

## 3. Execute (after approval)

For each slice, in dependency order:

1. `git action=switch create=true name=<branch> commit=<base>` (base = default branch,
   or the previous slice's branch when stacked).
2. Bring the slice's changes in:
   - whole files from the snapshot: `git checkout refs/backup/pre-split-<ts> -- <paths>`
     (exec), or from the original branch: `git checkout <source-branch> -- <paths>`;
   - part of a file: write the slice's hunks to a patch file and stage only them:
     ```bash
     git diff <base> refs/backup/pre-split-<ts> -- path/file.py > /tmp/slice-1.patch
     # edit the patch down to the hunks of this slice, then:
     git apply --cached --check /tmp/slice-1.patch && git apply --cached /tmp/slice-1.patch
     ```
     The git tool's `add` stages whole paths; `git apply --cached` through exec is the
     non-interactive hunk staging (never `add -p`, it hangs without a TTY).
3. `git action=add paths=[...]` for whole files, `git action=diff staged=true` to check
   exactly what is staged, then `git action=commit message="..."`.
4. Run the project's quick checks (build / tests) when they exist.
5. `git action=push set_upstream=true`, then `forge action=pr_create title=... body=...
   base=<base>`. The body says what the slice does and, when stacked, which PR it builds on.

After the last slice, switch back to the branch the user was on.

## 4. Report

- Each PR: title and URL, independent or stacked on which one.
- Leftovers: what was not included and where it still lives (the original branch, the
  working tree, or the backup ref).
- The backup ref name, kept on purpose.
