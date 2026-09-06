---
name: git
description: "Full git workflow for developers: status, staging, commits, branches, push/pull, merge/rebase, conflict resolution, stash, history (log/blame/bisect), remotes, tags, worktrees, cherry-pick, and recovery with reflog. The everyday operations live in the git tool; this skill covers the rest. Use for any local version-control task; use the github skill (gh) only for the GitHub API (PRs, issues, CI)."
metadata: {"navin":{"emoji":"🌿","category":"devops","requires":{"bins":["git"]},"install":[{"id":"apt","kind":"apt","package":"git","bins":["git"],"label":"Install git (apt)"},{"id":"dnf","kind":"dnf","package":"git","bins":["git"],"label":"Install git (dnf)"},{"id":"pacman","kind":"pacman","package":"git","bins":["git"],"label":"Install git (pacman)"},{"id":"brew","kind":"brew","formula":"git","bins":["git"],"label":"Install git (brew)"},{"id":"winget","kind":"winget","package":"Git.Git","bins":["git"],"label":"Install git (winget)"}]}}
---

# Git Skill

Complete local version-control workflow with the `git` CLI. Works the same on Linux, macOS, Windows, and WSL. For GitHub-specific actions (PRs, issues, CI runs) use the `github` skill (`gh`) instead - everything else lives here.

Reach for the `git` **tool** first. It covers the whole everyday workflow - `status`, `diff`, `log`, `show`, `blame`, `branches`, `add`, `commit`, `restore`, `switch`, `stash`, `fetch`, `push`, `pull`, `merge`, `rebase`, `reset` - with parsed output, paths checked against the workspace, and no way for git to stop at an editor or a credential prompt. Conflicted merges and rebases are resumed through the tool too: `step=continue`, `step=abort`, `step=skip`.

The commands below are for what the tool does not model: revert, reflog, bisect, cherry-pick, tags, worktrees, remotes, and `commit --amend`.

Judgement calls, not restrictions:

- `reset --hard`, `push --force`, `clean -fd` and `branch -D` destroy work that no checkpoint covers. Use them when that is the intent, and say what is being dropped.
- Rewriting history already pushed to a shared branch breaks everyone else's clone. Prefer a revert.
- Never use interactive flags (`rebase -i`, `add -i`, `add -p`) - they hang without a TTY. Use the non-interactive equivalents below.
- Commit when the work is at a coherent point or the user asks; do not commit someone else's uncommitted changes along with yours.

## Inspect state

```bash
git status                          # working tree + staging area
git diff                            # unstaged changes
git diff --staged                   # staged changes
git log --oneline --graph -15      # recent history
git show <commit>                   # one commit's diff + message
git blame -L 20,40 path/file.py    # who last touched lines 20-40
```

## Stage and commit

```bash
git add path/file.py another/file.ts    # stage specific files (prefer over `git add .`)
git restore --staged path/file.py       # unstage
git commit -m "fix: handle empty payload in parser"
```

Multi-line commit messages without an editor:

```bash
git commit -m "$(cat <<'EOF'
feat: add terminal re-attach on panel reopen

Replays the scrollback buffer so reopening the panel restores output.
EOF
)"
```

Fix the last commit (only if not pushed): `git commit --amend --no-edit` (add files first) or `git commit --amend -m "new message"`.

## Branches

```bash
git branch -a                       # list local + remote branches
git switch -c feature/login         # create + switch
git switch main                     # switch back
git branch -d feature/login         # delete merged branch
git push -u origin feature/login    # publish and set upstream
```

## Sync with remotes

Prefer the tool: `git action=fetch`, `action=pull`, `action=push`. The shell forms below are for the cases it does not cover - inspecting divergence, listing remotes, pushing tags.

```bash
git fetch --all --prune             # update remote refs, drop deleted ones
git pull --rebase                   # update current branch without merge commits
git push                            # publish commits
git remote -v                       # list remotes
```

Diverged from remote? Inspect first: `git log --oneline HEAD..origin/main` (incoming) and `origin/main..HEAD` (outgoing).

## Merge and rebase

Prefer the tool: `action=merge name=…`, `action=rebase name=…`, then `step=continue` / `step=abort` / `step=skip`. It reports which files conflicted instead of leaving you to run `status` again.

```bash
git merge feature/login             # merge into current branch
git rebase main                     # replay current branch onto main
git rebase --abort                  # bail out of a bad rebase
git merge --abort                   # bail out of a bad merge
```

Non-interactive squash of a feature branch onto main:

```bash
git switch main && git merge --squash feature/login && git commit -m "feat: login"
```

## Resolve conflicts

1. The failing `merge`/`rebase`/`pull` call already listed the conflicted paths; `git action=status` lists them again.
2. Open each file; resolve the `<<<<<<<`/`=======`/`>>>>>>>` sections.
3. `git action=add paths=[…]` for each.
4. Continue: `git action=rebase step=continue`, or `git action=merge step=continue`.

Take one side wholesale when appropriate:

```bash
git checkout --ours path/file.lock && git add path/file.lock     # keep current branch's version
git checkout --theirs path/file.lock && git add path/file.lock   # keep incoming version
```

## Stash

```bash
git stash push -m "wip: refactor parser"   # save dirty tree
git stash list
git stash pop                              # restore most recent and drop it
git stash apply stash@{1}                  # restore without dropping
```

## Undo and recover

```bash
git restore path/file.py            # discard unstaged changes in one file (destructive - confirm first)
git revert <commit>                 # safe undo: new commit that reverses another
git reset --soft HEAD~1             # uncommit, keep changes staged
git reflog                          # every position HEAD has been at
git reset --hard HEAD@{2}           # jump back to a reflog entry (destructive - confirm first)
```

Lost commits after a bad reset/rebase are almost always recoverable through `git reflog`.

## History search and bisect

```bash
git log -S "functionName" --oneline         # commits that added/removed a string
git log --follow --oneline -- path/file.py  # history of a file across renames
git bisect start && git bisect bad && git bisect good v1.2.0
# test, then mark: git bisect good | git bisect bad ... until found; finish:
git bisect reset
```

## Cherry-pick, tags, worktrees

```bash
git cherry-pick <commit>                     # copy one commit onto current branch
git tag -a v1.3.0 -m "release 1.3.0" && git push origin v1.3.0
git worktree add ../hotfix-dir hotfix/urgent # second working dir on another branch
git worktree remove ../hotfix-dir
```

## Setup on a fresh machine

```bash
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
git config --global init.defaultBranch main
git config --global pull.rebase true
```

If `git commit` fails with "Please tell me who you are", run the two identity commands above first.

## .gitignore

Add patterns to `.gitignore` before the files are tracked. To untrack an already-committed file while keeping it on disk:

```bash
git rm --cached path/secrets.env && echo "path/secrets.env" >> .gitignore
```
