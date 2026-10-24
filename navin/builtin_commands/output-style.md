---
description: List, switch or clear the output style (default, explanatory, learning or your own)
argument-hint: "[name|off] [--personal]"
allowed-tools: exec
---

# Output style

Result of `navin python -m navin.agent.output_styles $ARGUMENTS` in this workspace:

!`${NAVIN_PYTHON} -m navin.agent.output_styles $ARGUMENTS`

Show that result to the user as is, in a short answer. If it says the command failed, run `navin python -m navin.agent.output_styles $ARGUMENTS` with `exec` in the workspace, then report.

Usage reminder:

- `/output-style` lists the styles, the active one is marked with `*`.
- `/output-style learning` selects a style for this project (`.navin/output-style.local`).
- `/output-style learning --personal` selects it for every project (`~/.navin/output-style`).
- `/output-style off` clears the project choice (`off --personal` clears the personal one).
- `/output-style show <name>` prints the instructions of a style.
- Custom styles: `.navin/output-styles/<name>.md` or `~/.navin/output-styles/<name>.md` with `name` and `description` frontmatter.

The change applies from the next turn. If the user had a style active, follow the newly selected one from now on.
