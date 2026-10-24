---
name: mcp-integration
description: Connect MCP servers to Navin and ship them in plugins (mcp.json, stdio, sse, streamableHttp, env and headers, ${NAVIN_PLUGIN_ROOT}, enabledTools, toolTimeout, mcp_<server>_<tool> names). Use when the user asks to "add an MCP server", "integrate an MCP server in my plugin", "connect a remote MCP endpoint", "pass an API token to an MCP server", "limit the tools of a server", "why are the MCP tools missing", or needs to choose between stdio and HTTP transports.
---

# MCP Integration

The Model Context Protocol lets Navin call tools, read resources and use
prompts exposed by an external server. A server can be a local process (a CLI
speaking JSON-RPC on stdin/stdout) or a remote HTTP endpoint. Once connected,
each server tool becomes a Navin tool named `mcp_<server>_<tool>`.

Shipping a server inside a plugin gives users a ready integration: the plugin
carries the configuration (and often the server code), the user exports the
secrets, enables the plugin and the tools appear.

## Where servers are declared

| Scope | Location | Server key |
|---|---|---|
| User | `tools.mcpServers` in `~/.navin/config.json` (or the MCP settings panel) | as written |
| Plugin | `<plugin>/mcp.json` | `<plugin>-<server>` |

A plugin `mcp.json` has the same shape as the config block:

```json
{
  "mcpServers": {
    "search": {
      "type": "stdio",
      "command": "node",
      "args": ["${NAVIN_PLUGIN_ROOT}/servers/search/index.js"],
      "env": {"SEARCH_API_KEY": "${SEARCH_API_KEY}"}
    }
  }
}
```

The `mcpServers` wrapper is optional: a bare object of servers is accepted too.
Navin reads only `mcp.json` at the plugin root; there is no `mcpServers` key in
`plugin.json` and no `.mcp.json` file name.

```
my-plugin/
  plugin.json
  mcp.json
  servers/
    search/
      index.js
```

The same server in the user configuration (`~/.navin/config.json`):

```json
{
  "tools": {
    "mcpServers": {
      "search": {
        "command": "node",
        "args": ["/opt/search-mcp/index.js"],
        "env": {"SEARCH_API_KEY": "sk-..."}
      }
    }
  }
}
```

Variables are not expanded in the user configuration; write real values there
or use the settings panel.

## Choosing a transport

| Type | Use for | Required fields |
|---|---|---|
| `stdio` | local tools, CLIs, anything shipped inside the plugin | `command`, optional `args`, `env`, `cwd` |
| `streamableHttp` | current remote servers (single HTTP endpoint) | `url`, optional `headers` |
| `sse` | older remote servers using Server-Sent Events | `url`, optional `headers` |

`type` may be omitted: Navin infers `stdio` from `command`, `sse` from a `url`
ending in `/sse`, and `streamableHttp` from any other `url`. Set it explicitly
in plugins so the behaviour does not depend on detection. Navin has no
WebSocket (`ws`) transport. Details, lifecycle and trade-offs:
`references/server-types.md`.

### stdio

```json
{
  "mcpServers": {
    "files": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "${NAVIN_PLUGIN_ROOT}/data"],
      "env": {"LOG_LEVEL": "warn"}
    },
    "local-db": {
      "type": "stdio",
      "command": "python3",
      "args": ["${NAVIN_PLUGIN_ROOT}/servers/db_server.py"],
      "cwd": "${NAVIN_PLUGIN_ROOT}",
      "env": {"DATABASE_URL": "${DATABASE_URL}"}
    }
  }
}
```

Navin starts the process, speaks JSON-RPC over its stdin/stdout and keeps it
running for the session. The server must never write logs to stdout (use
stderr) and must not wait for interactive input.

### streamableHttp

```json
{
  "mcpServers": {
    "docs": {
      "type": "streamableHttp",
      "url": "https://mcp.example.com/mcp",
      "headers": {"Authorization": "Bearer ${EXAMPLE_DOCS_TOKEN}"},
      "toolTimeout": 45
    }
  }
}
```

### sse

