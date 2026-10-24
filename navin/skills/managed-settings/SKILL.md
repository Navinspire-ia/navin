---
name: managed-settings
description: Deploys and audits Navin organization policy through a managed-settings.json file that sits above every personal, project and plugin setting (managed hooks, plugin and MCP allowlists, pinned environment variables, tool deny rules, no permission bypass). Use when an administrator asks to "lock down Navin for my company", "deploy managed settings", "only allow approved plugins", "restrict MCP servers", "enforce a hook on every machine", "pin a proxy for all users", or needs to test a policy with NAVIN_MANAGED_SETTINGS.
---

# Managed Settings (organization policy)

Navin reads one optional file written by an administrator, the managed
settings file. It is the organization tier: it is read before personal
settings (`~/.navin`), project settings (`.navin/` in the workspace) and
installed plugins, and none of them can loosen what it says. Without the file
Navin behaves exactly as before: no extra check, no extra line in the system
prompt.

Use this skill to write the file, choose a preset, deploy it per operating
system and verify that it is in force.

## Where the file is read

The first path that exists wins:

1. The path in the environment variable `NAVIN_MANAGED_SETTINGS`. Meant for
   tests, CI images and MDM wrappers that launch Navin.
2. Linux and other POSIX systems: `/etc/navin/managed-settings.json`.
3. macOS: `/Library/Application Support/Navin/managed-settings.json` (then
   `/etc/navin/managed-settings.json`).
4. Windows: `%ProgramData%\Navin\managed-settings.json`.

The system paths must be writable by administrators only (root or the
Administrators group). A file a normal user can edit is not a policy. See
`references/deployment.md` for permissions, macOS configuration profiles,
Windows Group Policy and MDM notes.

The file is cached by modification time: editing it takes effect on the next
tool call or turn, without restarting Navin.

## Keys

All keys are optional. Names are camelCase.

### `hooks`

Managed hooks, in exactly the format of a Navin `hooks.json` (event name, then
a list of `{matcher, hooks: [{type: "command", command, timeout}]}` entries).
They differ from personal and project hooks in four ways:

- They always run, and they run first, before personal, plugin and project
  hooks for the same event.
- They need no trust step: the file is already protected by the system.
- A PreToolUse refusal from a managed hook is final. The remaining hooks for
  that call are not run, so a later hook that answers "allow" changes
  nothing.
- Disabling a plugin or untrusting a project never removes them.

Use absolute paths in `command` and keep the scripts in an
administrator-owned folder next to the managed file.

### `allowManagedHooksOnly`

`true` drops every personal (`~/.navin/hooks.json`), project
(`.navin/hooks.json`) and plugin hook. Only `hooks` from the managed file run.
Built-in features such as hookify rules are not hooks files and are not
affected.

### `allowManagedPluginsOnly` and `allowedPlugins`

With `allowManagedPluginsOnly: true`, an installed plugin whose folder name is
not in `allowedPlugins` is treated as disabled whatever the user toggled: its
hooks, commands, agents, skills and MCP servers do not load. `navin plugins`
lists it with `blocked_by_policy: true`. Names are compared case-insensitively.

### `mcpAllowlist`

A list of MCP server names that may be configured. Plugin servers are named
`<plugin>-<server>` (for example `github-tools-api`), so list them that way.
When the key is absent nothing is filtered; an empty list refuses every
plugin MCP server.

### `env`

Environment variables pinned by the organization, for example a proxy or a
telemetry endpoint. Every hook process receives them last, so personal,
project and plugin configuration cannot override them. Navin also pins the
variables that decide where the policy is read from (`NAVIN_MANAGED_SETTINGS`
is forced to the file in force, `ProgramData` keeps its value), so a hook
cannot point child processes at a weaker policy.

### `permissions.deny` and `permissions.allow`

Tool names, lower case Navin names such as `exec`, `browser`, `web_fetch`,
`write_file`. Rules:

- A tool in `deny` is refused.
- When `allow` is not empty, every tool outside it is refused.
- `deny` wins when both lists name the same tool.

The refusal happens before any hook runs and the agent receives a message
that names the organization policy.

### `disableBypassPermissions`

`true` means no mode or flag may skip permission checks. The value is exposed
as `ManagedPolicy.disable_bypass_permissions` for the permission layer.

## Fail closed

A managed file that exists but cannot be read, is not valid JSON, or has a
wrong type for a key (for example `"allowedPlugins": "all"`) does not leave
the machine open. Navin puts a lockdown policy in force:

- managed hooks only (and the broken file provides none);
- no plugin loads;
- no plugin MCP server is allowed;
- risky tools are refused: shell execution, file writes and edits, browser,
  web access, subagents, git and forge operations;
- permission bypass is disabled.

The system prompt receives one line naming the file and the parse error, and
the error is logged. Fix the file and the next turn picks it up.

Boolean keys lock on anything other than `false` or absent: a mistyped
`"true"` or `1` still locks.

## Presets

`references/presets.md` holds four ready files, each in a fenced json block:

- **strict**: managed hooks only, approved plugins only, no web tools, MCP
  allowlist, no bypass.
