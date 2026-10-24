# Skill anatomy in depth

## Template

```markdown
---
name: <kebab-name>
description: <Capability, with the main tools or formats>. Use when the user asks to "<phrase 1>", "<phrase 2>", "<phrase 3>", or <situation>.
---

# <Title>

<One paragraph: the goal and what a good result looks like.>

## Workflow

1. <Step> - <decision rule if any>
2. <Step>
3. <Step>

## Rules

- <Non-obvious rule>
- <Gotcha with its fix>

## Quick reference

| Option | Meaning | Default |
|---|---|---|

## Resources

- `references/<file>.md` - <what it holds>; read when <situation>.
- `scripts/<file>.py` - <what it does>; run `python3 scripts/<file>.py <args>`.
- `examples/<file>` - <what it shows>.
```

## Sizing guide

| Part | Target | Hard limit |
|---|---|---|
| Description | 150 to 600 characters | 1024 characters |
| Body | 1,500 to 2,000 words | 5,000 words |
| One reference file | 2,000 to 5,000 words | none, but split by topic |
| Script | whatever the job needs | must run standalone |

## Splitting a long skill

Start: a 6,000 word SKILL.md for a payment API.

1. List the sections and mark each: **every time** (auth flow, error
   handling, idempotency rule) or **sometimes** (webhook catalog, refund
   edge cases, currency table).
2. Keep the "every time" parts in the body, condensed.
3. Move each "sometimes" topic to its own reference:
   `references/webhooks.md`, `references/refunds.md`,
   `references/currencies.md`.
4. In the body, replace each moved section with one line saying when to read
   it: "Before handling a webhook, read `references/webhooks.md` for the
   event list and signature check."
5. Move repeated code (signature verification) to
   `scripts/verify_signature.py` and give its usage line.

Result: a 1,800 word body and three references read only when relevant.

## Description patterns

Capability + phrases:

```
Create and edit Excel workbooks (formulas, charts, pivot tables) with openpyxl. Use when the user asks to "make a spreadsheet", "add a chart to this xlsx", "fix my formulas", or attaches an .xlsx file.
```

Capability + situation:

```
Diagnose failing CI pipelines on GitHub Actions and GitLab CI. Use when a pipeline is red, when the user pastes a CI log, or asks "why did the build fail".
```

Capability + exclusion (to avoid false triggers):

```
Write unit tests for Python code with pytest. Use when the user asks to "add tests", "test this function", "raise coverage". Not for end-to-end browser tests.
```

## Where skills are found

Navin reads skills from, in priority order: the workspace `.navin/skills/`,
other workspace tool folders (`.<tool>/skills/`), the user's home folders
(`~/.navin/skills/`, ...), enabled plugins (`<plugin>/skills/`), then the
built-in library. The first skill with a given name wins.

## Scripts

- Start with a shebang (`#!/usr/bin/env python3` or `#!/usr/bin/env bash`) and
  `chmod +x`.
- Print a usage line on wrong arguments; exit non-zero on failure.
- Read input from arguments or files, write results to stdout or a named file.
- No network or secrets unless the skill is about that service; document any
  environment variable they need.

## Assets

Templates and binary files used in the output (a slide master, a logo, a
boilerplate project). The body tells Navin to copy them, not to read them.
