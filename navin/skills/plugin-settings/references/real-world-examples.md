# Real-world settings patterns

## 1. Guard plugin: protected paths per project

Settings:

```yaml
---
enabled: true
protected_paths: ["migrations/", "infra/prod/"]
mode: block        # block | warn
---
```

- A PreToolUse hook on `write_file|edit_file|apply_patch` reads the list and
  exits 2 (mode block) or prints a `systemMessage` (mode warn).
- A `/guard-settings` command edits the list.

## 2. Review plugin: reviewer persona and strictness

```yaml
---
strict: true
focus: ["security", "tests"]
reviewer: "backend team"
---
Flag any new dependency. Our API is public: breaking changes need a note.
```

- The review agent reads the frontmatter for strictness and focus, and the
  body as house rules.
- The `/review` command inlines the file with `@.navin/review.local.md`.

## 3. Loop plugin: Stop hook driven by a state file

```yaml
---
active: true
iteration: 3
max_iterations: 10
completion_marker: "ALL TESTS PASS"
---
Re-run the failing tests, fix the next failure, then report.
```

- A Stop hook blocks with the body as the reason while `active` is true, the
  marker is absent from `last_assistant_message` and `iteration` is below
  `max_iterations`; it increments `iteration` each time.
- A `/loop-start` command creates the file, `/loop-stop` sets `active: false`.

## 4. Context plugin: SessionStart summary

```yaml
---
include_git: true
include_board: false
---
```

- The SessionStart hook prints only the sections enabled in the settings.

## Common traits

- Missing file means defaults, never an error.
- One command creates and edits the file; users do not hand-edit YAML.
- `.navin/*.local.md` is git-ignored.
- The validator script runs after every write.
