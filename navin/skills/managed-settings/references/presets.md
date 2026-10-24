# Managed settings presets

Copy one block into `managed-settings.json`, then adapt names, domains and
paths. Every key is optional; see the SKILL.md for the meaning of each.

## Strict

For teams that want a closed, reviewed setup: only organization hooks, only
approved plugins, no web tools, a short MCP list and no bypass.

```json
{
  "allowManagedHooksOnly": true,
  "allowManagedPluginsOnly": true,
  "allowedPlugins": ["code-review", "security-guidance"],
  "mcpAllowlist": ["code-review-github"],
  "permissions": {
    "deny": ["web_search", "web_fetch", "browser"]
  },
  "disableBypassPermissions": true
}
```

## Lax

The smallest useful policy: users keep their hooks and plugins, but no mode
can skip permission checks.

```json
{
  "disableBypassPermissions": true
}
```

## Bash sandbox

Every shell command passes through an organization hook first, and all
traffic goes through the company proxy. Put the script in an
administrator-owned folder.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "exec",
        "hooks": [
          {"type": "command", "command": "python3 /etc/navin/hooks/shell-guard.py", "timeout": 10}
        ]
      }
    ]
  },
  "env": {
    "HTTPS_PROXY": "http://proxy.example.internal:8080",
    "HTTP_PROXY": "http://proxy.example.internal:8080",
    "NO_PROXY": "localhost,127.0.0.1"
  },
  "disableBypassPermissions": true
}
```

## Regulated data (health, finance)

For machines that handle protected records: no browsing or web access, no
subagents, internal MCP servers only, telemetry pinned off, every write
audited by a managed PostToolUse hook, and organization hooks only.

```json
{
  "allowManagedHooksOnly": true,
  "allowManagedPluginsOnly": true,
  "allowedPlugins": [],
  "mcpAllowlist": [],
  "permissions": {
    "deny": ["web_search", "web_fetch", "browser", "spawn", "forge", "pr_comments"]
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "write_file|edit_file|apply_patch",
        "hooks": [
          {"type": "command", "command": "python3 /etc/navin/hooks/audit-write.py", "timeout": 10}
        ]
      }
    ]
  },
  "env": {
    "NAVIN_TELEMETRY": "0",
    "HTTPS_PROXY": "http://proxy.example.internal:8080"
  },
  "disableBypassPermissions": true
}
```

This preset is a starting point, not a compliance certification: pair it with
disk encryption, access control and the organization's own review.
