# How to Configure MCP Tools in Navin

This guide adds an MCP server to Navin so the agent can use external tools through the Model Context Protocol.

## What you will build

- a working Navin agent in the desktop app
- one MCP integration configured through Apps / Settings
- a restricted set of MCP tools exposed to the model

## When to use this

Use MCP when the capability you need already exists as an MCP server, or when you want external tools to be managed outside Navin core.

## Before you start

1. Open the Navin desktop app.
2. Confirm a provider and model under **Settings → Providers** and **Settings → Models**.
3. Install any MCP server runtime separately if the integration needs one (many presets document `npx`, `uvx`, or a remote HTTP endpoint - follow that server's own install notes).

## Configure in the app

1. Open **Apps** (or the MCP / integrations area in Settings).
2. Choose a known integration preset, or add a custom stdio, HTTP, or SSE server.
3. Limit the enabled tools when the server exposes more than the task needs.
4. Save and restart when the app prompts you.
5. Mention the integration with `@` in the next chat message and ask for a small test action.

### Advanced JSON (optional)

If you maintain config by hand, MCP servers can also appear under tools in the local config file:

```json
{
  "tools": {
    "mcpServers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
        "enabledTools": ["read_file"]
      }
    }
  }
}
```

Prefer the Apps UI for everyday setup. Restart Navin when prompted, then ask a question that requires the MCP tool.

## DebugMCP preset (Debug mode)

Navin can auto-enable a built-in **DebugMCP** preset so Debug mode can talk to a local debug bridge. Opt out with `"autoEnableMcpPresets": false` under `tools` in advanced config if you do not want that preset.

| Field | Value |
| --- | --- |
| Preset name | `debugmcp` |
| URL | `http://127.0.0.1:3001/mcp` |
| Requires | A DebugMCP (or equivalent) debug extension running in your code editor for live tools |

Then run `/debug` (or Mode → Debug). The agent checks MCP status first against localhost only. If the editor extension is down, status reports clearly and the agent falls back to logs and other debug steps. Once the extension starts, reconnect works without reinstalling the Navin preset.

Details: [Expert tools (DebugMCP)](../navin_dev/en/expert-tools.md).

## Production notes

- Prefer enabling only the tools you need.
- Raise tool timeouts for slow MCP operations when Settings offer that control.
- Use HTTP MCP only for endpoints you trust.
- Keep MCP server commands stable when you share a project setup with teammates.

## Security notes

- Stdio MCP starts a local process; review the command before enabling it.
- HTTP/SSE MCP uses Navin's SSRF guard.
- Allow private HTTP MCP hosts only with narrow SSRF whitelist CIDRs in Settings / advanced tools config.
- Do not place secrets in command arguments when environment variables or headers can be used.

## Troubleshooting

- Confirm the MCP server runs on its own before blaming Navin.
- Open Apps / MCP settings and check that the server is enabled and tools are listed.
- If an HTTP MCP URL is blocked, check whether it points to loopback or a private address that needs explicit allowlisting.
- After changing MCP config, restart when the app asks, then retry with `@` mention.

## Related docs

- [MCP tools for AI agents](./mcp-tools-for-ai-agents.md)
- [Expert tools (Review / Security / Debug)](../navin_dev/en/expert-tools.md)
- [Composer modes](../navin_dev/en/modes.md)
