# Plugins

Plugins are self-contained bundles of **skills**, **agents**, **slash commands**, **hooks** and **MCP servers** that extend Navin at runtime - no restart required. Plugins written for Claude Code install unchanged.

## Anatomy of a plugin

```
my-plugin/
├── plugin.json                 # manifest: name, version, description, author, homepage
│   (or .claude-plugin/plugin.json, the Claude Code layout)
├── skills/<skill>/SKILL.md     # skills, loaded next to built-in and workspace skills
├── agents/*.md                 # subagents, listed under "Project Subagents"
├── commands/*.md               # slash commands, run as /my-plugin:<command>
├── hooks/hooks.json            # lifecycle hooks (PreToolUse, Stop, ...)
└── mcp.json                    # {"mcpServers": {...}}, merged with a my-plugin- prefix
    (or .mcp.json, or an "mcpServers" key in the manifest)
```

- Any single component makes a valid plugin (a commands-only plugin installs fine).
- `${CLAUDE_PLUGIN_ROOT}` and `${NAVIN_PLUGIN_ROOT}` both expand to the plugin folder in hooks and MCP commands.
- Skills, agents and commands are picked up on the next message (workspace > plugin > built-in on a name clash). MCP servers are merged into the main configuration as `<plugin>-<server>` and connected at once.

## Managing plugins

### From chat - `/plugin`

| Subcommand | Effect |
| --- | --- |
| `/plugin` or `/plugin list` | Installed plugins with status, components, the state of each MCP server (connected / not connected) and the registered marketplaces. |
| `/plugin install <source>` | Installs and reports what the plugin brings (skills, agents, `/plugin:command` names, hooks, MCP servers), then connects its MCP servers in the running agent. |
| `/plugin uninstall <name>` | Removes the plugin and disconnects its MCP servers. |
| `/plugin enable <name>` / `disable <name>` | Toggles it without uninstalling; MCP servers follow. |
| `/plugin marketplace add <owner/repo\|git-url\|path>` | Registers a marketplace (a repository with `.claude-plugin/marketplace.json`). |
| `/plugin marketplace list` | Marketplaces and the `name@marketplace` plugins they offer. |
| `/plugin marketplace update [name]` | Refreshes the marketplace clones. |
| `/plugin marketplace remove <name>` | Forgets a marketplace; installed plugins stay. |

`<source>` is one of:

| Source | Example |
| --- | --- |
| A marketplace plugin | `deploy@acme-tools` (or just `deploy` when one marketplace offers it) |
| A GitHub repository | `acme/tools`, or `acme/tools#plugins/deploy` for one folder of a monorepo |
| A git URL | `https://git.example/tools.git` |
| A local folder | `~/dev/my-plugin` |
| An npm package | `@acme/my-skill` (fetched with `npm pack`, nothing is executed) |

`/pack` remains an alias of `/plugin`.

### From the WebUI

**Settings → Skills → Plugins**: install from a Git URL, a GitHub `owner/repo`, a marketplace plugin, a local path, an npm package or an uploaded archive; enable/disable with a switch; remove with confirmation.

### From the agent

The `pack-builder` skill teaches the agent to create, audit and manage plugins. The `mcp` tool adds, lists and removes MCP servers live. You can simply ask: *"Install the acme/tools plugin"* or *"Build me a plugin with a skill for our internal API and an MCP server for our docs."*

## MCP servers

- A server declared with a `url` is connected over streamable HTTP; when the endpoint answers 405 (an SSE-only server), Navin retries over SSE on its own and tells you in the log. Set `"type": "sse"` to skip the probe.
- A stdio server (`npx`, `uvx`, `bunx`, `docker`, `node`, `python`) runs as a local process with your rights; on Windows the `.cmd` shims are resolved for you.
- A server that does not answer costs one connect attempt per cooldown window, never a frozen chat.

## State and storage

- Plugins live in `~/.navin/plugins/<name>/`; `state.json` tracks enabled/disabled status and the MCP servers each plugin owns; `marketplaces.json` lists the marketplaces and their clones under `.marketplaces/`.
- Uninstalling a plugin removes its namespaced MCP servers from the main config automatically.
- An organization policy (`allowManagedPluginsOnly`) can block plugins and MCP servers; `/plugin list` shows them as blocked by policy.
