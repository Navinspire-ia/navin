---
name: skill-development
description: Write, restructure or improve a Navin skill (SKILL.md frontmatter, trigger description, progressive disclosure with references/, examples/, scripts/). Use when the user wants to "create a skill", "add a skill to a plugin", "write a new skill", "improve a skill description", "why does my skill not trigger", "organize skill content", or asks for skill best practices.
---

# Skill Development

A skill is a folder that teaches Navin how to do one kind of work well: a
workflow, a domain, a tool, a file format. It turns a general agent into a
specialist for that task without growing the base prompt.

The `skill-reviewer` subagent reviews a finished skill; `scripts/validate_skill.py`
lints one. For packaging the skill into a plugin, see `plugin-structure`.

## What a skill can carry

- **Procedures**: the steps, in order, with the decisions that matter.
- **Domain knowledge**: schemas, business rules, conventions, gotchas.
- **Tool know-how**: how to drive a CLI, an API or a file format correctly.
- **Bundled resources**: scripts that do the deterministic parts, references
  with the long details, templates and sample files.

## Anatomy

```
my-skill/
├── SKILL.md          # required: frontmatter + core instructions
├── references/       # optional: long docs, read only when needed
├── examples/         # optional: complete working samples
├── scripts/          # optional: executable helpers
└── assets/           # optional: templates, images, fonts used in outputs
```

Frontmatter, at the very top of `SKILL.md`:

```yaml
---
name: my-skill
description: <what it does>. Use when the user asks to "<phrase>", "<phrase>", or <situation>.
---
```

- `name`: kebab-case, equal to the folder name.
- `description`: the only text Navin sees when choosing a skill. It decides
  whether the skill is ever used.

## Progressive disclosure: how Navin loads a skill

1. **Name**: every skill name is listed in the system prompt (a few tokens).
2. **Description**: returned by `skill action=find` when the task looks like a
   match. Keep it under about 1024 characters.
3. **Body**: loaded with `skill action=read name=<skill>` once Navin decides to
   use it. Aim for 1,500 to 2,000 words; stay under 5,000.
4. **Resources**: files the body points to, opened with `read_file` or run with
   `exec` only when the step needs them. Unlimited size; scripts can run
   without ever being read into context.

Consequences:

- Put in the body what is needed on almost every use. Move the rest to
  `references/` and say exactly when to open each file.
- Never duplicate content between the body and a reference.
- Large tables, API listings, schemas and edge-case catalogs belong in
  references. Code that would be rewritten each time belongs in scripts.

### Frontmatter samples

Minimal, enough for discovery:

```yaml
---
name: pdf-forms
description: Fill, flatten and extract PDF form fields with pypdf. Use when the user asks to "fill this PDF form", "read the form fields", "flatten a PDF", or attaches a fillable PDF.
---
```

With Navin metadata: `requires.bins` / `requires.env` hide the skill when a
binary or variable is missing, and `always: true` loads the body on every turn
(reserve it for tiny, universal skills):

```yaml
---
name: cloud-aws
description: Operate AWS with the aws CLI (S3, EC2, IAM, CloudWatch). Use when the user asks to "list my buckets", "check this EC2 instance", "read CloudWatch logs", or mentions an AWS resource ARN.
metadata: {"navin":{"emoji":"🟠","category":"devops","requires":{"bins":["aws"],"env":["AWS_PROFILE"]}}}
---
```

`tags` feeds the skills graph; other keys (`version`, `license`) are kept as
metadata but change no behaviour. Navin has no per-skill tool restriction field: a skill cannot limit
the tools available while it is in use.

### Progressive disclosure in practice

A body that keeps the always-needed steps and points at the rest:

```markdown
## Export

1. Run `python3 scripts/<export-helper>.py --format csv <input>`.
2. If the API returns 429, read `references/<rate-limits>.md` before retrying.
3. For the full field list, open `references/<schema>.md`; do not guess names.
```

The linter checks every `references/...`, `examples/...`, `scripts/...` or
`assets/...` path named in the body, so cite only files that exist and use
angle-bracket placeholders in templates.

## Process

### 1. Collect concrete examples

Before writing, get three to five real requests the skill must handle (ask the
user, or derive them from the conversation). For each, note what a perfect
result looks like. Skip this only when the use is already crystal clear.

### 2. Plan the reusable parts

For each example, ask what you would otherwise rebuild every time:

| Repeated work | Put it in |
|---|---|
| Same code written again and again | `scripts/` |
| Same documentation looked up | `references/` |
| Same starting files copied | `assets/` |
| Same working sample needed | `examples/` |

Worked plans for three sample skills:

- **pdf-forms** ("fill this PDF form"): the same pypdf code is rewritten each
  time -> `scripts/<fill-form>.py`; field naming rules -> one reference.
- **frontend-boilerplate** ("start a dashboard app"): the same starter files
  are copied each time -> `assets/<template>/` folder copied into the workspace.
- **warehouse-queries** ("how many active users last week"): table names and
  join keys are rediscovered each time -> `references/<schema>.md`, grep
  patterns for large tables listed in the body.

### 3. Create the folder

Where it lives decides who sees it:

- `<workspace>/.navin/skills/<name>/`: this project.
- `~/.navin/skills/<name>/`: every project of this user.
- `<plugin>/skills/<name>/`: everyone who installs the plugin.

