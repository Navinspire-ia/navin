# Feature Development Plugin

A guided, phase-by-phase way to build new features with Navin: understand the code first, settle every open question, compare designs, then build and review.

## Overview

Jumping straight into code tends to produce features that ignore existing patterns, miss edge cases or need rework after review. The `feature-dev` plugin gives Navin a fixed seven-phase routine that front-loads understanding and decisions, and backs each phase with specialised subagents:

- **`/feature-dev`** - the command that drives the whole workflow;
- **`code-explorer`** - traces how existing features work;
- **`code-architect`** - turns findings into a decided implementation blueprint;
- **`code-reviewer`** - reviews the result with a confidence score and a high bar.

The plugin also points to the `fullstack-dev`, `test-generator` and `code-reviewer` skills for the implementation and test-writing steps.

## Philosophy

1. **Read before you write.** Navin studies the code and its conventions before proposing anything.
2. **Ask, do not guess.** Ambiguities, edge cases and unspecified behaviour are turned into explicit questions, asked once the code is understood and before any design.
3. **Design with alternatives.** Several architectures are compared, with their trade-offs, and Navin states which one it recommends.
4. **Review before shipping.** Independent reviewers look for bugs, duplication and convention breaches.
5. **Stay visible.** Each phase is a task on the project board, moved as the work advances.

## Command: `/feature-dev`

Starts the guided workflow.

**Usage:**

```
/feature-dev Add rate limiting to the public API
```

The argument is optional. Without it, Navin starts by asking what you want to build:

```
/feature-dev
```

What Navin does on start:

- creates seven board tasks, one per phase;
- runs the phases in order, stopping at the decision points (questions, choice of architecture, go-ahead for implementation, what to do with review findings).

## The 7-Phase Workflow

### Phase 1: Discovery

**Goal:** know exactly what has to be built.

- Puts the phases on the board.
- If the request is vague, asks which problem the feature solves, what it must do and which constraints apply.
- Restates its understanding and waits for your confirmation.

**Example:**

```
You: /feature-dev Add a dark mode

Navin: Before I start, let me check my understanding:
  - Scope: the web dashboard only, or the CLI output too?
  - Should the choice follow the OS setting, a user toggle, or both?
  - Must the preference be saved per user?
Please confirm or correct.
```

### Phase 2: Codebase Exploration

**Goal:** understand the relevant code, from the overall architecture down to the details.

- Starts 2 to 3 `code-explorer` subagents **in parallel**, each with its own angle: similar features, overall architecture, user experience, tests and extension points.
- Each explorer returns a list of 5 to 10 key files.
- Navin then reads every file the explorers listed itself.
- Presents a summary of what it found and of the patterns to follow.

**Sample briefs given to the explorers:**

- "Find features similar to user preferences and trace their implementation end to end"
- "Map the architecture and abstractions of the theming layer"
- "Identify the UI patterns, test approaches and extension points relevant to dark mode"

**Sample output:**

```
Exploration summary
- Settings are stored in src/settings/store.py (UserSettings, line 42)
  and exposed through GET/PUT /api/settings (src/api/settings.py:18).
- Styles come from CSS variables declared in web/src/theme.css.
- Components read the theme via the useTheme() hook (web/src/hooks/theme.ts:7).
- Tests: pytest for the API, vitest for the hooks.
Patterns to follow: new settings go through UserSettings, new styles as
CSS variables, no inline colours.
```

### Phase 3: Clarifying Questions

**Goal:** close every gap before designing. This phase is never skipped.

- Goes back over the findings and the original request.
- Lists what is still underspecified: edge cases, error handling, integration points, scope limits, design preferences, backward compatibility, performance.
- Asks **all the questions at once**, as an organised list.
- **Waits for your answers** before designing.

If you reply "do what you think is best", Navin gives its recommendation and asks for an explicit yes.

**Example:**

```
Navin: A few points to settle before I design anything:
  1. Default theme for existing users: light, or follow the OS?
  2. Should charts switch palettes too, or keep their current colours?
  3. Is a server-side setting required, or is local storage enough?
  4. Any accessibility contrast target (for example WCAG AA)?

You: 1) follow the OS, 2) yes, 3) server-side, 4) AA.
```

