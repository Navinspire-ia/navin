# Issue intake (humans and agents)

GitHub issue **forms** (`.github/ISSUE_TEMPLATE/*.yml`) only run in the
browser. `gh issue create`, `POST /repos/{owner}/{repo}/issues`, and every
other API client skip them. That is why an agent that "just opens an issue"
produces a blank body with no kind label.

This file is the shared contract. The enforcer for agents is
`scripts/new-issue.py`. The machine-readable field list is
`.github/issue-intake.schema.json`.

## Humans

Use the GitHub chooser. Blank issues are disabled. Pick Bug, Regression,
Feature, Support, Docs, or Security.

## Agents

MUST use:

```bash
python3 scripts/new-issue.py --payload issue.json --confirm-searched --confirm-no-secrets
```

MUST NOT:

- call `gh issue create` or the Issues REST/GraphQL API directly
- invent extra kind labels
- apply `area:*`, `platform:*`, `packaging:*`, `severity:*`, `priority:*`,
  or `status:*` (maintainers add those at triage)
- open a public issue for an unreleased vulnerability

Discover the schema:

```bash
python3 scripts/new-issue.py --schema
```

Dry-run (no GitHub call):

```bash
python3 scripts/new-issue.py --payload issue.json --confirm-searched --confirm-no-secrets --dry-run
```

### Kinds

| kind | label | title prefix | when |
| --- | --- | --- | --- |
| `bug` | `bug` | `[Bug]: ` | defect on a specific surface or installer |
| `regression` | `regression` | `[Regression]: ` | used to work, then stopped |
| `feature` | `enhancement` | `[Feature]: ` | new capability or product surface |
| `support` | `support` | `[Support]: ` | question, debugging help |
| `docs` | `documentation` | `[Docs]: ` | docs gap or copy fix |
| `security` | n/a | n/a | **refused**. Email `security@navinspire.com`. See [SECURITY.md](../SECURITY.md). |

Required and optional fields per kind live in
`.github/issue-intake.schema.json`. Enums (`surface`, `channel`,
`installations`, ...) must match that file exactly.

### Payload example (bug)

```json
{
  "kind": "bug",
  "title": "MSI setup fails on Windows 11 24H2",
  "surface": "Desktop app",
  "exact_version": "2.0.4",
  "channel": "latest stable",
  "installations": ["Windows .msi"],
  "reproduction": "1. Run the MSI\n2. Setup exits 1603",
  "expected": "Setup completes and `navin --version` works.",
  "actual": "Exit 1603. No Start-menu shortcut.",
  "tried": "Re-downloaded the MSI; same hash. Repair install also fails."
}
```

Write the JSON to a temp file outside the worktree (for example `/tmp/navin-issue.json`).
Do not commit payloads.

### Confirmations

`--confirm-searched` means you searched existing issues first.
`--confirm-no-secrets` means the payload has no API keys, tokens, or
`~/.navin/config.json` dumps. Both flags are required.

## Why this shape

The rendered issue body uses the same headings as the human forms
(`Surface`, `Exact version`, `Reproduction steps`, ...). Triage then looks
the same whether a person clicked the chooser or an agent ran the script.
