---
name: risklens
description: >
  Run RiskLens on any plan, launch, product, hire, strategy, or decision.
  Assumes it already failed 6 months from now and works backward to find every
  reason why. Produces a revised plan with blind spots exposed. Use for
  /risklens, "run risklens", "risklens this", "what could kill this",
  "stress test this plan", "find the blind spots", or any high-stakes commitment
  before execution. Do not use for simple feedback, factual questions, or vague
  ideas with no plan.
metadata: {"navin":{"emoji":"🪦","category":"intelligence","requires":{}}}
---

# RiskLens

RiskLens uses prospective hindsight: it is the opposite of a postmortem. Instead
of figuring out what went wrong after something fails, you imagine it already
failed and figure out why before you start.

The method comes from psychologist Gary Klein (Harvard Business Review).
Daniel Kahneman called it his single most valuable decision-making technique.
Google, Goldman Sachs, and Procter & Gamble use it before major decisions.

Core insight: asking "what could go wrong?" yields cautious, hedged answers.
Saying "this already failed - tell me why" switches brains into narrative mode
and surfaces far more specific, honest failure reasons (prospective hindsight).

For an AI agent that defaults to agreeable optimism, the frame "this is dead,
explain how it died" is what breaks the pattern.

## When to run

**Good targets**

- A product or feature about to be built
- A launch with money or reputation on the line
- A pricing or business-model change
- A hire about to be made
- A strategy or positioning pivot
- A partnership or deal under evaluation
- Any commitment where being wrong is expensive

**Bad targets**

- Vague ideas with no concrete plan yet (help plan first, then run RiskLens)
- Questions with one right answer
- Creative feedback on a draft (that is editing)
- Decisions already made and irreversible

## Context gathering (minimum bar)

A risklens is only as good as its context. Hit this threshold before running.

### 1. Scan existing context

Before asking the user anything:

- Read the current conversation for plan / audience / success criteria
- Scan the workspace with `grep` / `read_file` for briefs, README, product docs,
  `CLAUDE.md`, memory notes, or files the user attached
- Spend at most ~30s of tool time - key grounding files only

### 2. Sufficiency check

You need all three:

1. **What is it?** - one-sentence description of the plan/decision
2. **Who is it for / who does it affect?** - audience, customers, team, stakeholders
3. **What does success look like?** - failure is the inverse of success

### 3. Fill gaps conversationally

If all three are present, proceed. Otherwise ask one focused question at a time,
never more than needed. Examples:

- "What specifically are you launching / deciding?"
- "Who is the primary buyer or affected party?"
- "What does success look like in 6 months?"

## Session workflow

### Step 1 - Set the frame

State the frame explicitly:

> OK, I have enough context. Let's run the risklens. Premise: it is 6 months
> from now. [The plan] has failed. It is done. We are looking back to understand
> what went wrong.

### Step 2 - Raw failure reasons

Generate every genuine reason the plan could have died. No padded categories.
Each reason must be:

- Specific to this plan
- Grounded in actual details
- A genuine threat (not a minor inconvenience)

Output a numbered list, 1-2 sentences each. Typical count: 3-9 real modes.

### Step 3 - Deep-dive agents (parallel)

Spawn **one subagent per failure reason**, all in the same batch via `spawn`
(they run concurrently). Subagents do not see your history - put the full brief
in `task`.

**Subagent task template:**

```text
Role: RiskLens investigator
You analyze ONE assigned failure reason in depth.

The plan:
---
[what it is, who it's for, success criteria, relevant workspace context]
---

PREMORTEM FRAME: It is 6 months from now. This plan has failed.

YOUR ASSIGNED FAILURE REASON: [reason from step 2]

Write:
1. THE FAILURE STORY - 2-3 paragraphs, specific moments, grounded in plan details
2. THE UNDERLYING ASSUMPTION - one sentence the user took for granted
3. EARLY WARNING SIGNS - 1-2 concrete, observable signals

Keep under 300 words. Be direct. Do not sugarcoat.
Done when: the three sections above are complete.
Do not: analyze other failure reasons, rewrite the whole plan, or hedge.
```

Prefer `spawn` for 3+ independent reasons. If spawn is unavailable, deep-dive
sequentially yourself - same structure, same honesty.

### Step 4 - Synthesis

Produce the **RiskLens Report**:

1. **Most Likely Failure** - most probable scenario and why
2. **Most Dangerous Failure** - highest damage even if less likely
3. **Hidden Assumption** - the biggest unquestioned assumption across all analyses
4. **Revised Plan** - concrete changes mapped to specific failure modes (actions
   the user can take this week, not vague advice)
5. **Pre-Launch Checklist** - 3-5 verifiable actions that prevent or detect modes

### Step 5 - Save artifacts

Write to the workspace:

- `risklens-report-[timestamp]` - Track A studio UI (Vite + official DS +
  Three.js / R3F / drei; follow `studio-html-report` Track A)
- `risklens-transcript-[timestamp].md` - full reasoning trail

RiskLens UI body must include:

- Synthesis block prominent at the top (most likely / most dangerous / hidden
  assumption)
- One card per failure reason (story, assumption, warning signs)
- Visual severity / likelihood indicators
- Revised plan + pre-launch checklist
- Deliverables table if other files were saved

Also save a plain-text summary as `risklens-report-[timestamp].txt` when useful.

**Immediately after the UI report exists**, call `open_preview` on that
Track A `risklens-report-*` app (Vite + official DS + Three.js / R3F / drei).
Do not close with Export PDF. Do not paste the whole HTML into the chat.

### Step 6 - Chat summary

In chat, three sentences max: most likely failure, hidden assumption, single
most important revision. Mention that the UI report is open in Preview.

## Important rules

- Always set the "already failed" frame - that is the mechanism
- Spawn failure investigators in parallel when possible
- Be comprehensive but not padded - real count only
- Synthesis is the product - specific and actionable
- Do not sugarcoat
- Revised plan must be concrete this week
- Respect the minimum context threshold - ask rather than invent
- This is not multi-perspective debate - it is future-failure forensic analysis
