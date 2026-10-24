# Example settings file

Save as `<workspace>/.navin/code-guard.local.md` for a plugin named
`code-guard`.

```markdown
---
# Master switch: false makes every code-guard hook exit at once.
enabled: true

# block refuses the tool call, warn only reports it.
mode: block

# Paths (workspace-relative prefixes) that tools must not write.
protected_paths:
  - migrations/
  - infra/prod/
  - .github/workflows/

# Files larger than this are refused (kilobytes).
max_file_kb: 512

# Shown in messages so the user knows whom to ask.
owner: "platform team"
---

Extra rules for the agent, read as free text:

- Generated files under src/gen/ are rebuilt by `make gen`; edit the schema
  instead.
- Ask before changing anything under config/feature-flags/.
```

.gitignore entry:

```
.navin/*.local.md
```
