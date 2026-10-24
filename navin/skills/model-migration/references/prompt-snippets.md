# Prompt snippets for newer, more literal models

Each snippet fixes one observed behaviour. Apply it only when that behaviour is
requested or seen in the before/after comparison. Adapt the wording to the
application, place it next to related rules, and delete the older instruction
it replaces.

## 1. Tools called too often

Cause: emphasis written for older models (`CRITICAL`, `You MUST`, `ALWAYS`,
`NEVER skip`, `REQUIRED`) now overshoots. Rewrite only the tool-triggering
lines:

| Before | After |
|---|---|
| `CRITICAL: You MUST call search before answering.` | `Call search when the answer depends on facts not in the conversation.` |
| `ALWAYS read the file first.` | `Read the file before editing it.` |
| `NEVER skip the linter.` | `Run the linter after changing code.` |

Keep strong wording on safety, privacy and output-format rules.

## 2. Tool use triggers (too rare, or answering without looking)

```text
<tool_usage>
Before proposing a change to code, open and read the files involved. Do not
describe code you have not inspected. When the question depends on the current
state of the repository, the web or a database, use the matching tool first,
then answer from what it returned.
</tool_usage>
```

## 3. Explicit scope

```text
<scope>
Deliver exactly: <list the outputs>. Also update the tests that cover the
changed behaviour. Do not change unrelated files. If something outside this
scope looks broken, mention it at the end instead of fixing it.
</scope>
```

## 4. Overengineering guard

```text
<code_guidelines>
Make the smallest change that solves the request. Do not add files,
abstractions, configuration options, fallbacks or features that were not asked
for. Prefer editing existing code over creating new modules. Do not write
helpers for a single call site. Validate input only at system boundaries.
</code_guidelines>
```

## 5. Frontend aesthetics guard

```text
<frontend_aesthetics>
Avoid the generic look. Choose a distinctive type pairing instead of default
system fonts. Commit to a clear color palette with one dominant color and a
sharp accent, defined as CSS variables. Use atmosphere (gradients, subtle
textures, layered backgrounds) instead of flat white. Add purposeful motion for
entrances and key interactions, preferring CSS. Vary layouts between projects;
do not reuse the same hero, card grid and palette every time.
</frontend_aesthetics>
```

## 6. Verbosity and progress updates

```text
<communication>
Keep answers under <N> sentences unless the user asks for detail. After a
series of tool calls, give a two-line summary of what was done and what is
next. Do not repeat the question back. Use lists only for three items or more.
</communication>
```

If the new model became too terse, invert it: ask for a short explanation of
the reasoning behind each change.

## 7. Reasoning word sensitivity

When requests are sent without a reasoning budget, replace "think" and its
forms in the prompt:

| Before | After |
|---|---|
| `Think about the edge cases.` | `Consider the edge cases.` |
| `Think step by step.` | `Check each step in order.` |
| `What do you think?` | `What is your assessment?` |

## Integration rules

- Match the prompt's structure: reuse its tags or headings, or add tags in the
  same style.
- Put each snippet near the rules it relates to (coding rules with coding
  rules, tool rules with tool rules).
- Remove the old instruction the snippet replaces; contradictions confuse
  literal models more than missing rules.
- Re-run the baseline set after each snippet to confirm it fixed the problem
  without new regressions.
