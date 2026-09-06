# How to Secure a Local AI Agent with Navin

This guide covers the practical controls to review before letting a Navin agent access files, shell commands, web fetch, chat apps, or remote users - all from the desktop app.

## What you will tighten

- a project-scoped agent setup
- narrow channel access
- safer secrets handling
- optional shell sandboxing on Linux

## When to use this

Use this before exposing Navin to teammates, chat apps, public networks, broad web access, or unattended automations.

## Start from Settings

1. Open the Navin desktop app.
2. Confirm **Settings → Providers** and **Settings → Models** work with a short chat test.
3. Review tool and security options under Settings (workspace restriction, shell, web, channels).

## Minimal safe posture

Prefer these habits in the app:

- Keep **restrict to workspace / project** enabled so file tools stay inside the selected folder.
- On Linux, enable the native shell sandbox when Settings offer it (bubblewrap-based). On macOS or Windows, keep project restriction on and review shell access carefully.
- Leave destructive shell/file ops on approval when the assisted security profile is active.
- Store API keys and bot tokens in Settings fields (or OS environment variables), not in shared chat logs.

A fresh desktop setup often applies an **assisted** security profile when posture knobs are still at factory defaults: approvals for destructive ops, builtin deny rules, workspace restriction, and the native sandbox when the OS supports it. Ordinary reads, edits, and search stay autonomous; you can allow once for the session.

To work more openly for trusted solo use, switch the security profile toward **autonomous** in Settings, then turn off only the knobs you understand.

### Advanced JSON shape (optional)

```json
{
  "tools": {
    "restrictToWorkspace": true,
    "exec": {
      "enable": true,
      "sandbox": "bwrap"
    }
  }
}
```

`bwrap` is Linux-only and requires bubblewrap. Prefer the Settings toggles when available.

## Production notes

- Use one project per trust boundary.
- Prefer pairing for DM-capable chat apps under **Settings → Channels**.
- Use narrow allowlists only when static allowlists are intentional.
- Keep group policy mention-only at first.
- Keep Navin local unless remote access is intentional.

## Security notes

- Project restriction is an application-level guard, not a full OS sandbox.
- Disabling shell execution removes command tools entirely.
- HTTP web fetch and HTTP MCP use SSRF protections by default.
- Broad SSRF whitelist ranges increase exposure.
- Allowing everyone on a channel (`*`) bypasses pairing - anyone who can reach that bot can talk to the agent.

## Troubleshooting

- Needed file cannot be read: confirm the active project path in the workbench.
- Shell command fails under sandbox: the command may need files outside the sandbox; review approvals or scope.
- Local HTTP tools blocked: review the SSRF whitelist and use a narrow CIDR.

## Related docs

- [`chat-app-ai-agent.md`](./chat-app-ai-agent.md)
- [`../automations.md`](../automations.md)
- [`../configuration.md`](../configuration.md)
