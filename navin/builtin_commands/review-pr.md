---
description: Review the current change or pull request with specialised agents (code, tests, errors, types, comments, simplify)
argument-hint: "[comments|tests|errors|types|code|simplify|all] [parallel]"
allowed-tools: spawn, code_review, git, forge, read_file, grep, find_files, board, Bash(git diff:*), Bash(git status:*), Bash(gh pr view:*), Bash(gh pr diff:*)
---

# Pull request review

Review the change with several specialised agents, each one looking at a single aspect of quality, then merge their reports into one action plan.

**Requested aspects (optional):** "$ARGUMENTS"

## Workflow

1. **Find the scope**
   - Get the change-set with `code_review action=scope` (or `git diff --name-only` and `git status`).
   - Check whether the branch already has a pull request (`forge action=pr_view`, or `gh pr view`). If it does, review that pull request's diff.
   - Read the arguments to see which aspects were asked for. Default: every aspect that applies.

2. **Aspects**

   - **comments** - accuracy and long-term value of code comments
   - **tests** - quality and completeness of the test coverage
   - **errors** - silent failures and weak error handling
   - **types** - type design and invariants (when types were added or changed)
   - **code** - general review against the project guidelines and for bugs
   - **simplify** - simplify the code for clarity, keeping its behaviour
   - **all** - every applicable aspect (default)

3. **Decide which agents apply**, from the changed files:
   - **always**: `code-reviewer` (general quality);
   - **test files changed or new behaviour without tests**: `pr-test-analyzer`;
   - **comments or docstrings added or changed**: `comment-analyzer`;
   - **error handling changed** (try/except, catch, fallbacks, retries): `silent-failure-hunter`;
   - **types added or changed** (classes, models, schemas, interfaces): `type-design-analyzer`;
   - **after the review has passed**: `code-simplifier` (polish). It edits files, so run it only when `simplify` is asked for, or once no critical issue is left.

   Put one board task per agent you will run.

4. **Run the agents** with `spawn agent=<name>`, giving each the list of changed files (or the pull request number) and the pull request title and description when there is one.

   - **Sequential (default)**: one agent at a time. Each report is complete before the next starts, which is easier to act on in an interactive review.
   - **Parallel** (when `parallel` is in the arguments): start every review agent in the same reply. Faster for a full review; the reports come back together. `code-simplifier` still runs last, alone.

5. **Merge the results** once the agents are done:
   - **Critical issues** (must fix before merge)
   - **Important issues** (should fix)
   - **Suggestions** (nice to have)
   - **Strengths** (what is done well)

   Drop duplicates reported by several agents, and keep the `file:line` of each finding.

6. **Give the action plan** in this shape:

   ```markdown
   # PR review summary

   ## Critical issues (X found)
   - [agent-name]: issue description [file:line]

   ## Important issues (X found)
   - [agent-name]: issue description [file:line]

   ## Suggestions (X found)
   - [agent-name]: suggestion [file:line]

   ## Strengths
   - what this change does well

   ## Recommended action
   1. Fix the critical issues first
   2. Address the important issues
   3. Consider the suggestions
   4. Re-run the review after the fixes
   ```

## Usage examples

**Full review (default):**
```
/review-pr
```

**Specific aspects:**
```
/review-pr tests errors
# reviews only the test coverage and the error handling

/review-pr comments
# reviews only the code comments

/review-pr simplify
# simplifies the code once the review has passed
```

**Parallel review:**
```
/review-pr all parallel
# starts every review agent at once
```

## The agents

**comment-analyzer**: checks comments against the code, finds comment rot, checks documentation completeness.

**pr-test-analyzer**: reviews behavioural coverage, finds critical gaps, rates test quality.

**silent-failure-hunter**: finds silent failures, reviews catch blocks, checks error logging.

**type-design-analyzer**: analyses encapsulation and invariant expression, rates the type design.

**code-reviewer**: checks the project guidelines (AGENTS.md and the other agent rule files), finds bugs, reviews general quality.

**code-simplifier**: simplifies complex code, improves clarity, applies the project standards, keeps the behaviour.

## Tips

- **Run it early**: before opening the pull request, not after.
- **Stay on the change**: agents analyse the diff by default.
- **Critical first**: fix the high-priority findings before the rest.
- **Re-run after fixes**: confirm the issues are gone.
- **Target an aspect** when you already know the concern.

## Workflow integration

**Before a commit:**
```
1. Write the code
2. /review-pr code errors
3. Fix the critical issues
4. /commit
```

**Before a pull request:**
```
1. Stage the changes
2. /review-pr all
3. Fix the critical and important issues
4. Re-run the targeted reviews
5. /commit-push-pr
```

**After review feedback:**
```
1. Make the requested changes
2. Re-run the reviews that match the feedback
3. Check the issues are resolved
4. Push
```

## Notes

- The agents work on their own and return complete reports with `file:line` references.
- Each agent stays on its specialty, so its analysis goes deep.
- Only `code-simplifier` edits files; the other agents are read-only.
- `/code-review` is the other review workflow: it validates every finding with a second agent and can post inline comments on the pull request.