Create only the subfolders you will fill. Built-in skills shipped with Navin
live in `navin/skills/<name>/` in the source tree; the same layout applies.

```bash
mkdir -p .navin/skills/pdf-forms/scripts .navin/skills/pdf-forms/references
$EDITOR .navin/skills/pdf-forms/SKILL.md
```

Navin re-reads skills from disk; a new folder is visible on the next message.

### 4. Write the resources first, then SKILL.md

Resources first: the body then only has to point at them. Test every script
by running it.

Write the body for another capable agent that does not know this domain:

- Start with one paragraph on the purpose.
- Give the workflow as numbered steps with the decisions that matter.
- State the non-obvious rules and gotchas; skip what any agent already knows.
- End with a **Resources** section that lists each bundled file and when to use
  it.

### 5. Validate

```bash
# One skill folder (or its SKILL.md); exit 1 on any critical issue
python3 <this skill>/scripts/validate_skill.py .navin/skills/pdf-forms
python3 <this skill>/scripts/validate_skill.py .navin/skills/pdf-forms/SKILL.md

# Machine-readable report: {"critical", "warnings", "stats", "passed"}
python3 <this skill>/scripts/validate_skill.py .navin/skills/pdf-forms --json

# Whole plugin, when the skill ships in one
python3 <plugin-structure skill>/scripts/validate_plugin.py <plugin-dir>
```

Critical: missing frontmatter, `name` or `description`, body over 5,000
words, a cited file that does not exist. Warnings: description over 1,024 or
under 80 characters, fewer than three quoted phrases, no "Use when", "you" in
the description, body over 3,000 words, more than three second-person
phrases, long dashes, resource files never mentioned, scripts not executable.

Then test triggering: ask for the task in the words a user would use and check
that Navin finds and reads the skill. Ask something adjacent and check it does
not.

### 6. Iterate

Use the skill on real tasks. When Navin hesitates, misses a step or reads the
wrong reference, fix the body or the pointer, not the symptom.

## Writing the description

The description answers two questions: what does the skill do, and when should
it be used. Write it in third person and quote the phrases users actually type.

Good:

```yaml
description: Generate and fix database migrations (Alembic, Prisma, Rails). Use when the user asks to "add a column", "write a migration", "migration failed", "rollback a migration", or changes a model that maps to a table.
```

Weak:

```yaml
description: Helps with databases.
```

Also weak, and why:

```yaml
# Second person, says how instead of when
description: Helps you run alembic revision --autogenerate.
# Trigger phrases nobody types
description: Database schema evolution management capability. Use when "schema evolution" is required.
```

Better for the same skill, with situations as well as phrases:

```yaml
description: Review and fix pull requests on GitHub (diff reading, inline comments, CI logs). Use when the user asks to "review this PR", "why is CI red", "answer the review comments", "merge when green", or pastes a pull request URL.
```

Checklist:

- Names the capability and the main tools or formats.
- Contains three or more literal trigger phrases in quotes.
- Mentions situations, not only phrases ("or changes a model that ...").
- Avoids "you"; does not explain how, only what and when.

## Writing style for the body

- Imperative form: "Run the linter", "Read `references/<topic>.md` before calling
  the export endpoint". Not "You should run".
- Objective and specific: commands, paths, field names, thresholds.
- Short sections with headings; tables for options; code blocks for anything
  that must be typed exactly.
- Plain hyphens only, no long dashes.

Good:

```markdown
Run `pytest -x -q` before committing. If a test fails, read the first
traceback only and fix that test before running the suite again.
```

Bad:

```markdown
It is probably a good idea to run the tests at some point, and maybe
look at the errors and consider fixing them if that seems to make sense.
```

Good (a gotcha stated as a rule):

```markdown
The export endpoint paginates with `cursor`, not `page`; stop when
`next_cursor` is null.
```

## Common mistakes

1. **Vague trigger**: the description says what but not when. Navin never
   picks the skill. Add quoted phrases and situations.
2. **Everything in SKILL.md**: 8,000 words in the body cost context on every
   use. Move details to references.
3. **Orphan resources**: files exist but the body never mentions them, so they
   are never read. List each one with its purpose.
4. **Second person and chatter**: "You might want to consider ...". State the
   rule.
5. **Untested scripts**: a script that fails on first use breaks trust in the
   whole skill. Run each one.
6. **Duplicated general knowledge**: explaining what git is. Keep only what is
   specific to this domain.

## Validation checklist

- [ ] `SKILL.md` has frontmatter with `name` (equal to the folder) and
      `description`.
- [ ] Description: third person, what and when, quoted trigger phrases.
- [ ] Body: imperative, 1,500 to 2,000 words (under 5,000).
- [ ] Every file in `references/`, `examples/`, `scripts/` is cited in the body
      with when to use it; every cited file exists.
- [ ] Scripts run and are executable.
- [ ] Triggering tested with realistic requests; no false triggers on nearby
      topics.

## Resources

- `references/skill-anatomy.md` - templates, sizing guide, worked example of
  splitting a long skill, description patterns.
- `scripts/validate_skill.py` - lint one skill folder (frontmatter, size,
  style, missing or orphan resources).
