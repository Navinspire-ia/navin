# Code Review Plugin

Automated pull request review in Navin: several agents review independently, every finding is double-checked, and only high-signal issues reach you or the pull request.

## Overview

The `code-review` plugin adds the `/code-review` command. It splits a review across parallel subagents (guideline compliance, bugs in the diff, problems in the new code), then sends each candidate issue to a separate validation subagent. Anything that is not confirmed is dropped. The result is printed in the chat, or posted as inline comments on the pull request when you ask for it.

The plugin also ships the `code-reviewer` agent for reviewing local changes before a commit, and points to the `pr-autopilot` skill for answering review threads and fixing CI.

## Commands

### `/code-review`

**Arguments:** `[PR number or URL] [--comment]`. Without a number, Navin reviews the pull request of the current branch.

**How it works:**

1. **Eligibility check** (light subagent): stops if the pull request is closed, a draft, needs no review (automated or obviously correct trivial change), or already has a Navin review (a comment starting with `## Code review`). Pull requests written by Navin are still reviewed.
2. **Guideline discovery** (light subagent): returns the paths of the guideline files that apply - the root `AGENTS.md` and the other agent rule files of the repository, plus `AGENTS.md` files in the directories touched by the change.
3. **Change summary** (subagent): reads the pull request and its diff (`forge action=pr_view`).
4. **Parallel review** by 4 subagents, each given the PR title and description:
   - **agents 1 and 2** - guideline compliance; a guideline file only applies to files in its directory or below;
   - **agent 3** - obvious bugs visible from the diff alone (strongest model preset);
   - **agent 4** - problems in the introduced code: security, wrong logic (strongest model preset).
5. **Validation**: one subagent per issue, in parallel, confirms that it is real (for example that a variable really is undefined, or that a rule really applies to that file and is broken).
6. **Filtering**: every unconfirmed issue is dropped.
7. **Summary** in the chat. Without `--comment`, Navin stops here.
8. With `--comment`, Navin drafts each comment for itself first, then
9. posts **one inline comment per distinct issue** with `pr_comments action=post`.

**What counts as an issue:**

- code that will not compile or parse (syntax or type error, missing import, unresolved reference);
- code that gives a wrong result whatever the input;
- a clearly broken guideline rule that can be quoted.

**What is filtered out:**

- issues that existed before the pull request;
- code that looks wrong but is correct;
- pedantic nitpicks;
- anything a linter will catch;
- general quality concerns (coverage, security posture) unless a guideline requires them;
- issues silenced explicitly in the code (for example a lint ignore comment);
- style concerns, input-dependent "maybe" problems and subjective suggestions.

**Usage:**

```
/code-review
/code-review 142
/code-review https://github.com/acme/shop/pull/142 --comment
```

**Example:**

```
# On a PR branch, review and print the result in the chat:
/code-review

# Review PR 142 and post inline comments on it:
/code-review 142 --comment

# Navin:
# - checks the PR is open, not a draft, not already reviewed
# - runs 4 reviewers in parallel, then validates every finding
# - prints the confirmed issues, and posts them inline with --comment
```

**Sample chat summary:**

```
Code review of #142 "Add coupon support to checkout"

1. checkout/discount.py:88 - `apply_coupon` returns before saving the
   order total, so the discount is never persisted (bug).
2. checkout/api.py:12 - imports the ORM directly from a view; AGENTS.md
   says "views must go through services/" (AGENTS.md rule).
```

**Sample inline comment:**

~~~markdown
`apply_coupon` returns before `order.save()`, so the new total is lost.

```suggestion
    order.total = new_total
    order.save()
    return order
```

See https://github.com/acme/shop/blob/1f3c9a7d2e.../checkout/discount.py#L86-L90
~~~

Small self-contained fixes get a committable suggestion block. Larger fixes (6 lines or more, structural changes, several places) are described without one, and a suggestion is only posted when committing it fixes the issue completely.

**When nothing is found with `--comment`, Navin posts:**

```markdown
## Code review

No issues found. Checked for bugs and AGENTS.md compliance.
```

**No pull request?** When the branch has no pull request, Navin reviews the local change-set (`code_review action=scope`) with the same steps and only prints the summary.

## Agent: `code-reviewer`

The same plugin exposes the `code-reviewer` agent for local reviews outside a pull request. By default it reviews the uncommitted changes of the working tree, checks guidelines, bugs and significant quality problems, scores each finding from 0 to 100, and reports only those at 80 or more, grouped as Critical then Important. It never modifies files.

