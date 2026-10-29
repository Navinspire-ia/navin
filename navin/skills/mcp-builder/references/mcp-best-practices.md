# MCP server best practices

These rules apply to any language or SDK. The reader of a tool definition is
a language model: it has the name, the description and the input schema, and
nothing else, to decide whether and how to call the tool.

## Naming

- Server name: general, stable, no version. Python: `<service>_mcp`
  (`notes_mcp`). TypeScript: `<service>-mcp-server` (`notes-mcp-server`).
  Navin builds tool names from the server key, so renaming the server renames
  every tool.
- Tool name: `<service>_<verb>_<object>`, snake_case, verb first:
  `notes_search`, `notes_get`, `notes_create`, `notes_list_folders`. The
  service prefix matters because several servers are usually loaded at once.
- Verbs with a shared meaning: `list` (paginated, filterable), `get` (one
  object by id), `search` (free text), `create`, `update`, `delete`,
  `run` (an action that is none of the above).

## Descriptions written for a model

A good description answers four questions in two to four sentences:

1. What does the tool do, precisely? ("Full-text search over note titles and
   bodies; returns matching notes with id, title, folder and a snippet.")
2. What does it return, in which shape?
3. When should a sibling tool be used instead? ("To read the whole body of one
   note, call notes_get with its id.")
4. One example argument set.

Avoid: adjectives ("powerful"), implementation details ("uses SQLite"),
repetition of the schema. Field descriptions carry the per-argument detail,
with an example value: `folder: "Folder path such as projects/alpha; omit to
search everywhere"`.

The description must match the behaviour exactly. A tool that says "returns
all notes" but pages at 20 teaches the model to trust nothing.

## Input schemas

- Typed fields with constraints: min and max length, numeric ranges, enums
  for closed sets, patterns for ids.
- Every field has a description. Required fields are few; the rest have
  defaults.
- Reject unknown fields (`extra="forbid"` in pydantic, `z.strictObject` in
  zod) so a misspelled argument is an error, not a silent no-op.
- Pagination fields on every listing tool: `limit` (default 20, max 100) and
  `offset` (default 0), or `cursor` when the upstream API is cursor-based.
- A `response_format` enum (`markdown` default, `json`) on tools that return
  data.
- Dates as ISO 8601 strings; the schema says so.

## Responses

- Markdown mode: short headings, lists, human dates, display names with ids
  in parentheses, no verbose metadata. End a page with how to get the next
  one ("More results: call again with offset=20").
- JSON mode: complete objects, consistent field names, the same data as
  `structuredContent` when the SDK supports it.
- Pagination envelope:

  ```json
  {"total": 150, "count": 20, "offset": 0, "items": [], "has_more": true, "next_offset": 20}
  ```

- Size cap: measure the final text and stay under roughly 25,000 characters
  by returning fewer items. Set `next_offset` to the first item left out.
  Never cut a string in the middle.
- Empty result: say so in words ("No notes match 'budget' in folder
  finance"), not an empty array alone.

## Errors

Two channels exist:

- Tool errors: a result with `isError: true` and a text the model reads.
  Use them for anything the model can act on: bad argument, not found,
  permission denied, rate limited, upstream timeout.
- Protocol errors: JSON-RPC errors handled by the client application. Reserve
  them for malformed requests and server faults.

An error text names the problem and the next step: "Folder 'finace' not
found. Call notes_list_folders to see valid folder paths." Map upstream HTTP
statuses once, in one helper: 401 and 403 to an authentication message, 404
to a not-found message with the id, 429 to a wait suggestion, 5xx to a retry
suggestion. Never leak stack traces, internal URLs or secrets.

## Annotations

| Annotation | Set true when |
|---|---|
| `readOnlyHint` | the tool changes nothing |
| `destructiveHint` | the tool may delete or overwrite data |
| `idempotentHint` | repeating the call with the same arguments has no extra effect |
| `openWorldHint` | the tool talks to an external system |

Clients use them to auto-approve reads or confirm destructive calls. They are
hints, not security controls.

## Resources and prompts

- Resources expose data the model should read as context (a schema, a config,
  a document) addressed by URI, with optional templates (`notes://{id}`).
  Use them for things that are read, never for actions.
- Prompts are reusable instruction templates with arguments. Use them for
  repeatable workflows the server author knows well ("summarise folder X").
- Both are optional. A server with well-designed tools and no resources beats
  a server with everything half-done.

## Transports

| | stdio | streamable HTTP |
|---|---|---|
| Runs as | subprocess of the client | web service |
| Clients | one | many |
| Auth | environment variables | bearer token or OAuth on the endpoint |
| Use for | local tools, anything shipped in a Navin plugin | remote or shared servers |

stdio rule: stdout carries the protocol. Logs go to stderr. Never prompt for
input. Exit cleanly on EOF.

HTTP rule: prefer stateless JSON responses; bind to `127.0.0.1` when local;
validate the `Origin` header to block DNS rebinding; terminate TLS in front of
the server for anything remote. SSE is legacy; support it only for old
clients.

## Authentication and secrets

- Read credentials from environment variables at startup. Fail fast with a
  message naming the variable when one is missing.
- For HTTP servers, verify the bearer token on every request and accept only
  tokens issued for this server.
- Never put a secret in a tool result, a log line or an error text.
- Document the variables the operator must set.

## Input safety

- Validate and normalise file paths; refuse anything that escapes the allowed
  root.
- Validate URLs (scheme, host allow-list) before fetching.
- Never build shell commands from arguments; pass argument lists to
  subprocess APIs.
- Bound sizes: maximum query length, maximum `limit`, request timeouts.

## Testing checklist

- Every tool listed with name, description and schema that reads well.
- Valid call returns the documented shape in both formats.
- Missing required argument, wrong type and unknown field each return a
  clear error.
- Listing with `limit=1` returns `has_more` and a usable `next_offset`.
- Large result stays under the size cap.
- Server starts with no secrets and reports which variable is missing.
- stdio server writes nothing to stdout outside the protocol.
- Evaluation set passes (see `evaluation.md`).
