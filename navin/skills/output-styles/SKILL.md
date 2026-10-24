---
name: output-styles
description: Use when the user asks to "change the output style", "make the agent explain its choices", "switch to learning mode" or "write a custom output style". Explains the builtin and custom styles and how to select them.
---

# Output styles

An output style changes how the main agent talks while it works, without changing what it can do.

## Builtin styles

- `default`: adds nothing to the system prompt.
- `explanatory`: the agent adds short Insight blocks about implementation choices and codebase patterns.
- `learning`: the user writes the meaningful decision points; the agent leaves `TODO(human)` markers and explains what is expected.

## Selecting a style

Use the `/output-style` command:

```text
/output-style list
/output-style set explanatory
/output-style clear
```

The active style is resolved in this order: the `NAVIN_OUTPUT_STYLE` environment variable, the project file `.navin/output-style.local`, the personal file `~/.navin/output-style`, then `default`. An unknown name falls back to `default`.

## Custom styles

A custom style is a Markdown file with `name` and `description` frontmatter. The body is the instruction added to the system prompt.

```markdown
---
name: terse
description: Answers in at most three sentences.
---
Keep every answer under three sentences unless the user asks for detail.
```

Put it in `.navin/output-styles/` (project) or `~/.navin/output-styles/` (personal). On a name clash the project style wins over the personal one, which wins over a builtin.

## When to suggest a style

- The user keeps asking "why did you do it that way": suggest `explanatory`.
- The user wants to learn the codebase by doing: suggest `learning`.
- The user wants a house tone or format: help write a custom style file and activate it.
