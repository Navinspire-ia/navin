# Testing commands

## Test matrix

Run each command at least with:

| Case | Input | Expect |
|---|---|---|
| No arguments | `/name` | usage message or the documented default |
| One argument | `/name foo` | `$1` = foo, `$ARGUMENTS` = foo |
| Quoted words | `/name "two words" x` | `$1` = two words, `$2` = x |
| Extra arguments | `/name a b c d` | extras kept in `$ARGUMENTS` |
| Missing file | `/name missing.txt` with `@$1` | literal `@missing.txt`, turn reads or reports |
| Shell denied | body with `!` but no shell in allowed-tools | snippet left as text, nothing ran |

## Reading the expansion

The turn starts with `[Command /name args]` followed by the expanded body.
Check that placeholders are gone, files are inlined in `<file>` blocks and
shell output sits in fenced blocks.

## Debugging

| Symptom | Cause |
|---|---|
| "Unknown command" | file not under a scanned folder, plugin disabled, or name invalid (must start with a letter or digit) |
| Built-in behaviour instead of the file | the name belongs to a Navin built-in; rename the file |
| Frontmatter shown in the prompt | `---` not on the first line, or never closed |
| `$1` empty | arguments not separated by spaces, or quotes unbalanced |
| `@file` not inlined | outside the workspace or plugin folder, or path typo |
| `!` snippet not executed | `allowed-tools` does not grant the shell |

## Regression checks for plugins

Keep a short `TESTING.md` in the plugin with the commands and expected outcome
for each, and rerun it after every change. The `plugin-validator` agent checks
the structure; behaviour needs these manual runs.