### Phase 4: Architecture Design

**Goal:** compare several implementation approaches.

- Starts 2 to 3 `code-architect` subagents in parallel, each with its own focus:
  - **minimal change** - smallest diff, maximum reuse;
  - **clean architecture** - maintainability and sound abstractions;
  - **pragmatic balance** - speed and quality together.
- Weighs the proposals against the task (size, urgency, complexity, team context).
- Presents each approach briefly, the trade-offs side by side, its recommendation and the reason.
- **Asks you which approach to take**, with its pick marked as recommended.

**Sample output:**

```
Approach A - minimal change
  Add a `theme` field to UserSettings and a CSS class on <body>.
  + 3 files, fast  - colours stay scattered

Approach B - clean architecture
  New ThemeProvider, design tokens, palette module for charts.
  + easy to extend  - 12 files, larger review

Approach C - pragmatic balance (recommended)
  `theme` setting + CSS variables for both palettes, charts read the variables.
  + 6 files, consistent styling  - no runtime theme plug-ins

Which approach should I implement?
```

### Phase 5: Implementation

**Goal:** build the feature.

- **Does not start without your explicit go-ahead.**
- Reads every relevant file identified so far.
- Implements the chosen architecture, strictly following the codebase conventions.
- Writes clean, documented code and tests for the new behaviour.
- Keeps the board up to date.

### Phase 6: Quality Review

**Goal:** code that is simple, DRY, readable and functionally correct.

- Starts 3 `code-reviewer` subagents in parallel, one focus each:
  - simplicity, DRY and elegance;
  - bugs and functional correctness;
  - project conventions and abstractions.
- Merges their findings and picks the most severe ones it recommends fixing.
- **Shows you the findings and asks what to do**: fix now, fix later, or ship as is.
- Acts on your decision.

**Sample output:**

```
Review findings
Critical
  - src/api/settings.py:57 - an unknown `theme` value is stored without
    validation (confidence 90). Fix: restrict to {"light","dark","system"}.
Important
  - web/src/hooks/theme.ts:22 and web/src/charts/palette.ts:10 duplicate
    the OS-preference lookup (confidence 85). Fix: share one helper.

Fix now, fix later, or ship as is?
```

### Phase 7: Summary

**Goal:** record what was done.

- Closes the remaining board tasks.
- Summarises what was built, the key decisions, the files changed and the suggested next steps.

**Sample output:**

```
Done: dark mode for the web dashboard.
Decisions: follows the OS by default, stored server-side, AA contrast.
Files: src/settings/store.py, src/api/settings.py, web/src/theme.css,
       web/src/hooks/theme.ts, web/src/charts/palette.ts, tests (2 new).
Next: add a toggle to the mobile app, run a visual regression pass.
```

## Agents

All three agents are read-only: they never modify files. They use Navin's code navigation tools (`read_file`, `grep`, `find_files`, `list_dir`, `code_index`, `lsp`, plus `metagraph` for the explorer and architect, `code_review` for the reviewer) and can consult the web (`web_fetch`, `web_search`).

### `code-explorer`

**Purpose:** reconstruct how an existing feature is really implemented, from its entry points to where its data is stored.

**What it covers:**

- entry points (API routes, UI components, CLI commands, jobs) and the core logic files;
- call chains, data transformations, dependencies, state changes and side effects;
- architecture layers, design patterns, interfaces, cross-cutting concerns (auth, logging, caching, errors);
- key algorithms, error handling, edge cases, performance, technical debt.

**Output:** entry points and flow with `file:line` references, components and their responsibilities, architecture insights, dependencies, observations, and the list of files essential to the topic.

**When it runs:** automatically in Phase 2, or on demand when a feature must be understood before it is changed.

### `code-architect`

**Purpose:** produce a complete, decided implementation blueprint that fits the existing code.

**What it covers:**

- the conventions already in place: stack, module boundaries, abstraction layers, and the project guidelines (`AGENTS.md` and the other agent rule files the repository keeps);
- one chosen design, integrated with the existing code and built for testability, performance and maintainability.

