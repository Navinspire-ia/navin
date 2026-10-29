---
name: doc-coauthoring
description: Co-author structured documents (proposals, technical specs, decision docs, RFCs, briefs, PRDs) in three stages - a short context round, outline then section-by-section drafting reviewed block by block, and a reader simulation that lists gaps first. Use when the user asks to "write a proposal", "draft a spec", "help me write a decision doc" or "co-write this brief". In French "coécris ce document avec moi", "rédige une spécification section par section" or "aide-moi à écrire cette proposition".
metadata: {"navin":{"emoji":"📝","category":"writing"}}
---

# Doc Co-Authoring

A document that other people must read and act on fails for one reason: the
author knows things the reader does not, and never notices. This skill closes
that gap in three stages. Stage 1 moves the user's context into the
conversation. Stage 2 builds the document one section at a time, with the user
approving each block. Stage 3 re-reads the finished draft as the target reader
and lists what is missing or ambiguous.

Act as a guide, not a ghostwriter: ask, propose, draft, revise. Keep the user
in control of every block and never reprint the whole document when a targeted
edit will do.

## When to trigger

Offer the workflow when the user:

- names a document type: proposal, spec, design doc, decision doc, RFC, PRD,
  brief, one-pager, memo for a committee;
- says "write up", "draft", "put together a doc" about something with several
  stakeholders or a decision attached;
- pastes notes and asks to turn them into something others will read.

Do not force it on short texts (an email, a paragraph, a changelog entry) or on
documents the user only needs for themselves. For plain business letters and
reports with a fixed format, prefer `professional-writer`. Present the three
stages in two sentences, say roughly how long each takes, and ask whether the
user wants the guided path or a freeform draft. Respect the answer.

## Workflow

### Stage 1: gather context

Goal: know enough to ask about edge cases and trade-offs without having the
basics explained.

1. Ask the five opening questions in one message: document type, primary
   reader, the effect the document must have on that reader, any template or
   format to follow, constraints (deadline, length, confidentiality). Invite
   shorthand answers.
2. If a template or an existing draft exists, read it with `read_file` (or
   `web_fetch` for a public page). Note images without alt text and offer to
   describe them, since many readers will skim the text only.
3. Ask for an information dump: background, why alternatives were rejected,
   organisational context, timeline pressure, technical dependencies,
   stakeholder worries. Say explicitly that order and polish do not matter.
4. When the dump is in, pick the question set for the document type in
   `references/question-bank.md` and ask 5 to 10 numbered questions that
   target real gaps. Accept answers such as "3: no, backwards compat" or
   "see the file I attached". Use `ask_user` only for a true fork (two viable
   framings, one must be chosen); open questions belong in the chat.
5. Track what is known and what is still unclear. Exit when the next question
   would be about a nuance rather than a fact. Ask once whether anything else
   should be added, then move on.

### Stage 2: outline, then draft section by section

1. Propose a structure: 3 to 7 sections suited to the document type (the
   question bank lists typical outlines). Adjust until the user agrees.
2. Create the scaffold as a markdown file in the workspace with `write_file`
   (`docs/<slug>.md` or the path the user names): every heading, each followed
   by a one-line placeholder. Open it with `open_in_editor` so the user sees
   the document grow.
3. Start with the section that carries the most unknowns: the core proposal
   in a decision doc, the technical approach in a spec, the problem statement
   in a brief. Leave summaries and introductions for last.
4. For each section:
   - ask 3 to 8 clarifying questions specific to that section;
   - brainstorm 5 to 20 numbered candidate points, including context the user
     shared earlier but may have forgotten;
   - ask which to keep, drop or merge, with a short reason for each ("drop 6,
     the audience knows it") - the reasons teach the user's priorities for the
     next sections; accept freeform feedback and translate it;
   - ask once whether anything important is missing;
   - draft the section into the file with `edit_file`, replacing only the
     placeholder;
   - ask the user to read it and say what to change rather than editing
     silently, and apply each change with `edit_file`. If the user edits the
     file directly, read it again and adopt their choices in later sections.
5. After three rounds without substantial change, ask what can be removed
   without losing information. Shorter is a feature.
6. When roughly 80 percent of the sections are done, re-read the whole file
   once: flow between sections, repetition, contradictions, generic filler,
   sentences that carry no information. Report the findings and fix them.
7. When every section is drafted, do a final coherence pass, then ask whether
   to move to reader testing.

### Stage 3: reader simulation

Goal: find the blind spots before a real reader does.

1. Write down 5 to 10 questions the target reader would realistically bring to
   the document ("what do I have to do by when", "why not option B", "what does
   this cost my team").
2. Run the test with a fresh reader that has no conversation context: use
   `spawn` with a task containing only the document text, the reader persona
   and the questions, asking for the answer to each question, a list of
   ambiguous passages, the knowledge the document assumes, and any internal
   contradiction. If `spawn` is not available, answer the questions from the
   file alone, deliberately ignoring everything learned in Stage 1, and say so.
3. Summarise per question: answered correctly, answered wrong, not
   answerable. Add the ambiguity and assumption findings.
4. Fix the affected sections through the Stage 2 loop. Repeat the simulation
   until the reader answers every question correctly and surfaces nothing new.

### Close

1. Ask the user for a last personal read: they own the facts, the links and the
   consequences.
2. Offer the final format. For a Word file hand over to the `docx-generator`
   skill with the finished markdown; for a PDF hand over to `pdf-generator`.
   Keep the markdown as the editable source and name both outputs the same
   slug.
3. Suggest an appendix for depth that would bloat the body, and a short
   "decision log" line pointing to the context the document was built from.

## Rules

- Be procedural and brief. Explain a step only when it changes what the user
  has to do.
- Never let an unknown term or project pass: ask when it appears.
- Edits are surgical. Use `edit_file` on the placeholder or the sentence in
  question; do not rewrite untouched sections.
- Brainstorm lists live in the chat, never in the document.
- The user can skip or shorten any stage; offer the lighter path when they
  look rushed ("draft all sections at once, then one review round").
- Invent nothing: a missing fact becomes a `[TODO: owner, date]` marker, not a
  plausible sentence.

## Bundled files

- `references/question-bank.md` - opening questions, per-type question sets
  and typical outlines for proposals, specs, decision docs, RFCs, PRDs, briefs
  and post-mortems.