```json
{
  "mcpServers": {
    "tracker": {
      "type": "sse",
      "url": "https://mcp.example.com/sse",
      "headers": {"X-API-Key": "${TRACKER_API_KEY}"}
    }
  }
}
```

## Fields

Keys are camelCase in JSON (snake_case is accepted as well).

| Field | Meaning |
|---|---|
| `type` | `stdio`, `sse` or `streamableHttp` |
| `command`, `args` | process to start (stdio) |
| `env` | extra environment for the process (stdio) |
| `cwd` | working directory of the process (stdio) |
| `url` | endpoint (sse, streamableHttp); `https://` except for localhost |
| `headers` | HTTP headers, typically `Authorization` |
| `toolTimeout` | seconds before a tool call is cancelled (default 30) |
| `enabledTools` | list of tools to expose; default `["*"]` (all tools, resources and prompts) |
| `maxTools` | cap on registered tools; `0` built-in default, `-1` no limit |
| `maxToolTokens` | cap on tokens taken by the tool definitions; `0` default, `-1` no limit |

`enabledTools` accepts raw server names (`"query"`) or wrapped Navin names
(`"mcp_docs_query"`). Any restriction exposes only the listed tools: resources
and prompts of that server are then not registered.

## Variables

In plugin `mcp.json` files, Navin replaces when the plugin is synced:

- `${NAVIN_PLUGIN_ROOT}` with the installed plugin folder
- `${VAR}` with the environment variable `VAR` of the Navin process; unknown
  variables are left as written so the problem is visible

Expansion applies to every string value (command, args, env, cwd, url,
headers). There is no default-value syntax such as `${VAR:-x}`.

```json
{
  "mcpServers": {
    "api": {
      "type": "streamableHttp",
      "url": "${API_BASE_URL}/mcp",
      "headers": {
        "Authorization": "Bearer ${API_TOKEN}",
        "X-Workspace-Id": "${API_WORKSPACE_ID}"
      }
    }
  }
}
```

Secrets therefore stay out of the plugin files: document the variables the
user must export, then re-enable the plugin after setting them (values are
captured at sync time, not on each connection). More on tokens, OAuth-only
servers and header patterns: `references/authentication.md`.

## Authentication

| Method | How in Navin |
|---|---|
| Bearer token | `"headers": {"Authorization": "Bearer ${TOKEN}"}` |
| API key header | `"headers": {"X-API-Key": "${KEY}"}` |
| Token for a stdio server | `"env": {"SERVICE_TOKEN": "${SERVICE_TOKEN}"}` |
| OAuth browser flow | not handled by Navin's MCP client; use a token the provider issues, or a local stdio bridge that performs the login |

Navin has no `headersHelper` (a script that generates headers at connect
time). For short-lived tokens, wrap the server in a stdio launcher that fetches
the token and passes it on, or ask the user to refresh the variable and
re-enable the plugin.

Document required variables in the plugin README:

```markdown
## Setup
export EXAMPLE_DOCS_TOKEN=...   # from https://example.com/settings/tokens
Then run `/pack enable docs-tools`.
```

## Tool naming

Wrapped name = `mcp_<server key>_<tool>`, with characters outside
`[A-Za-z0-9_-]` replaced by `_` and names longer than 64 characters shortened
with a hash suffix. For plugins the server key is `<plugin>-<server>`:

| Plugin | Server | Server tool | Navin tool |
|---|---|---|---|
| `docs-tools` | `docs` | `query` | `mcp_docs-tools-docs_query` |
| `asana` | `api` | `create_task` | `mcp_asana-api_create_task` |
| (user config) | `github` | `search.repos` | `mcp_github_search_repos` |

Resources and prompts get `mcp_<server>_resource_<name>` and
`mcp_<server>_prompt_<name>`.

## Lifecycle

1. The plugin is enabled (`/pack enable <name>`).
2. `sync_mcp_servers` validates each server, expands variables and writes it
   to `tools.mcpServers` under `<plugin>-<server>`. An invalid server is
   skipped with a warning; the others still load.
