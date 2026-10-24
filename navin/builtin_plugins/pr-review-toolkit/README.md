# PR Review Toolkit

A set of specialised Navin agents for a thorough pull request review: code comments, test coverage, error handling, type design, code quality and simplification.

## Overview

The plugin bundles 6 expert review agents, each focused on one aspect of quality, and the `/review-pr` command that runs them and merges their reports. Use one agent for a targeted review, or `/review-pr` for a complete one.

## Commands

### `/review-pr`

**Arguments:** `[comments|tests|errors|types|code|simplify|all] [parallel]`. Without arguments, every applicable aspect is reviewed, one agent at a time.

**How it works:**

1. finds the change-set (`code_review action=scope`) and the pull request of the branch, if any;
2. picks the agents that apply to the changed files;
3. runs them sequentially, or all at once with `parallel`;
4. merges the reports into Critical issues, Important issues, Suggestions and Strengths, with `file:line` references;
5. ends with a recommended action plan.

**Examples:**

```
/review-pr
/review-pr tests errors
/review-pr comments
/review-pr simplify
/review-pr all parallel
```

**Sample summary:**

```markdown
# PR review summary

## Critical issues (1 found)
- [silent-failure-hunter]: `except Exception: pass` swallows the upload error [storage/s3.py:88]

## Important issues (1 found)
- [pr-test-analyzer]: no test for an expired coupon (rated 8/10) [checkout/discount.py:40]

## Suggestions (1 found)
- [comment-analyzer]: docstring still says "returns None" [checkout/api.py:12]

## Strengths
- clear separation between the view and the service layer

## Recommended action
1. Fix the critical issues first
2. Address the important issues
3. Consider the suggestions
4. Re-run the review after the fixes
```

## Agents

### 1. comment-analyzer
**Focus**: accuracy and maintainability of code comments.

**Analyses:** comments against the actual code, documentation completeness, comment rot and technical debt, misleading or outdated comments.

**When to use:** after adding documentation, before finalizing a pull request that changes comments, when auditing existing comments.

**Triggers:**
```
"Check if the comments are accurate"
"Review the documentation I added"
"Analyze comments for technical debt"
```

### 2. pr-test-analyzer
**Focus**: quality and completeness of the test coverage.

**Analyses:** behavioural rather than line coverage, critical gaps, test quality and resilience, edge cases and error conditions.

**When to use:** after opening a pull request, when adding behaviour, to confirm the tests are thorough.

**Triggers:**
```
"Check if the tests are thorough"
"Review test coverage for this PR"
"Are there any critical test gaps?"
```

### 3. silent-failure-hunter
**Focus**: error handling and silent failures.

**Analyses:** silent failures in catch blocks, weak error handling, unjustified fallbacks, missing error logs.

**When to use:** after writing error handling, when reviewing try/catch blocks, before finalizing a pull request that touches error paths.

**Triggers:**
```
"Review the error handling"
"Check for silent failures"
"Analyze catch blocks in this PR"
```

### 4. type-design-analyzer
**Focus**: type design and invariants.

**Analyses:** encapsulation, invariant expression, invariant usefulness and invariant enforcement, each rated from 1 to 10.

**When to use:** when introducing a type, when a pull request adds data models, when refactoring a type.

**Triggers:**
```
"Review the UserAccount type design"
"Analyze type design in this PR"
"Check if this type has strong invariants"
```

### 5. code-reviewer
**Focus**: general review against the project guidelines.

**Analyses:** compliance with AGENTS.md and the other agent rule files, style breaches, bugs, quality problems. Each finding is scored from 0 to 100 and only those at 80 or more are reported. This is the same `code-reviewer` agent the `code-review` and `feature-dev` plugins use.

**When to use:** after writing or changing code, before a commit, before opening a pull request.

**Triggers:**
```
"Review my recent changes"
"Check if everything looks good"
"Review this code before I commit"
```

