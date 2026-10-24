# Commit Commands Plugin

Three commands that take the routine out of everyday git work: commit, ship a pull request, and clean up stale branches.

## Overview

The `commit-commands` plugin lets Navin handle the repetitive parts of a git workflow. It reads the state of the repository, writes a commit message that matches your history, and talks to your forge (GitHub, GitLab or Forgejo) through Navin's `git` and `forge` tools. For anything beyond these three commands, the plugin points to the `git` and `github` skills.

## Commands

### `/commit`

Creates exactly one commit from the current changes.

**What it does:**

1. Collects context: `git status`, `git diff HEAD`, the current branch and the last 10 commits.
2. Stages the relevant files with `git action=add`, never staging secrets (`.env`, credentials, private keys).
3. Commits with `git action=commit`, using a message that follows the style of your recent commits.

Staging and committing happen in a single reply, with no other tool calls and no extra chat.

**Usage:**

```
/commit
```

**Example:**

```
# You changed src/parser.py and its test, then:
/commit

# Navin:
# - reads the diff and the recent history ("fix(parser): ...", "feat(cli): ...")
# - stages src/parser.py and tests/test_parser.py
# - commits: "fix(parser): handle empty input without raising"
```

**Highlights:**

- messages match the convention already used in the repository;
- secrets are left out of the commit;
- one commit per run, nothing pushed.

### `/commit-push-pr`

Goes from local changes to an open pull request in one step.

**What it does:**

1. If you are on the default branch (`main` or `master`), creates a feature branch with a short descriptive name (`git action=switch create=true`).
2. Creates one commit with a fitting message (secrets are never staged).
3. Pushes the branch to `origin` and sets the upstream (`git action=push set_upstream=true`).
4. Opens the pull request with `forge action=pr_create`, with a clear title and a body made of:
   - a `## Summary` of 1 to 3 bullets covering every commit of the branch;
   - a `## Test plan` checklist.

All of this happens in a single reply.

**Usage:**

```
/commit-push-pr
```

**Example:**

```
# On main, with uncommitted work on a retry helper:
/commit-push-pr

# Navin:
# - creates branch "add-http-retry"
# - commits "feat(http): retry idempotent requests with backoff"
# - pushes to origin with upstream
# - opens the PR and returns its URL
```

Sample pull request body:

```markdown
## Summary
- Add a retry helper with exponential backoff for idempotent HTTP calls
- Use it in the sync client

## Test plan
- [ ] Unit tests for the backoff schedule pass
- [ ] Sync against a flaky test server succeeds
```

**When to use it:** when the work is ready for review and you want branch, commit, push and PR done together.

### `/clean_gone`

Deletes the local branches whose remote branch is gone, together with their worktrees.

**What it does:**

1. Runs `git branch -v` to list branches and spot the ones marked `[gone]`.
2. Runs `git worktree list` to find worktrees attached to those branches.
3. For each `[gone]` branch: removes its worktree (unless it is the main checkout) with `git worktree remove --force`, then deletes the branch with `git branch -D`.
4. Reports which worktrees and branches were removed.

If no branch is marked `[gone]`, Navin says nothing needed cleaning and suggests `git fetch --prune` to refresh the remote state.

**Usage:**

```
/clean_gone
```

**Example:**

```
/clean_gone

Processing branch: fix-login-redirect
  Deleting branch: fix-login-redirect
Processing branch: feature-export
  Removing worktree: /home/me/wt/feature-export
  Deleting branch: feature-export

Removed 1 worktree and 2 branches.
```

**When to use it:** after pull requests are merged and their remote branches deleted.

## Best Practices

### Using `/commit`

- Look over your changes before running it.
- Let Navin match your message convention; rename afterwards with `git commit --amend` if needed.
- Use it often for small, focused commits.

### Using `/commit-push-pr`

- Run it only when the change is ready for review.
- Check the PR description afterwards and complete the test plan.
- Make sure the branch contains only related commits, since the summary covers all of them.

### Using `/clean_gone`

- Run it regularly to keep your branch list short.
- Run `git fetch --prune` first if remote branches were deleted recently.
- It is especially useful after a batch of merged pull requests.

## Workflow Integration

### Quick commit workflow

```
# write code
/commit
# keep going
```

### Feature branch workflow

```
# work on the feature
/commit
# more changes
/commit
# ready for review
/commit-push-pr
```

### Maintenance workflow

```
# several pull requests merged
/clean_gone
# clean branch list for the next feature
```

## Requirements

- A git repository with a configured `origin` remote.
- For `/commit-push-pr`: a forge Navin can reach (GitHub, GitLab or Forgejo) with a token configured for the `forge` tool. The `gh` CLI is not required for this plugin.
- For `/clean_gone`: a shell with `grep`, `sed` and `awk`.

## Troubleshooting

### `/commit` creates no commit

**Cause:** there are no changes, or only files that should not be committed (secrets).

**Fix:** check `git status` and make sure there are real changes to commit.

### `/commit-push-pr` fails to open the pull request

**Cause:** missing forge credentials, no push rights, or no `origin` remote.

**Fix:**

- check that a forge token is configured for Navin;
- check `git remote -v` and that you can push to that remote;
- if the branch was pushed, open the pull request again with the `forge` tool once the access problem is fixed.

### `/clean_gone` finds no branches

**Cause:** the local view of the remote is outdated.

**Fix:** run `git fetch --prune`, then run `/clean_gone` again.

## Tips

- Chain them: `/commit` while you work, `/commit-push-pr` when you are done, `/clean_gone` after the merge.
- Leave sensitive files out of git with `.gitignore`; Navin will not stage them anyway.
- Commit history style is learned from the last 10 commits, so a consistent history gives better messages.

## Plugin contents

| Item | Kind |
|---|---|
| `/commit`, `/commit-push-pr`, `/clean_gone` | commands |
| `git`, `github` | related skills |

Custom versions of this plugin can live in `~/.navin/plugins/commit-commands/` (personal) or the project's `.navin/` folder.

## Version

1.0.0
