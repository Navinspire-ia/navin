# MCP controls

An MCP server is code the agent can call with the user's rights. Vet it
like a dependency, bound it like a service account, and watch its calls like
any outbound channel.

## Before enabling a server

| Check | How | Reject when |
|---|---|---|
| Origin | the repository, publisher and release the package comes from | unknown publisher, no source, name close to a known server (typosquat) |
| Transport | `stdio` runs a local process; `streamableHttp` and `sse` talk to a remote endpoint | a remote endpoint without TLS or without an identity (token, OAuth) |
| Tool list | start the server once and list its tools, resources and prompts | tools that execute shell, raw SQL, file deletes or arbitrary HTTP when the task does not need them |
| Tool descriptions | read every description: the model reads them as instructions | text that tells the model to call other tools, read files, or hide its actions (tool poisoning) |
| Secrets | `env` and `headers` in the declaration use `${VAR}` references | a literal token in `mcp.json` or `config.json` |
| Permissions | what the token behind the server can do on the target system | an admin token where a read scope would do |
| Network | which hosts the server itself contacts | a server that phones home or fetches remote instructions |

## Declaration limits (Navin)

User servers live under `tools.mcpServers` in `~/.navin/config.json`;
plugin servers in `<plugin>/mcp.json`. Use these fields to bound a server:

```json
{
  "mcpServers": {
    "crm": {
      "type": "streamableHttp",
      "url": "https://mcp.crm.example/v1",
      "headers": {"Authorization": "Bearer ${CRM_MCP_TOKEN}"},
      "enabledTools": ["search_contacts", "get_contact"],
      "toolTimeout": 20
    }
  }
}
```

- `enabledTools`: expose only the tools the task needs; the default `["*"]`
  exposes every tool, resource and prompt.
- `toolTimeout`: seconds before a call is cancelled (default 30); a server
  that hangs cannot stall the turn.
- The exposed name is `mcp_<server>_<tool>`; policy rules in
  `guard_pretooluse.py` match on that name (`mcp.allow_servers`,
  `mcp.deny_tools`).
- A plugin can restrict which tools a command or subagent may call with
  `allowed-tools` in its frontmatter; this is a static restriction, the
  PreToolUse hook is the dynamic one.

The `mcp-integration` skill documents the full declaration format.

## Runtime controls

| Risk | Control |
|---|---|
| Tool poisoning (instructions hidden in a tool description or result) | `prompt-injection-defender` rules; the hook denies `mcp_*` calls whose arguments were not requested by the user's task (allow_servers, deny_tools) |
| Exfiltration through a tool argument | `mcp.max_arg_chars`; `network.allow_hosts` on URLs inside arguments; `secrets.deny_in_arguments` |
| Destructive tool | `mcp.deny_tools` globs `mcp_*_delete_*`, `mcp_*_drop_*`, `mcp_*_execute_sql`, `mcp_*_run_shell*`; or `enabledTools` so the tool does not exist at all |
| Confused deputy (the server acts with the user's identity on a third system) | per-server token with the minimum scope; a distinct token per project |
| Rug pull (the server changes its tools after approval) | pin the server version; compare the tool list at each start with the one approved; a new tool is denied until reviewed |
| Runaway loop | `audit_tool_log.py --loop-threshold`; a Stop hook that surfaces loops |
| Silent failures | `toolTimeout`; PostToolUse hook that flags empty or error results from `mcp_*` |

## Reviewing a session

1. `audit_tool_log.py tool-calls.jsonl`: which servers were called, how
   often, what was denied.
2. For each MCP server in the report, check that every call maps to a step
   the user asked for. A call the task did not need is the finding, even
   when it was allowed.
3. Hosts and paths tables: anything outside the project or the approved
   APIs is a finding.
4. Record the approved server list and tool list in the policy so the next
   session is denied by default on anything new.
