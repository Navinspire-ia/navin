---
description: Delete the local git branches marked [gone] (removed on the remote but still present locally), including their worktrees.
---

## Task

Run the shell commands below to remove the local branches whose remote branch has been deleted.

## Commands

1. **List the branches and spot the ones marked [gone]**
   ```bash
   git branch -v
   ```
   A branch prefixed with `+` is checked out in a worktree: that worktree must be removed before the branch can be deleted.

2. **List the worktrees that belong to [gone] branches**
   ```bash
   git worktree list
   ```

3. **Remove those worktrees and delete every [gone] branch (plain and worktree branches alike)**
   ```bash
   # Every [gone] branch, without its '+' or '*' marker
   git branch -v | grep '\[gone\]' | sed 's/^[+* ]//' | awk '{print $1}' | while read branch; do
     echo "Processing branch: $branch"
     # Remove its worktree, unless it is the main checkout
     worktree=$(git worktree list | grep "\\[$branch\\]" | awk '{print $1}')
     if [ ! -z "$worktree" ] && [ "$worktree" != "$(git rev-parse --show-toplevel)" ]; then
       echo "  Removing worktree: $worktree"
       git worktree remove --force "$worktree"
     fi
     # Delete the branch
     echo "  Deleting branch: $branch"
     git branch -D "$branch"
   done
   ```

## Expected result

Once the commands have run:

- the user sees every local branch with its status;
- the worktrees of [gone] branches are removed;
- every branch marked [gone] is deleted;
- you report which worktrees and branches were removed.

If no branch is marked [gone], say that nothing needed cleaning (a `git fetch --prune` refreshes the remote state when branches were deleted recently).
