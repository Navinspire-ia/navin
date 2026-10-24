# MCP server types

## stdio

Navin starts the process and speaks JSON-RPC over its stdin/stdout.

```json
{"type": "stdio", "command": "uvx", "args": ["mcp-server-git", "--repository", "."], "cwd": "${NAVIN_PLUGIN_ROOT}"}
```

- Lifecycle: started when MCP connects, stopped when Navin stops or the
  configuration changes.
- Logs: the server must write logs to stderr only; anything else on stdout
  breaks the protocol.
- Runtimes: `npx -y <package>`, `uvx <package>`, `python3 path/server.py`,
  `node path/index.js`, or a binary. Pin versions (`pkg@1.2.3`) in plugins.
- Environment: `env` adds variables; the rest of Navin's environment is
  inherited.
- Best for: local files, CLIs, databases on the machine, anything shipped in
  the plugin.

## streamableHttp

One HTTP endpoint that accepts JSON-RPC POSTs and may stream responses.

```json
{"type": "streamableHttp", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer ${EXAMPLE_TOKEN}"}}
```

- Best for: hosted services and shared team servers.
- No local process; availability depends on the network.

## sse

Older HTTP transport with a Server-Sent Events stream for responses.

```json
{"type": "sse", "url": "https://legacy.example.com/sse", "headers": {"X-API-Key": "${LEGACY_KEY}"}}
```

- Use only when the server does not offer streamableHttp.

## Choosing

| Need | Pick |
|---|---|
| Ship the server code in the plugin | stdio |
| Access local files or local network | stdio |
| Hosted SaaS endpoint | streamableHttp |
| Server documentation shows a `/sse` URL only | sse |
| Server only supports a browser OAuth login | stdio bridge (see authentication.md) |

## Performance notes

- Each stdio server is a process; avoid shipping many small servers.
- Remote calls add network latency to every tool call; set `toolTimeout`.
- Use `enabledTools` to keep the tool list short.
