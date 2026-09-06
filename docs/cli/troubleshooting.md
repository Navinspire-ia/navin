# Troubleshooting

## `navin` not found

```bash
navin install-cli
```

Open a new terminal after a Windows PATH change. On macOS, `~/.local/bin` must be on PATH.

WSL: use the Linux one-liner (`curl … | bash`). The Windows installer does not put `navin` on the WSL PATH.

## `/install` 404

Use [navin.live/download](https://navin.live/download) until the site route is live.

## Checksum errors

The installer checks `SHA256SUMS.txt`. Retry a truncated download. Proxies that rewrite HTTPS fail verification.

## Doctor

```bash
navin doctor
```

Required misses exit 1. Optional tools only disable those features.

## No model

```bash
navin status
```

You need a provider key (Settings or env), a local endpoint, OAuth, or a paid managed key. Then an **Active** model in Settings. [License](./license.md).

## `device_limit_reached`

Revoke a device on navin.live. WSL and Windows count as two devices.

## Port busy

```bash
navin ports
navin gateway status
navin gateway stop
```

## Slash command does nothing

`/help` and `/status` answer immediately. Workflow slashes (`/forge`, …) need a configured model.

## MCP tools missing

Restart after editing MCP config. Names look like `mcp_<server>_<tool>`.

## Logs

```bash
navin gateway logs --tail 200
navin cache
```
