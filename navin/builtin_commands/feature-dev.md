---
description: Guided feature development - explore the codebase, clarify, design, build and review
argument-hint: Optional feature description
---

# Feature development

Help the developer ship a new feature with discipline: learn how the codebase works, surface every unclear detail, design an architecture that fits, then build and review it.

## Ground rules

- **Clarify, do not assume**: list the ambiguities, edge cases and unspecified behaviours, and ask precise questions (`ask_user` when the answer is a choice). Ask once you understand the code and before you design. Do not implement until the answers are in.
- **Understand first**: read the existing code and its patterns before proposing anything.
- **Read what the agents point to**: every subagent returns its list of key files. Read those files yourself before moving on.
- **Simple and elegant**: favour readable, maintainable, well-structured code.
- **Track progress**: put the seven phases on the board (one task per phase) and move them as you go.

---

## Phase 1: Discovery

**Goal**: know what has to be built.

Initial request: $ARGUMENTS

1. Put the phases on the board.
2. If the feature is unclear, ask which problem it solves, what it must do, and which constraints or requirements apply.
3. Restate your understanding and get the user's confirmation.

---

## Phase 2: Codebase exploration

**Goal**: understand the relevant code and patterns, from the big picture down to the details.

1. Start 2 to 3 `code-explorer` subagents in parallel (`spawn agent="code-explorer"`, all in the same reply). Give each one a different angle (similar features, overall architecture, user experience, tests and extension points) and ask each for a list of 5 to 10 key files. Example briefs:
   - "Find features similar to <feature> and trace their implementation end to end"
   - "Map the architecture and abstractions of <area>, tracing the code end to end"
   - "Analyse how <existing feature> works today, tracing the code end to end"
   - "Identify the UI patterns, test approaches and extension points relevant to <feature>"
2. When they report, read every file they listed.
3. Present a complete summary of what you found and of the patterns to follow.

---

## Phase 3: Clarifying questions

**Goal**: close every gap before designing. **Never skip this phase.**

1. Go back over the findings and the original request.
2. List what is still underspecified: edge cases, error handling, integration points, scope limits, design preferences, backward compatibility, performance.
3. **Ask all the questions at once, as a clear and organised list.**
4. **Wait for the answers before designing.**

If the user answers "do what you think is best", give your recommendation and get an explicit yes.

---

## Phase 4: Architecture design

**Goal**: compare several implementation approaches and their trade-offs.

1. Start 2 to 3 `code-architect` subagents in parallel, each with its own focus: minimal change (smallest diff, maximum reuse), clean architecture (maintainability, sound abstractions), pragmatic balance (speed and quality).
2. Weigh the proposals and decide which one fits this task best (small fix or large feature, urgency, complexity, team context).
3. Present each approach briefly, the trade-offs side by side, **your recommendation and why**, and the concrete differences in implementation.
4. **Ask the user which approach to take** (`ask_user`, your pick marked recommended).

---

## Phase 5: Implementation

**Goal**: build the feature.

**Do not start without the user's approval.**

1. Wait for an explicit go-ahead.
2. Read every relevant file identified in the earlier phases.
3. Implement the chosen architecture.
4. Follow the codebase conventions strictly.
5. Write clean, documented code, with tests for the new behaviour.
6. Keep the board up to date.

---

## Phase 6: Quality review

**Goal**: code that is simple, DRY, elegant, easy to read and functionally correct.

1. Start 3 `code-reviewer` subagents in parallel, one focus each: simplicity / DRY / elegance, bugs / functional correctness, project conventions / abstractions.
2. Merge their findings and pick the most severe ones you recommend fixing.
3. **Show the findings and ask what to do**: fix now, fix later, or ship as is.
4. Act on the user's decision.

---

## Phase 7: Summary

**Goal**: record what was done.

1. Close the remaining board tasks.
2. Summarise what was built, the key decisions, the files changed and the suggested next steps.
