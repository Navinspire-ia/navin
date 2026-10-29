---
name: internal-comms
description: Write internal communications that people actually read - weekly status updates, team newsletters, FAQ answers, leadership notes, project updates and incident communications - with an audience and tone matrix and fixed structure templates. Use when the user asks to "write a status update", "draft the weekly newsletter", "write an incident update" or "send a note to leadership". In French "rédige une note interne", "écris le point hebdo de l'équipe", "annonce ce changement à l'équipe" or "prépare la communication d'incident".
metadata: {"navin":{"emoji":"📣","category":"writing"}}
---

# Internal Comms

Internal communication competes with everything else in a colleague's inbox.
The reader gives it thirty seconds; the text must earn more or deliver in
thirty. Every format below optimises for that: the point first, numbers over
adjectives, links instead of retelling, and a clear statement of what the
reader should do.

This skill covers the recurring formats. For external announcements use the
marketing skills; for formal letters or long reports use `professional-writer`;
for a document that needs stakeholder co-authoring use `doc-coauthoring`.

## Pick the format

| Request sounds like | Format | Example |
|---|---|---|
| "weekly update", "what did the team do", "3P" | Status update: Progress, Plans, Problems | `examples/status-update.md` |
| "newsletter", "company digest", "monthly recap" | Newsletter with sections and links | `examples/newsletter.md` |
| "people keep asking", "FAQ", "answer these questions" | FAQ answers | `examples/faq-answers.md` |
| "we are down", "outage comms", "post the incident status" | Incident update | `examples/incident-update.md` |
| "note to leadership", "heads-up to execs" | Leadership note (structure below) | - |
| "project update", "milestone mail" | Project update (status update structure with a timeline line) | `examples/status-update.md` |

If none fits, ask for audience, purpose and tone, then use the leadership note
structure as the default.

## Audience and tone matrix

| Audience | They want | Tone | Length |
|---|---|---|---|
| Executives | decisions needed, risk, numbers against plan | matter-of-fact, no hedging | 5 to 8 lines |
| Whole company | what changed for them, where to read more | warm, first person plural ("we") | 20 to 25 bullets max |
| Own team | detail, credit, next steps | direct, informal | as long as useful |
| Neighbouring teams | dependencies, dates, who to contact | precise, neutral | 1 screen |
| Everyone during an incident | impact, workaround, next update time | calm, factual, no blame | 6 to 10 lines |

Match the vocabulary to the reader: a company newsletter never contains a
ticket number; a team update can be nothing but ticket numbers.

## What to ask before writing

Ask in one message, accept shorthand answers:

1. Audience and channel (chat post, email, wiki page) - the channel sets the
   length and whether links or attachments work.
2. Period covered (last week, the sprint, the quarter) or the moment (incident
   start time).
3. Source material: notes, tickets, dashboards, a previous update to mirror.
   Read what is given with `read_file`; search the user's notes or meetings
   with the `notes` and `meetings` tools when available; use `accounts`
   (mail.search, calendar.list) only when the user points at a mailbox or
   calendar.
4. Anything that must not be said yet (unannounced hires, pending deals,
   personal situations).
5. Who signs it, and whether there is a call to action.

If the user has already pasted the content, skip the questions and format.

## Workflow

1. Identify the format from the table and read its example file.
2. Collect facts. Every statement needs a source the user can verify: a number,
   a link, a named deliverable. Never invent a metric to make a line sound
   complete; write `[number?]` and ask.
3. Draft in the structure of the chosen format. Lead with the item that
   changes the reader's week.
4. Trim. Remove adjectives ("exciting", "huge"), remove history the reader
   already knows, merge duplicate items, turn paragraphs into one-line bullets
   where the format allows.
5. Check against the audience matrix: tone, length, vocabulary, call to
   action present when one is needed.
6. Return the text ready to paste in the target channel (plain text for chat,
   light markdown for wiki or email). Offer one alternative only when the user
   hesitated between two tones.

## Structure templates

### Status update (Progress, Plans, Problems)

One line per heading, 1 to 3 sentences each, numbers wherever possible. The
reader is someone with some but not much context on the team.

```
<Team> - <dates>
Progress: what shipped, what milestone moved, with figures.
Plans: the two or three things that matter next period.
Problems: what slows the team and what would unblock it.
```

The bigger the scope, the coarser the items: a squad lists features, a
company lists hires and deals.

### Newsletter

20 to 25 bullets, grouped in 3 to 5 sections (announcements, progress on
priorities, people, external mentions, social). One or two sentences per
bullet, a link in most of them, "we" voice. Company-wide impact only; team
detail belongs in status updates.

### FAQ answers

One question in a single sentence, one answer in one or two sentences, a link
to the authoritative source. Base answers on official communications; when the
answer is uncertain, say so and name who owns the real answer. Flag questions
that need an executive reply instead of a guess.

### Leadership note

```
Subject: <decision or risk in six words>
1. The point (one sentence: what happened or what is needed).
2. Why it matters (impact in numbers, deadline).
3. Options or recommendation (one line each).
4. What is needed from the reader, by when.
```

### Incident update

Same skeleton at every stage (investigating, identified, monitoring,
resolved): status, user impact, what is known, workaround, next update time.
No speculation about root cause until confirmed, no names of individuals.
See `examples/incident-update.md` for the sequence of posts.

## Quality bar

- First line stands alone: a reader who stops there still knows the headline.
- Every number has a unit and a comparison point when one exists.
- Links replace retelling: "see the design review" with the link, not a
  paragraph summarising it.
- Active voice, present or past tense, no passive "it was decided".
- Nothing confidential leaks: re-read with the "must not say" list.
- Fictional or placeholder content is marked as such; never pass an example as
  a real fact.

## Bundled files

- `examples/status-update.md` - two weekly updates at different scopes.
- `examples/newsletter.md` - a monthly company newsletter with sections.
- `examples/faq-answers.md` - a batch of FAQ answers with source links.
- `examples/incident-update.md` - the four posts of one incident, start to
  resolution.
