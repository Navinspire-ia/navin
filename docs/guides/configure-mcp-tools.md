# How to Configure MCP Tools in navin

This guide adds an MCP server to navin so the agent can use external tools
through the Model Context Protocol.

## What you will build

- a working navin agent
- one MCP integration configured through Apps or `~/.navin/config.json`
- a restricted set of MCP tools exposed to the model

## When to use this

Use MCP when the capability you need already exists as an MCP server, or when
you want external tools to be managed outside navin core.

## Install

```bash
python -m pip install navin-ai
navin webui   # configure provider & model in the platform (Settings → Providers)
navin agent -m "Hello!"
```

Install the MCP server runtime separately. Many examples use `npx`, `uvx`, or a
remote HTTP endpoint.

## Minimal working example

For local interactive setup:

1. Run `navin webui` and open **Apps**.
2. Choose a known integration preset, or add a custom stdio, HTTP, or SSE server.
3. Limit the enabled tools when the server exposes more than the task needs.
4. Save and restart when prompted.
5. Mention the integration with `@` in the next message and ask for a small test action.

For manual or deployment-managed config, add this to `~/.navin/config.json`:

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

Restart navin and ask a question that requires the MCP tool.

## Production notes

- Prefer `enabledTools` over exposing every tool by default.
- Use `toolTimeout` for slow MCP operations.
- Use HTTP MCP only for endpoints you trust.
- Keep MCP server commands stable and versioned in deployment docs or scripts.

## Security notes

- Stdio MCP starts a local process; review the command before enabling it.
- HTTP/SSE MCP uses navin's SSRF guard.
- Allow private HTTP MCP hosts only with narrow `tools.ssrfWhitelist` CIDRs.
- Do not place secrets in command arguments when environment variables or
  headers can be used.

## Troubleshooting

- Run the MCP command outside navin first.
- Start `navin gateway --verbose` and inspect tool registration logs.
- If an HTTP MCP URL is blocked, check whether it points to loopback or a
  private address that needs explicit allowlisting.

## Related navin docs

- [MCP tools for AI agents](./mcp-tools-for-ai-agents.md)
- [Configuration: MCP](../configuration.md#mcp-model-context-protocol)
- [Security](../configuration.md#security)
