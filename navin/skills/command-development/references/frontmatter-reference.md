# Command frontmatter reference

Frontmatter is a YAML block between two `---` lines at the very top of the
file. Every field is optional.

```yaml
---
description: Short line for the command list
argument-hint: <required> [optional]
allowed-tools: Read, Grep, Bash(git:*)
model: inherit
disable-model-invocation: false
---
```

## description

- One line, under 80 characters, starting with a verb: "Generate release notes
  from merged PRs".
- Shown by the command palette, the help list and the system prompt index, so
  Navin can suggest the command when the user describes that task.
- Without it the list shows "no description"; always set it.

## argument-hint

- Documents the arguments: `<name>` for required, `[name]` for optional,
  `...` for repeatable. Example: `<issue-number> [priority]`.
- Purely informative; validation belongs in the body.

## allowed-tools

Restricts the tools of the command turn. Accepted forms:

```yaml
allowed-tools: Read, Write, Bash(npm test:*)
allowed-tools: [read_file, grep, exec]
allowed-tools:
  - read_file
  - apply_patch
```

Mapping applied by Navin (case-insensitive):

| Written | Navin tool |
|---|---|
| `Bash`, `Bash(...)` | `exec` |
| `Read` | `read_file` |
| `Write` | `write_file` |
| `Edit` | `edit_file`, `apply_patch` |
| `MultiEdit` | `apply_patch` |
| `Grep` | `grep` |
| `Glob` | `find_files` |
| `LS` | `list_dir` |
| `WebFetch` / `WebSearch` | `web_fetch` / `web_search` |
| `Task` | `spawn` |
| `TodoWrite` | `board` |
| any other name | used as is (`git`, `verify`, `ask_user`, `mcp_<server>_<tool>`) |

`skill` and `read_file` are always added. The parenthesised filter is kept as
documentation only. Granting the shell also enables `` !`cmd` `` snippets.

## model

Accepted so shared command files load without errors. Navin runs the command on
the model of the current session; switch the model before invoking when a
command needs a deeper one.

## disable-model-invocation

Accepted for compatibility. Markdown commands in Navin run only when the user
types them; Navin may suggest a command from its description but never runs it
on its own.

## YAML pitfalls

- Quote values containing `: ` or starting with `[`, `{`, `*`, `&`, `!`, `%`, `@`:
  `description: "Fix: flaky tests"`.
- Indent list items with two spaces under the key.
- Tabs are invalid in YAML indentation.
- The block must start on the first line; a blank line before `---` turns the
  whole file into the body.
- Unknown keys are kept and ignored, which lets one file carry metadata for
  several tools.