- **lax**: only disables the permission bypass.
- **bash-sandbox**: a managed PreToolUse hook that screens every shell
  command, plus a pinned proxy.
- **regulated-data**: for health or financial data; adds deny rules on
  network tools, pins telemetry off and limits MCP to internal servers.

Start from the closest preset, then remove what the organization does not
need.

## Workflow for an administrator

1. Pick a preset and copy it into `managed-settings.json`.
2. Validate the JSON: `python -m json.tool managed-settings.json`.
3. Test it locally without installing it (next section).
4. Deploy it to the system path for each operating system with the tool the
   organization already uses (see `references/deployment.md`).
5. Check one machine: ask Navin a question and look for the
   "Organization policy in force" line, or run
   `python -m navin.agent.user_hooks list <workspace>`.

## Testing with NAVIN_MANAGED_SETTINGS

Point the variable at a draft file to try a policy as a normal user:

```bash
export NAVIN_MANAGED_SETTINGS=$HOME/draft-policy.json
navin
```

Things to check:

- A denied tool returns "blocked by your organization's policy".
- A managed PreToolUse hook that exits 2 refuses the call even when a
  personal hook prints `{"decision": "approve"}`.
- With `allowManagedHooksOnly`, a personal hook that writes a marker file no
  longer writes it.
- A plugin outside `allowedPlugins` shows `blocked_by_policy`.
- Corrupting the draft (delete a brace) shows the lockdown line.

In Python tests, set the variable with `monkeypatch.setenv` and call
`navin.agent.managed_policy.load()`; the mtime cache reloads automatically.

## Writing a managed hook

Managed hooks follow the hook contract described in the hook-development
skill: the event arrives as JSON on stdin, exit 2 with a reason on stderr
refuses, exit 0 allows, JSON output may add context. A good managed hook is
small, fast (it runs on every matching call) and has no network dependency,
because a timeout counts as a failure, not a refusal. The hook-development
skill ships a Python shell-advice example (refuses plain grep and find -name
with ripgrep advice) and a bash validator for destructive commands; both are
good starting points.

A managed hook can do three things:

- **Refuse**: exit 2 and write the reason on stderr. On PreToolUse the call
  is refused and the agent reads the reason, so phrase it as an instruction
  ("use rg instead", "ask the user to run this manually").
- **Advise**: exit 0 and print JSON with `additionalContext`; the agent sees
  it without the call being refused.
- **Notify the user**: print JSON with `systemMessage`; the user sees one
  activity row, the model does not.

Any other exit code is logged as a hook failure and the call goes on. If a
managed hook must be fail closed (for example an audit hook that cannot reach
its log), make it exit 2 itself on internal errors.

Pinned `env` values are present in the hook process, so a hook can rely on
the organization proxy or endpoint without reading any user file.

## Choosing between keys

Several keys can express a similar intent. Prefer the narrowest one:

| Intent | Key to use |
| --- | --- |
| Never use a tool at all | `permissions.deny` |
| Use a tool, but only for some inputs | a managed PreToolUse hook |
| Only reviewed automation runs | `allowManagedHooksOnly` |
| Only reviewed extensions load | `allowManagedPluginsOnly` + `allowedPlugins` |
| Only reviewed external services | `mcpAllowlist` |
| Same network and telemetry setup everywhere | `env` |

A deny rule is cheaper than a hook (no process starts) and cannot time out.
A hook can inspect arguments, such as the command line or the target path,
and can explain itself. An allowlist of tools (`permissions.allow`) is the
strictest choice and also the easiest to get wrong: every tool missing from it
is refused, including read-only ones such as `read_file` and `grep`, so list
those explicitly.

## How the tiers combine

From highest to lowest: organization (this file), personal (`~/.navin`),
plugins, project (`.navin/` in the workspace, after the user trusted its
hooks file). A lower tier can add restrictions of its own, for example a
project hook that refuses writes to `migrations/`, but it cannot remove one
set above it. In practice:

- A personal hook can block a call the organization allows; it cannot allow a
  call a managed hook refused.
- A user can disable a plugin the organization allows; they cannot enable one
  outside `allowedPlugins`.
- A project can configure fewer MCP servers; it cannot add one outside
  `mcpAllowlist`.

This matches the expectation of most security teams: the organization sets a
floor, teams and individuals build on top of it.

## Troubleshooting

- **Policy not applied**: check which file wins. A stale
  `NAVIN_MANAGED_SETTINGS` in a shell profile takes precedence over the system
  path; remove it in production.
- **Everything refused**: the file is corrupt and lockdown is in force; the
  prompt line quotes the parse error.
- **Plugin MCP server missing**: list it as `<plugin>-<server>` in
  `mcpAllowlist`.
- **Managed hook never runs**: its `matcher` must match the Navin tool name
  (`exec`, `write_file`) or a supported alias.

## References

- `references/presets.md`: strict, lax, bash-sandbox and regulated-data files.
- `references/deployment.md`: per-OS paths, permissions, macOS profiles,
  Windows policy and MDM notes.