```
Have code-reviewer check my uncommitted changes before I commit.
```

## Best Practices

### Using `/code-review`

- Keep an `AGENTS.md` with clear, specific rules: the guideline reviewers can only enforce what is written.
- Run it on non-trivial pull requests; trivial ones are skipped anyway.
- Treat the findings as a starting point for human review, not a replacement.
- Re-run it after significant changes to the pull request.

### When to use

- pull requests with real logic changes;
- pull requests touching critical code;
- repositories with several contributors;
- projects that have written guidelines.

### When not to use

- closed or draft pull requests (they are skipped);
- automated or obviously correct trivial changes;
- urgent hotfixes that need to merge immediately;
- pull requests that already carry a Navin review.

## Workflow Integration

### Standard pull request review

```
# open a pull request
/commit-push-pr

# review it locally (chat only)
/code-review

# fix what was found, push, then publish the review
/code-review --comment

# merge when ready
```

### In CI or automation

```
# trigger on pull request creation or update
/code-review <number> --comment
# reviewed or draft pull requests are skipped automatically
```

## Requirements

- A git repository hosted on a forge Navin can reach (GitHub, GitLab or Forgejo), with a token configured for the `forge` and `pr_comments` tools.
- Optionally the `gh` CLI, authenticated (`gh auth login`), which Navin may use for read-only lookups (`gh pr view`, `gh pr diff`, `gh search`).
- Subagent support (`spawn`); model presets help (a fast one for light checks, a strong one for bug analysis).
- Optionally `AGENTS.md` files for guideline checks.

## Troubleshooting

### Review takes too long

**Cause:** many subagents run (4 reviewers plus one validator per issue), and large diffs take longer.

**Fix:** this is expected for big pull requests; configure faster model presets for the light steps, or split large pull requests.

### Too many false positives

**Fix:**

- make `AGENTS.md` rules more specific;
- remember that pre-existing issues and linter-catchable issues are excluded by design;
- check that the pull request description states the intent, since every reviewer reads it.

### No review comment posted

**Check:**

- whether `--comment` was passed (without it the review is chat only);
- whether the pull request was skipped (closed, draft, trivial, or already reviewed);
- whether the forge token allows posting reviews.

### Link formatting broken

Links must use this exact form so the forge renders them:

```
https://github.com/<owner>/<repo>/blob/<full-commit-sha>/path/to/file.ext#L4-L7
```

- the full commit sha, written out (never a shell expression);
- the repository under review;
- `#` after the file name and the range written `L<start>-L<end>`;
- at least one line of context on each side (commenting lines 5-6 links `L4-L7`).

### Forge access not working

**Fix:** check the forge token configured for Navin; if you rely on `gh`, run `gh auth status` and `gh auth login`. Navin never reads the forge through web fetching.

## Tips

- Write precise `AGENTS.md` files, and put directory-specific rules in that directory's `AGENTS.md`.
- Give the pull request a good description: reviewers use it to understand the intent.
- Run a local `/code-review` before `--comment` to see what would be posted.
- Each review step is put on the project board, so you can follow its progress.

## Configuration

### Adjusting strictness

`/code-review` does not use a numeric threshold: an issue is kept only when a validation subagent confirms it. The standalone `code-reviewer` agent uses a confidence threshold of 80. To change either behaviour, copy the command or the agent into your project's `.navin/` folder (or `~/.navin/plugins/code-review/`) and adapt it.

### Customising the review focus

In your override you can add security-focused reviewers, performance checks, or extra false-positive rules for your codebase.

## Technical Details

### Agent architecture

- 1 eligibility checker, 1 guideline locator, 1 summariser;
- 4 parallel reviewers (2 guideline, 1 diff bug, 1 new-code problems);
- N parallel validators, one per candidate issue.

### Filtering

Instead of a confidence score, every candidate goes through an independent validation; anything not confirmed with high confidence is dropped, and duplicates are never posted.

### Forge integration

- `forge action=pr_view` to read the pull request and its diff;
- `pr_comments action=post` for inline comments;
- `forge action=pr_review event=comment` for the "no issues" summary;
- optional read-only `gh` commands (`gh pr view`, `gh pr diff`, `gh pr list`, `gh issue view`, `gh issue list`, `gh search`).

## Version

1.0.0