3. Navin reloads MCP connections: stdio processes start, HTTP sessions open,
   tools are listed and registered within the tool and token budgets.
4. Calls go through the live connection; transient connection errors and
   terminated HTTP sessions trigger a reconnect.
5. Disabling or removing the plugin removes its servers from the config and
   closes the connections. User-declared servers are never touched.

## Workflow

1. Pick the transport (table above).
2. Start from `examples/stdio-server.json`, `examples/http-server.json` or
   `examples/sse-server.json`.
3. Put every plugin path behind `${NAVIN_PLUGIN_ROOT}` and every secret behind
   `${VAR}`.
4. Restrict with `enabledTools` when the server exposes more than the plugin
   needs; fewer tools means better tool choice by the model.
5. Validate the plugin (`plugin-validator` agent or the `plugin-structure`
   validator): it checks types, required fields, https and hard-coded secrets.
6. Enable the plugin, then confirm the `mcp_` tools are listed.

## Using the tools from other components

- Skills and commands name the tools explicitly: "Search with
  `mcp_docs-search_query`, then read the top results".
- Commands that restrict tools must list the MCP tool names in
  `allowed-tools`.
- Agents get MCP tools like any other tool, or a subset through `tools`.

A command that may only use two MCP tools and reading:

```markdown
---
description: Create an Asana task from the current discussion
allowed-tools: mcp_asana-api_create_task, mcp_asana-api_search_projects, read_file
---

Find the project with `mcp_asana-api_search_projects`, then create the task
with `mcp_asana-api_create_task` using: $ARGUMENTS
```

`allowed-tools` takes exact tool names separated by commas or a YAML list; it
has no wildcard, so list each MCP tool. An agent restricted the same way:

```markdown
---
name: docs-researcher
description: Answers questions from the product docs. ...
tools: [mcp_docs-tools-docs_query, mcp_docs-tools-docs_get_page]
---
```

Good practice when calling MCP tools: check the arguments against the tool
schema, handle server errors (Navin returns the server message so the model can
correct the call), and batch work instead of calling a tool in a tight loop.
Naming, argument shapes, errors and timeouts: `references/tool-usage.md`.

## Security

- Use `https://` for remote servers; plain `http://` only for localhost.
- Never hard-code tokens in `mcp.json`; always `${VAR}`.
- Pin package versions in `args` (`"@scope/server@1.4.2"`) for stdio servers
  fetched with `npx` or `uvx`.
- Expose the minimum with `enabledTools`.

## Testing

```bash
# stdio: the server must start and wait silently on stdin
node servers/search/index.js
# HTTP: the endpoint must answer with the auth header
curl -sS -H "Authorization: Bearer $EXAMPLE_DOCS_TOKEN" https://mcp.example.com/mcp -o /dev/null -w '%{http_code}\n'
# config after enabling: the server key must be present
python3 -c 'import json,os; print(list(json.load(open(os.path.expanduser("~/.navin/config.json")))["tools"]["mcpServers"]))'
```

Then ask Navin to call one tool with known arguments and check the result.

## Troubleshooting

| Symptom | Check |
|---|---|
| No `mcp_` tools | plugin disabled, server failed to start (logs), `enabledTools` typo |
| Some tools missing | `maxTools` / `maxToolTokens` budget reached, or `enabledTools` restriction |
| stdio server exits | run `command args...` in a terminal; missing runtime (`node`, `uvx`) |
| Literal `${VAR}` in the config | variable not exported before enabling; export it and re-enable |
| 401 / 403 | header value empty or stale token; re-export and re-enable |
| Calls time out | raise `toolTimeout`, or the server waits on stdin prompts |
| Server key missing after enabling | invalid server entry (warning in the log) or malformed `mcp.json` |
| Plain `http://` remote rejected by the validator | use https; plain http is only fine for localhost |

## Additional resources

- `references/server-types.md` - stdio, sse, streamableHttp in depth
- `references/authentication.md` - tokens, headers, OAuth-only servers
- `references/tool-usage.md` - naming, calling patterns, errors
- `examples/stdio-server.json`, `examples/sse-server.json`, `examples/http-server.json`
