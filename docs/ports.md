# Ports

Navin binds a small fixed set of local ports. Keep them free of other services
on your machine so the WebUI, gateway health endpoint, and optional API server
do not collide.

## Registry

| Role | Default | Owner | Config |
| --- | --- | --- | --- |
| WebUI + WebSocket | `8765` | Navin | `channels.websocket.port` / `channels.websocket.host` |
| Gateway health | `18790` | Navin | `gateway.port` / `gateway.host` |
| OpenAI-compatible API (`navin serve`) | `8900` | Navin | `api.port` / `api.host` |
| DebugMCP | `3001` | External (VS Code / Cursor extension) | `tools.mcp_servers.debugmcp.url` |
| Figma Dev Mode MCP | `3845` | External (Figma desktop) | `tools.mcp_servers.figma.url` |

Source of truth in code: [`navin/ports.py`](../navin/ports.py).

## Rules

1. Do **not** reuse `8765`, `18790`, or `8900` for unrelated local services while Navin is running.
2. Navin does **not** auto-remap those ports (changing them on the fly breaks bookmarks, the mobile PWA, and front/back wiring).
3. External MCP ports (`3001`, `3845`) are **client-only** for Navin. Another app may listen there; Navin probes and degrades cleanly when they are down.
4. For a second Navin instance, use a separate config and explicit ports (`--port`, `--gateway-port`). See [Multiple Instances](./multiple-instances.md).

## CLI

```bash
navin ports          # table: role, owner, host:port, status, detail
navin ports check    # exit 1 if a Navin-owned port is busy with a non-Navin process
```

Statuses:

| Status | Meaning |
| --- | --- |
| `free` | Nothing accepts TCP on that host:port |
| `navin` | A Navin gateway/serve process already holds it |
| `busy` | Another process holds it (PID/cmdline when available) |

## Startup preflight

`navin webui` / `navin gateway` refuse to start when a Navin-owned port is taken by a non-Navin process. The error lists the registry table and suggests:

```bash
navin gateway status
navin gateway stop
navin ports
```

External MCP ports are logged at info level only; they never block gateway start.

## Related docs

- [Architecture](./architecture.md) - WebUI vs health endpoint
- [Deploy the gateway](./guides/deploy-navin-gateway.md)
- [Multiple Instances](./multiple-instances.md)
- [Configure MCP Tools](./guides/configure-mcp-tools.md) - DebugMCP preset
