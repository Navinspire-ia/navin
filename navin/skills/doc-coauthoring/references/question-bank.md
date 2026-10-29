# Question bank

Pick the set that matches the document type, then keep only the questions the
context dump left open. Five good questions beat ten generic ones. Number them
so the user can answer in shorthand.

## Opening questions (every document)

1. What kind of document is this, and is there a template or a past example to
   match?
2. Who reads it first, and who else will see it later?
3. After reading, what should the reader know, decide or do?
4. Deadline, length limit, confidentiality or review process to respect?
5. What already exists: notes, threads, tickets, slides, earlier drafts?

## Proposal (asking for a yes)

Typical outline: Summary / Problem / Proposed approach / Alternatives /
Cost and timeline / Risks / Ask.

- What exactly is being asked for: budget, headcount, permission, a priority
  change? In which unit (euros, people, weeks)?
- Who decides, and what do they usually need to see before saying yes?
- What happens if nothing is done for six months?
- Which alternatives were considered and why were they dropped?
- What has already been tried, and what did it show?
- Which numbers are solid and which are estimates? How were the estimates made?
- What are the first three things that happen after approval?
- Who loses something if this is approved, and have they been consulted?
- What is the smallest version that would still be worth doing?
- Is there a date after which the proposal no longer makes sense?

## Technical specification

Typical outline: Goals and non-goals / Background / Design / Interfaces and
data / Failure modes / Rollout / Open questions.

- What must the system do, stated as observable behaviour? What is explicitly
  out of scope?
- Which existing components does it touch, and which contracts must not break?
- What are the inputs, outputs and data shapes? Where is the source of truth?
- Expected load, latency and size figures, with the source of each number.
- How does it fail: partial outage, bad input, dependency down? What does the
  user see in each case?
- Security and privacy constraints: who may call it, what is logged, what is
  retained.
- Migration or rollout plan: feature flag, dual run, backfill?
- How will it be observed in production: metrics, alerts, dashboards?
- Which decisions are still open, and who owns each one?
- Which parts could a reviewer reasonably push back on? Pre-empt them.

## Decision document

Typical outline: Decision / Context / Options considered / Recommendation and
reasoning / Consequences / Review date.

- What is the decision, in one sentence a newcomer understands?
- Why now? What forced the question?
- Which options are on the table, including "do nothing"?
- Which criteria matter, in order (cost, speed, risk, reversibility,
  team skills)?
- How does each option score against those criteria, and where is the
  evidence?
- What would change the recommendation?
- Who is accountable for executing, and who must be informed?
- Is the decision reversible? At what cost and until when?
- When will it be reviewed, and against which signal?

## RFC (request for comments)

Typical outline: Summary / Motivation / Detailed proposal / Drawbacks /
Alternatives / Unresolved questions / Timeline for comments.

- Who are the commenters, and what level of detail do they expect?
- What specific feedback is wanted: approval, design critique, naming,
  edge cases?
- What is deliberately not being decided in this RFC?
- Which prior art or external standards apply?
- What are the known drawbacks the author already accepts?
- Comment deadline and how the decision will be recorded afterwards.

## PRD (product requirements)

Typical outline: Problem and users / Goals and success metrics / Requirements
(must, should, could) / User flows / Out of scope / Launch plan.

- Who is the user, in concrete terms (role, situation, frequency)?
- What do they do today without the feature, and what does it cost them?
- Which evidence exists: interviews, support tickets, usage data?
- How will success be measured, with a baseline and a target?
- Which requirements are non-negotiable, and which are nice to have?
- What is explicitly out of scope for the first release, and why?
- Dependencies on other teams, legal review, pricing or support?
- What does the launch look like: beta cohort, announcement, docs, training?

## Brief (short document that aligns a group)

Typical outline: Situation / Objective / Approach / Roles / Timeline /
What is needed from the reader.

- What single thing must every reader take away?
- Who are the readers, and how much do they already know?
- What is the reader expected to do, and by when?
- Which decisions are already made and must not be reopened?
- Which constraints shape the approach (budget, people, brand, legal)?
- Where do readers go with questions?

## Post-mortem or incident review

Typical outline: Summary / Impact / Timeline / Root causes / What went well /
Action items with owners and dates.

- What was the user-visible impact, in numbers and duration?
- Minute-by-minute timeline: detection, escalation, mitigation, resolution.
- Which contributing causes were technical, which were process?
- What slowed detection or recovery?
- What already worked and should be kept?
- Which actions prevent recurrence, who owns each, and when is it due?
- Blameless framing: name systems and decisions, not people.

## Section-level prompts (use during Stage 2)

For any section, these usually surface what is missing:

- Which sentence here would a sceptical reader challenge first?
- Which claim has no number, link or example behind it?
- What does this section assume the reader already knows?
- If this section were deleted, what would the reader fail to understand?
- Is there a decision hidden inside a description?

## Reader simulation prompts (Stage 3)

Give the fresh reader the document text, the persona and these questions:

- Answer each prepared question using only the document.
- List every passage with two possible readings.
- List the knowledge the document assumes.
- Point out contradictions between sections.
- Say what the reader would still have to ask the author.