### 6. code-simplifier
**Focus**: simplification and refactoring without behaviour change.

**Analyses:** clarity and readability, needless complexity and nesting, redundant code and abstractions, consistency with the project standards, overly compact or clever code.

**When to use:** after writing or changing code, after a review has passed, when code works but feels complex.

**Triggers:**
```
"Simplify this code"
"Make this clearer"
"Refine this implementation"
```

**Note:** this is the only agent of the toolkit that edits files. It keeps the behaviour, then runs `verify` and the relevant tests.

## Usage patterns

### One agent

Ask a question that matches an agent's focus and Navin starts the right one (`spawn agent=<name>`):

```
"Can you check if the tests cover all edge cases?"
-> pr-test-analyzer

"Review the error handling in the API client"
-> silent-failure-hunter

"I've added documentation - is it accurate?"
-> comment-analyzer
```

### Complete review

Run `/review-pr`, or ask for several aspects at once:

```
"I'm ready to open this PR. Please:
1. Review test coverage
2. Check for silent failures
3. Verify code comments are accurate
4. Review any new types
5. General code review"
```

### Proactive review

Navin may start these agents on its own when the context calls for it:

- **after writing code** -> code-reviewer
- **after adding docs** -> comment-analyzer
- **before opening a pull request** -> several agents as appropriate
- **after adding types** -> type-design-analyzer

## Agent details

### Scoring

- **comment-analyzer**: reports factual errors as critical, then improvements and removals
- **pr-test-analyzer**: rates each gap from 1 to 10 (10 = must add)
- **silent-failure-hunter**: CRITICAL, HIGH or MEDIUM severity per finding
- **type-design-analyzer**: rates 4 dimensions from 1 to 10
- **code-reviewer**: scores findings from 0 to 100 and reports 80 and above
- **code-simplifier**: lists the simplifications applied and the check results

### Output

Every agent returns a structured, actionable report: the issue, its `file:line`, why it is a problem, a suggested fix, ordered by severity.

## Best practices

### Which agent when

**Before a commit:** code-reviewer, plus silent-failure-hunter when error handling changed.

**Before a pull request:** pr-test-analyzer, comment-analyzer (comments changed), type-design-analyzer (types changed), code-reviewer (final pass).

**After the review passes:** code-simplifier.

**During review:** any agent for a specific concern, then a targeted re-run after the fixes.

### Several agents

**Parallel** (faster):
```
"Run pr-test-analyzer and comment-analyzer in parallel"
/review-pr all parallel
```

**Sequential** (when one informs the next):
```
"First review test coverage, then check code quality"
```

## Tips

- **Be specific**: name the agent or the concern for a focused review.
- **Use it early**: before opening the pull request, not after.
- **Critical first**: the agents order their findings by priority.
- **Iterate**: re-run after the fixes.
- **Do not overuse it**: stay on the changed code, not the whole codebase.

## Troubleshooting

### The agent did not start

**Fix:** be more specific, name the agent, or name the concern ("test coverage", "silent failures").

### The agent reviews the wrong files

**Fix:** name the files, the pull request number or the branch, or say "my uncommitted changes".

## Workflow integration

Recommended order:

1. write the code -> **code-reviewer**
2. fix the issues -> **silent-failure-hunter** (error handling)
3. add tests -> **pr-test-analyzer**
4. document -> **comment-analyzer**
5. the review passes -> **code-simplifier** (polish)
6. open the pull request -> `/commit-push-pr`

It combines well with:

- `/code-review` (plugin `code-review`): validates every finding with a second agent and can post inline comments on the pull request;
- your own project agents in `.navin/agents/`.

## Customising

Copy an agent into `.navin/agents/` (project) or `~/.navin/agents/` (personal) with the same name to override it, for example to add your project's logging functions to `silent-failure-hunter`. A project agent wins over a personal one, which wins over the bundled one.

## Version

1.0.0
