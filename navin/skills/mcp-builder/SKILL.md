---
name: mcp-builder
description: Build an MCP (Model Context Protocol) server end to end - scope the upstream API, choose Python FastMCP or the TypeScript SDK, design tools an LLM picks correctly (names, schemas, pagination, errors), implement, run over stdio or HTTP, test with a client, write an evaluation set, wire it into Navin. Use when the user asks to "build an MCP server", "expose this API to the agent as tools" or "evaluate our MCP server". In French "construis un serveur MCP", "expose cette API à l'agent comme outils" or "évalue notre serveur MCP".
metadata: {"navin":{"emoji":"🔌","category":"development"}}
---

# MCP Builder

An MCP server turns an API or a local capability into tools, resources and
prompts that any LLM client can call. The server is good when a model with no
other context picks the right tool, fills its arguments correctly and gets back
a result it can use without drowning. Code quality matters; tool design
matters more.

This skill builds the server. Connecting a finished server to Navin (plugin
`mcp.json`, transports, `${VAR}` secrets, `enabledTools`) is the job of the
`mcp-integration` skill; hand over to it at the end.

## Workflow

### 1. Scope

1. Ask what the server must let the agent do, in task terms ("find overdue
   invoices and send reminders"), not endpoint terms. Record the three to five
   tasks that matter.
2. Read the upstream API: authentication, rate limits, pagination style, the
   data shapes of the objects involved. Use `web_fetch` on the official docs;
   read local code with `read_file` when the "API" is an internal module.
3. List candidate tools. Prefer broad coverage of read operations plus the
   few write operations the tasks need. Add a workflow tool (one call that
   chains several API calls) only when the agent would otherwise repeat the
   same five-call sequence.
4. Decide the transport: stdio for a local, single-user server that Navin
   starts itself; streamable HTTP for a remote or shared server. Avoid SSE
   for new servers.
5. Confirm the plan with the user when scope is ambiguous (`ask_user` with
   two or three concrete tool sets).

### 2. Choose the stack

| Pick | When |
|---|---|
| Python + FastMCP (`mcp` package) | the team writes Python, the server wraps Python code or data science libs, fastest to iterate |
| TypeScript + official SDK | the server ships with `npx`, runs inside a Node toolchain, or must be installed by non-Python users |

Walkthroughs with a complete small server for each: `references/python-server.md`
and `references/typescript-server.md`. Read `references/mcp-best-practices.md`
before designing the first tool.

### 3. Design the tools

For each tool, write the contract before the code:

- Name: `<service>_<verb>_<object>` in snake_case (`notes_search`,
  `notes_create`). The prefix avoids collisions with other servers.
- Description: two to four sentences for the model, not for a human reader:
  what it does, what it returns, when to use a sibling tool instead, one
  example request. This text is the whole documentation the model sees.
- Input schema: typed fields with constraints and a description per field;
  defaults for optional fields; `limit` and `offset` (or a cursor) on anything
  that lists.
- Output: readable text by default, structured JSON on request
  (`response_format`), paginated with `has_more` and `next_offset`, capped at
  roughly 25,000 characters by returning fewer items, never by truncating.
- Errors: returned as tool errors (`isError`) with a sentence that says how to
  recover ("unknown folder; call notes_list_folders first"). Never a stack
  trace.
- Annotations: `readOnlyHint`, `destructiveHint`, `idempotentHint`,
  `openWorldHint` set honestly.

### 4. Implement

1. Scaffold the project as the chosen walkthrough shows (`pyproject.toml` or
   `package.json`, one module per domain, a shared API client, one error
   formatter).
2. Write the shared pieces first: API client with auth from environment
   variables, error mapping, pagination helper, response renderer.
3. Add tools one by one, each with its schema, description and annotations.
   Keep logging on stderr: stdout belongs to the protocol when running over
   stdio.
4. Add resources for data the model should read as context (configuration,
   schemas) and prompts for repeatable instructions, only when they earn
   their place.
5. Compile or type-check: `python3 -m py_compile server.py` or `npm run build`.

### 5. Run and test with a client

1. Start the server in a background `exec` session (stdio servers wait
   silently on stdin; HTTP servers print their port on stderr).
2. List the tools through the bundled client:

   ```bash
   python3 navin/skills/mcp-builder/scripts/connections.py list-tools \
     --transport stdio --command python3 --arg server.py
   python3 navin/skills/mcp-builder/scripts/connections.py call \
     --transport http --url http://127.0.0.1:8000/mcp \
     --tool notes_search --args '{"query": "budget", "limit": 5}'
   ```

   The script needs the `mcp` Python package; it prints an install hint when
   the package is missing.
3. Read the listed schemas as the model would: is every tool distinguishable
   from its siblings by description alone? Are required fields obvious?
4. Call each tool with valid, missing and wrong arguments. Check the error
   text tells the caller what to do next. Check a large listing stays under
   the size cap and reports `has_more`.
5. For HTTP servers, also check the endpoint answers with the auth header and
   rejects without it.

### 6. Write the evaluation set

The measure of the server is whether a model with only these tools answers
realistic questions. Follow `references/evaluation.md`: 10 or more questions,
read-only, independent, multi-step, each with one verifiable answer.

```bash
python3 navin/skills/mcp-builder/scripts/evaluate.py --validate-only evaluation.json
python3 navin/skills/mcp-builder/scripts/evaluate.py evaluation.json \
  --transport stdio --command python3 --arg server.py --output eval-report.md
```

`scripts/evaluation.example.json` shows the format (JSON; the XML form with
`<qa_pair>` elements is accepted too). The script validates the file, lists
the server tools and writes a report skeleton; it does not call an LLM. Run
each question through the agent itself (or a `spawn` subagent given only the
tool list and the question), fill in actual answers and verdicts, then fix the
tools that caused wrong turns and run again.

### 7. Wire into Navin

Switch to the `mcp-integration` skill: write the plugin `mcp.json` (stdio
`command`/`args` behind `${NAVIN_PLUGIN_ROOT}`, or `streamableHttp` `url` with
`${TOKEN}` headers), restrict with `enabledTools` if the server exposes more
than the plugin needs, enable the plugin and confirm the `mcp_<server>_<tool>`
names appear. Document the required environment variables in the plugin
README.

## Rules

- Descriptions are for the model: say what, what it returns, when not to use
  it. No marketing, no internal jargon.
- One concept per tool. A tool with a `mode` argument that changes what it
  does is two tools.
- Never load an entire dataset to answer a page; pass `limit` and `offset`
  to the upstream API.
- Secrets come from environment variables, validated at startup with a
  clear message when missing. Never in code, never echoed in errors.
- Validate every input (pydantic or zod) and every path or URL that reaches
  the operating system or the network.
- Bind local HTTP servers to `127.0.0.1` and check the `Origin` header.
- Keep the server name stable and general (`notes_mcp`, `notes-mcp-server`):
  Navin derives tool names from it.

## Bundled files

- `references/mcp-best-practices.md` - naming, descriptions, schemas,
  response size, pagination, errors, annotations, auth, transports, security.
- `references/python-server.md` - FastMCP walkthrough: complete example
  server, lifespan, pydantic inputs, resources, prompts, stdio and HTTP,
  `pyproject.toml`.
- `references/typescript-server.md` - official TypeScript SDK walkthrough:
  complete example, zod schemas, stdio and HTTP transports, `package.json`,
  build.
- `references/evaluation.md` - how to write the questions, grading rubric,
  iteration loop.
- `scripts/connections.py` - stdio, streamable HTTP and SSE client: list
  tools, call a tool, print JSON.
- `scripts/evaluate.py` - validate an evaluation file, list tools, write the
  report skeleton.
- `scripts/evaluation.example.json` - eleven example questions for a notes
  server.
