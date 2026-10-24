# Full-featured plugin

A release plugin using every component type plus a per-project settings file.

```
release-helper/
├── plugin.json
├── README.md
├── CHANGELOG.md
├── skills/
│   └── release-process/
│       ├── SKILL.md
│       ├── references/semver.md
│       └── scripts/next_version.py
├── agents/
│   └── release-checker.md
├── commands/
│   ├── release.md
│   └── release-config.md
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── block-force-push.sh
│       └── session-context.sh
├── mcp.json
└── scripts/
    └── changelog.py
```

`agents/release-checker.md`:

```markdown
---
name: release-checker
description: Checks that a branch is ready to release (tests, changelog, version bump, open blockers). Use when the user asks "is this ready to release", "check the release", or after the release command prepared a version. Examples:

<example>
user: "Can we ship 2.4.0?"
assistant: "I'll run the release-checker agent on the current branch."
</example>
tools: ["read_file", "grep", "exec", "mcp_release-helper-tracker_list_issues"]
color: green
---

You verify release readiness. Load the `release-process` skill first.

Steps:
1. Run the test command found in the project (Makefile, package.json, pyproject).
2. Check CHANGELOG.md has an entry for the target version.
3. List open issues labelled "release-blocker" with the tracker tools.

Report: a table of checks with pass/fail and evidence, then a one-line verdict.
```

`commands/release.md`:

```markdown
---
description: Prepare a release (version, changelog, checks)
argument-hint: [major|minor|patch]
---

Settings for this project: @.navin/release-helper.local.md

Current version files:
!`python3 ${NAVIN_PLUGIN_ROOT}/skills/release-process/scripts/next_version.py --current`

1. Compute the next version for bump "${1:-patch}" with the script above.
2. Update the version files and CHANGELOG.md (use scripts/changelog.py).
3. Spawn the release-checker agent and report its verdict.
4. Never push or tag without the user's explicit go-ahead.
```

`hooks/hooks.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "exec",
        "hooks": [
          {"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/block-force-push.sh", "timeout": 10}
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [
          {"type": "command", "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/session-context.sh"}
        ]
      }
    ]
  }
}
```

`mcp.json`:

```json
{
  "mcpServers": {
    "tracker": {
      "type": "streamableHttp",
      "url": "https://tracker.example.com/mcp",
      "headers": {"Authorization": "Bearer ${TRACKER_TOKEN}"}
    }
  }
}
```

The server becomes `release-helper-tracker`; its tools are named
`mcp_release-helper-tracker_<tool>`.

`.navin/release-helper.local.md` (written by `/release-config`, git-ignored):

```markdown
---
enabled: true
channel: stable
require_changelog: true
---

Release notes go to the #releases channel.
```
