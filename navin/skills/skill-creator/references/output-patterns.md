# Output patterns for skills

Skills that produce something (a file, a report, a message) should state the expected shape of the output. Pick one of these patterns and show a short example in SKILL.md or in a reference file.

## Template

For outputs with a fixed structure (reports, briefs, release notes), give the skeleton with placeholders in angle brackets and one sentence per section on what goes there.

```markdown
# <Title>

**Context**: <one paragraph: who asked, why now>

## Findings
<3 to 6 bullets, each verifiable, strongest first>

## Recommendation
<one decision, the trade-off accepted, the next step with an owner>
```

State whether the template is strict (keep every heading) or a guide (adapt headings to the content).

## Example-driven

When tone and length matter more than structure (emails, slide text, UI copy), show one good example and one bad example with the reason.

```markdown
Good: "Deploy paused at 14:10; cause found (expired certificate); fix in review; next update 15:00."
Bad:  "We are looking into some issues with the deploy and will keep you posted." (no cause, no time, no next step)
```

## Schema

For machine-readable outputs, give the JSON shape with types and one filled example, and say which fields are required.

```json
{"ok": true, "output": "path/to/file", "items": [{"id": "a1", "status": "done"}], "warnings": []}
```

Scripts that print JSON should also return a non-zero exit code when `ok` is false, so the agent can branch without parsing.

## Quality bar

List the properties every output must have. Keep them checkable.

- Language: the one the user asked for, consistently.
- Punctuation: plain hyphen, never em dash or en dash.
- Numbers carry a unit and a period.
- Every claim in a report points to its evidence (command output, file, line).
- Files are editable (real text, native tables), never screenshots of content.

## Delivery message

End with what the user receives: the file(s), what changed, what was checked and how, and what was out of scope. Three short paragraphs beat a long bullet list.

```markdown
The deck is updated (deck-v2.pptx): slides 3 and 7 carry the Q3 numbers, slide 9 was removed.
Checked: inventory before/after, package validation, thumbnail sheet reviewed.
Not changed: the template, the other slides, the speaker notes.
```
