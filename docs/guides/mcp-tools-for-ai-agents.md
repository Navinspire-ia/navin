# MCP tools for AI agents in Navin

Navin can connect MCP servers and expose their tools to the agent alongside built-in file, shell, web, schedule, image generation, and subagent tools.

Everything below happens in the **Navin desktop app**. Prefer **Settings → MCP** (or Apps / MCP presets) over hand-editing files.

## What you will set up

- Navin open with a provider and model configured
- one MCP server added from Settings
- only the tools you choose enabled for the agent

## When to use this

Use MCP when a tool already exists as an MCP server, when another application publishes an MCP adapter, or when you want a clean boundary between Navin and external tool logic.

## Add an MCP server in the app

1. Open Navin from [navin.live/download](https://navin.live/download) if you have not already.
2. Confirm a provider under **Settings → Providers** and a model under **Settings → Models**.
3. Open **Settings → MCP** (or the Apps / MCP area in the workbench).
4. Add a server: local process (stdio) or trusted remote HTTP endpoint, depending on what the MCP package documents.
5. Enable only the tools the agent should see.
6. Save, then ask in chat for something that needs that tool.

Install any MCP server runtime the way that package recommends (separate from Navin). Navin only needs the connection details you enter in Settings.

## Everyday tips

- Expose only the tools the agent actually needs.
- Prefer local (stdio) MCP for tools on your machine; use HTTP MCP only for services you trust.
- Keep secrets in Settings fields meant for keys or environment values - not pasted into chat.

## Security notes

- Remote MCP URLs follow the same network safeguards as other web tools.
- Local private endpoints may need an explicit allow entry in Settings before Navin can reach them.
- Stdio MCP starts a local process; review the command and arguments the server docs ask you to use.

## Troubleshooting

- Server does not appear: reopen Settings, confirm it is enabled, and restart Navin once.
- Tools missing in chat: check the enabled-tools list for that server.
- HTTP MCP blocked: review network / allowlist options in Settings and use a narrow host range.

## Related

- [Configure MCP tools](./configure-mcp-tools.md)
- [Desktop app interface](./ai-agent-webui.md)
