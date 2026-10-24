# plugin.json reference

The manifest is a JSON object at the plugin root. Unknown fields are kept and
ignored, so a manifest can carry extra metadata for other tooling.

## Identity

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | string | recommended | `^[a-z0-9][a-z0-9._-]{0,63}$`. Defaults to the folder name. Used for the install folder, command namespace and MCP prefix. |
| `displayName` | string | no | Human label in the Plugins panel. |
| `version` | string | recommended | Semantic version `MAJOR.MINOR.PATCH`. Bump MAJOR on breaking changes (renamed command, removed agent). |
| `description` | string | recommended | One sentence: what the plugin is for. Shown in `/pack list`. |

## Authorship and distribution

| Field | Type | Notes |
|---|---|---|
| `author` | object or string | `{"name": "...", "email": "...", "url": "..."}` or `"Jane Doe"`. |
| `homepage` | string | Documentation URL. |
| `repository` | string or object | Source URL, or `{"type": "git", "url": "..."}`. |
| `license` | string | SPDX identifier (`MIT`, `Apache-2.0`, `AGPL-3.0-only`). |
| `keywords` | array of strings | Search terms. |

## Component paths

Defaults are always scanned: `skills/`, `agents/`, `commands/`,
`hooks/hooks.json`, `mcp.json`. These fields add locations on top of them.

| Field | Type | Example |
|---|---|---|
| `commands` | string or array | `"./extra-commands"` or `["./cmds", "./admin/cmds"]` |
| `agents` | string or array | `"./team"` |
| `hooks` | string or object | `"./config/hooks.json"`, or an inline `{"PreToolUse": [...]}` |

Paths are relative to the plugin root, use `/`, and must stay inside the
plugin (no `..`, no absolute paths). Navin skips a path that escapes the
plugin folder.

## Examples

Minimal:

```json
{"name": "hello"}
```

Recommended:

```json
{
  "name": "db-tools",
  "displayName": "Database Tools",
  "version": "0.3.1",
  "description": "Inspect schemas, write migrations and review SQL",
  "author": {"name": "Data Team", "email": "data@example.com"},
  "repository": "https://github.com/example/db-tools",
  "license": "MIT",
  "keywords": ["sql", "migrations", "postgres"]
}
```

Custom locations:

```json
{
  "name": "ops-suite",
  "version": "2.0.0",
  "commands": ["./commands-admin"],
  "agents": "./specialists",
  "hooks": "./config/hooks.json"
}
```

## Validation rules applied by `scripts/validate_plugin.py`

- The file parses as a JSON object (critical).
- `name` matches the pattern above (critical when present).
- `version` looks like semantic versioning (warning).
- `description` is present (warning).
- Each custom path exists and stays inside the plugin (critical).
- A bare name in `skills`, `agents` or `commands` (no slash, no extension,
  for example `"agents": ["code-explorer"]`) is a component Navin ships: it
  resolves to the plugin's own `skills/<name>/SKILL.md`, `agents/<name>.md`
  or `commands/<name>.md` first, then to the one bundled with Navin. A name
  that neither provides is critical.
- Each `steps` entry has a `step` label and a `use` of `/command`,
  `agent:name` or `skill:name` that the manifest lists (critical).

## Versioning advice

- PATCH: wording fixes, bug fixes in scripts.
- MINOR: new command, agent, skill or hook; new optional setting.
- MAJOR: removed or renamed component, changed settings format, hook that now
  blocks something it used to allow.

Keep a `CHANGELOG.md` next to the README so users know what an update does.
