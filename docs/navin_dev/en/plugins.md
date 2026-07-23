# Plugin packs

Packs are self-contained bundles of **skills**, **MCP servers**, and configuration that extend Navin at runtime — no restart required.

## Anatomy of a pack

```
my-pack/
├── pack.json          # manifest: name, version, description
├── skills/            # one folder per skill, each with SKILL.md
│   └── my-skill/
│       └── SKILL.md
└── mcp/
    └── servers.json   # MCP server definitions (merged with a my-pack- prefix)
```

- Skills become available to the agent immediately when the pack is enabled (workspace skills > pack skills > built-in skills in case of name conflicts).
- MCP servers are merged into the main configuration namespaced as `<pack>-<server>` and hot-reloaded.

## Managing packs

### From the WebUI

**Settings → Skills → Plugin packs**: install from a Git URL or a local path, enable/disable with a switch, remove with confirmation.

### From chat — `/pack`

| Subcommand | Effect |
| --- | --- |
| `/pack list` | Lists installed packs with status and contents. |
| `/pack install <git-url\|path>` | Installs from a Git repository or a local directory. |
| `/pack enable <name>` | Enables the pack (skills + MCP servers become active). |
| `/pack disable <name>` | Disables it without uninstalling. |
| `/pack remove <name>` | Uninstalls the pack and cleans up its MCP servers. |

### From the agent

The `pack-builder` skill teaches the agent to create, audit, and manage packs. You can simply ask: *"Build me a pack with a skill for our internal API and an MCP server for our docs."*

## State and storage

- Packs live in `~/.navin/plugins/`, with a `state.json` tracking enabled/disabled status and MCP servers managed by each pack.
- Uninstalling a pack removes its namespaced MCP servers from the main config automatically.
