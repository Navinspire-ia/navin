# Using MCP tools

## Names

`mcp_<server>_<tool>`, where `<server>` is the configuration key. For plugin
servers the key is `<plugin>-<server>`, so a `query` tool of server `docs` in
plugin `kb` becomes `mcp_kb-docs_query` (characters a provider rejects are
normalized). Resources and prompts get `mcp_<server>_resource_<name>` and
`mcp_<server>_prompt_<name>`.

## Referencing tools in skills and commands

- Name the exact tool and its purpose: "Use `mcp_kb-docs_query` with a short
  keyword query; read at most the three best hits".
- Restricted commands: list the tools in `allowed-tools`, e.g.
  `allowed-tools: mcp_kb-docs_query, read_file`.
- Prefer one clear tool per step; the model picks better from a short list.

## Arguments

The server publishes a JSON schema per tool; Navin passes it to the model.
When a skill gives an example call, copy the argument names from the schema,
not from memory.

## Errors and timeouts

- A server error comes back as the tool result; the instructions should say
  what to do then (retry with a narrower query, fall back to web_fetch, report).
- `toolTimeout` bounds each call. Long jobs should return an id the next call
  polls.
- A disconnected server removes its tools until Navin reconnects.

## Limiting exposure

`enabledTools` keeps only the listed server tools:

```json
{"enabledTools": ["query", "get_page"]}
```

Use it to hide write or admin tools a plugin does not need.

## Testing

1. Enable the plugin and check the tool names appear.
2. Call each tool once with a minimal valid argument set.
3. Trigger one error on purpose (bad id) and confirm the instructions handle it.
