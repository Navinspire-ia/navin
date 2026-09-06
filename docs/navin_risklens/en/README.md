# RiskLens module - Overview

The **RiskLens** module (sidebar → **RiskLens**, route `#/risklens`) is the studio to open **before** Code. It assumes your plan, launch, hire, or decision already failed 6 months from now, then works backward through every genuine failure reason to produce a revised plan and a pre-launch checklist.

The method comes from psychologist Gary Klein (Harvard Business Review). The core insight: asking "what could go wrong?" yields cautious answers; saying "this already failed - tell me why" switches reasoning into narrative mode and surfaces far more specific causes.

In Navin, RiskLens sits **just above the Code module** in the sidebar: explain and stress-test the idea before you start the project.

## How it works

1. Open **RiskLens** in the sidebar (above **Code**).
2. Click an action card in one of the three groups - **Run**, **Decisions**, **Focus** (see [Actions](./actions.md)).
3. The chat opens with `/risklens` and the card prompt already in the composer (height follows the text). Complete the plan in chat, then send.
4. The agent gathers missing context if needed, sets the failure frame, generates reasons, deep-dives in parallel, synthesizes, then saves the report files.

Direct usage in the module chat:

```
/risklens launch a billing SaaS at $29/mo for freelancers, 200 paying users in 6 months
```

## The `/risklens` command

| | |
| --- | --- |
| Command | `/risklens [plan\|launch\|decision]` |
| Lifecycle | Agent workflow (runs a full agent turn) |
| Skills preloaded | `risklens`, `multi-agent-orchestration`, `task-planner` |
| Output | Synthesis (most likely failure, most dangerous failure, hidden assumption, revised plan, checklist) + HTML/MD files in the workspace |

## Session flow

| Step | What the agent does |
| --- | --- |
| Context | Checks the minimum bar: what it is, who it is for, what success looks like. Asks focused questions if a piece is missing. |
| Frame | States explicitly: "in 6 months, this plan has already failed." |
| Raw reasons | Lists every genuine failure reason grounded in plan details (no generic padding). |
| Deep-dives | One subagent per reason, in parallel (`spawn`): failure story, underlying assumption, early warning signs. |
| Synthesis | Most likely failure, most dangerous failure, hidden assumption, concrete revised plan, 3-5 item checklist. |
| Deliverables | `risklens-report-[timestamp].html`, `risklens-transcript-[timestamp].md`, short chat summary. |

## Good and bad targets

**Good targets**

- A product or feature about to be built
- A launch with money or reputation on the line
- A pricing or business-model change
- A hire about to be made
- A strategy or positioning pivot
- A partnership or deal under evaluation
- Any commitment where being wrong is expensive

**Bad targets**

- Vague ideas with no concrete plan yet (plan first, then risklens)
- Questions with one right answer
- Creative feedback on a draft (that is editing)
- Decisions already made and irreversible

## Module scoping

`/risklens` belongs to the RiskLens module. From **Code**, the command is hidden from the palette and rejected if typed - open RiskLens in the sidebar, then retry. Other studios (`/seo`, `/campaign`, …) stay out of RiskLens.

## Recommended sequence

1. **RiskLens** - stress-test the plan, get the revised plan and checklist.
2. **Documents** (`/studio`) - if you need to formalize the brief or pitch.
3. **Code** (`#/code`) - only after that, to implement the revised version.

## Tips

- Always give the three context pieces: what, who, success.
- The useful product is the **synthesis** and the **revised plan**, not a raw fear list.
- Demand revisions the user can run this week ("$47 pilot with 20 people"), not vague advice.
- The agent must **not** start coding in this module - that is intentional.