**Output:** patterns found (with `file:line`), the architecture decision and its trade-offs, component design, implementation map (files to create or change), data flow, build sequence as a checklist, and critical details (errors, state, tests, performance, security).

**When it runs:** automatically in Phase 4 (several in parallel, each with a different focus), or on demand when a design must be decided before coding.

### `code-reviewer`

**Purpose:** find real problems with very few false positives.

**What it checks:**

- project guidelines (`AGENTS.md` or equivalent): imports, framework conventions, style, error handling, logging, tests, naming, platform compatibility;
- bugs: logic errors, null handling, race conditions, leaks, security holes, performance problems;
- significant quality issues: duplication, missing critical error handling, accessibility, inadequate tests.

**Scoring:** each candidate issue gets a confidence from 0 to 100; **only issues at 80 or more are reported**.

**Scope:** by default the uncommitted changes of the working tree (via `code_review action=scope`); you can name other files.

**Output:** what was reviewed, then each issue with its description, score, `file:line`, the rule or bug explanation and a concrete fix, grouped as Critical then Important. When nothing reaches the threshold, it says the code meets the standard.

**When it runs:** automatically in Phase 6, or on demand before a commit.

## Usage Patterns

### Full workflow (recommended for new features)

```
/feature-dev Add CSV export to the reports page
```

Then answer the questions, pick an architecture, approve the implementation and decide on the review findings.

### Manual agent invocation

You can use an agent on its own, without the full workflow:

```
Use the code-explorer agent to trace how authentication works.
```

```
Ask code-architect for a blueprint to add webhooks to the billing module.
```

```
Have code-reviewer check my uncommitted changes.
```

Under the hood Navin starts them as subagents, for example `spawn agent="code-explorer"`.

## Best Practices

1. **Use the full workflow for anything non-trivial.** The phases pay for themselves on features that touch several files.
2. **Answer the clarifying questions carefully.** They prevent most of the rework.
3. **Choose the architecture deliberately.** The recommendation is a starting point, not an order.
4. **Do not skip the review.** It catches problems while the context is still fresh.
5. **Read the summary.** It records the decisions you will want later.

## When to Use This Plugin

**Use it for:**

- new features touching several files or layers;
- features that need architecture decisions;
- work in an unfamiliar part of the codebase;
- requirements that are still fuzzy.

**Do not use it for:**

- one-line fixes or obvious bugs;
- trivial, well-defined changes;
- urgent hotfixes where speed matters more than process.

## Requirements

- Navin with subagent support (`spawn`) and the project board.
- A project with existing code to explore (the workflow assumes there are patterns to learn from).
- Optionally, an `AGENTS.md` (or equivalent agent rule file) so the architect and reviewer can follow your conventions.

## Troubleshooting

### Agents take too long

**Cause:** a very large codebase or broad briefs.

**Fix:**

- this is expected on big repositories; the agents run in parallel where they can;
- narrow the feature description, or name the area to explore;
- configure a faster model preset for subagents.

### Too many clarifying questions

**Cause:** the initial request was vague.

**Fix:**

- give more context up front (scope, constraints, preferred behaviour);
- answer "do what you think is best" on minor points: Navin will propose and ask for a yes;
- the questions save time later, so answer the important ones properly.

### Architecture options are overwhelming

**Fix:**

- follow the recommended option when unsure;
- ask Navin to explain a trade-off in more detail;
- ask for a mix ("approach C, but with B's palette module").

## Tips

- **Be specific:** "Add OAuth login with Google and GitHub to the web app" beats "Add login".
- **Mention constraints early:** deadlines, compatibility, dependencies you cannot add.
- **Use the agents alone** for exploration or reviews outside a full feature.
- **Keep the board open** to follow the phases as they progress.

## Plugin contents

| Item | Kind |
|---|---|
| `/feature-dev` | command |
| `code-explorer`, `code-architect`, `code-reviewer` | agents |
| `fullstack-dev`, `test-generator`, `code-reviewer` | related skills |

Personal or project overrides of this plugin can be placed in `~/.navin/plugins/feature-dev/` or in the project's `.navin/` folder; files inside a plugin pack refer to themselves through `${NAVIN_PLUGIN_ROOT}`.

## Version

1.0.0
