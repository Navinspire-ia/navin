# MCP authentication

## Rule: no secret in plugin files

Plugin files are shared and often committed. Reference secrets as `${VAR}`;
Navin resolves them from its environment when the plugin is synced.

## API keys for stdio servers

```json
{"command": "npx", "args": ["-y", "@acme/mcp@2.1.0"], "env": {"ACME_API_KEY": "${ACME_API_KEY}"}}
```

Document in the plugin README: `export ACME_API_KEY=...` before starting
Navin, then `/pack enable <plugin>` (or disable and enable again after a
change).

## Bearer tokens and custom headers for HTTP servers

```json
{"type": "streamableHttp", "url": "https://api.acme.dev/mcp", "headers": {"Authorization": "Bearer ${ACME_TOKEN}"}}
```

Other common headers: `X-API-Key`, `X-Workspace-Id`. Keep the variable name
explicit and prefixed by the vendor.

## Servers that only offer a browser OAuth login

Navin connects with static headers. For a server that requires an interactive
OAuth flow, run a local bridge that handles the login and exposes stdio:

```json
{"type": "stdio", "command": "npx", "args": ["-y", "mcp-remote", "https://mcp.vendor.com/sse"]}
```

The bridge opens the browser on first use and stores the token on the
machine, outside the plugin.

## Checklist

- Every secret is a `${VAR}` reference; the validator flags literal tokens.
- The README lists each variable, where to get it and the minimal scope.
- Use https for remote servers.
- Tokens with write rights are scoped to what the tools need.
- Rotate: changing the variable and re-enabling the plugin is enough.
