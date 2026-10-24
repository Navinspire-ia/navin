# Minimal plugin

One command, nothing else. Good first plugin and a template for quick tools.

```
greet/
├── plugin.json
└── commands/
    └── standup.md
```

`plugin.json`:

```json
{
  "name": "greet",
  "version": "0.1.0",
  "description": "Daily stand-up summary"
}
```

`commands/standup.md`:

```markdown
---
description: Summarize my work since yesterday for the stand-up
argument-hint: [extra notes]
allowed-tools: exec(git:*), read_file
---

Commits since yesterday:
!`git log --since=yesterday --oneline --author="$(git config user.email)"`

Write a stand-up update with three short sections: Done, Doing, Blocked.
Use the commits above and these notes from the user: $ARGUMENTS
Keep it under 120 words.
```

Install and use:

```
/pack install /path/to/greet
/standup waiting on API keys
```

The same command works without a plugin: put `standup.md` in
`<workspace>/.navin/commands/` or `~/.navin/commands/`.
