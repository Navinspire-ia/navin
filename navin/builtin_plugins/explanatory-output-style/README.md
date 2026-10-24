# explanatory-output-style

The agent teaches while it works. Before or after it writes code it adds a short Insight block with 2 or 3 points about why the code looks that way: the choice made, the pattern this codebase follows, the pitfall avoided. Insights stay in the chat, never in your files.

The instructions are part of the system prompt on every turn of the main agent, so they survive compaction. Subagents do not get them: they report to the main agent, not to you.

## Commands

- `/output-style explanatory` - select it for this project (`.navin/output-style.local`).
- `/output-style explanatory --personal` - select it for every project (`~/.navin/output-style`).
- `/output-style` - list the styles, the active one marked with `*`.
- `/output-style off` - back to the default style.

You can also set `NAVIN_OUTPUT_STYLE=explanatory` in the environment; it wins over the files.

## Example insight block

```
> Insight
> - The cache key includes the file mtime, so an edited file is re-read without a restart.
> - This module returns "" for the default style, which keeps the prompt prefix identical for cache hits.
```

## Custom style example

Create `.navin/output-styles/terse-review.md` (project) or `~/.navin/output-styles/terse-review.md` (personal):

```markdown
---
name: terse-review
description: Short answers, then a one line risk note after each change
---
Answer in as few words as possible. After every code change, add one line
starting with "Risk:" naming what could break.
```

Then run `/output-style terse-review`. On a name clash the project file wins over the personal one, which wins over the builtin styles.
