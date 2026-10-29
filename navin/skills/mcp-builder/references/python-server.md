# Python server walkthrough (FastMCP)

FastMCP ships inside the official `mcp` package. It derives the tool schema
from type hints and pydantic models, so the code stays short and the schema
stays honest.

## Project layout

```
notes_mcp/
  pyproject.toml
  README.md
  src/notes_mcp/
    __init__.py
    server.py        # FastMCP instance, lifespan, transport selection
    store.py         # the thing being exposed (here an in-memory notes store)
    tools.py         # tool functions and their input models
  evaluation.json
```

### pyproject.toml

```toml
[project]
name = "notes_mcp"
version = "0.1.0"
description = "MCP server exposing a notes workspace"
requires-python = ">=3.11"
dependencies = ["mcp>=1.2", "pydantic>=2.5"]

[project.scripts]
notes-mcp = "notes_mcp.server:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Install in development mode with `python3 -m pip install -e .` (or `uv pip
install -e .`). Add `httpx` when the server calls a remote API.

## Complete small server

One file version, enough to run and test. Split it as above once it grows.

```python
#!/usr/bin/env python3
"""notes_mcp: expose a small notes store to LLM clients."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

CHARACTER_LIMIT = 25_000


# ---- the capability being exposed -------------------------------------

@dataclass
class Note:
    id: str
    title: str
    folder: str
    body: str
    tags: list[str] = field(default_factory=list)


class NoteStore:
    def __init__(self) -> None:
        self.notes: dict[str, Note] = {}

    def search(self, query: str, folder: str | None) -> list[Note]:
        q = query.lower()
        return [
            n for n in self.notes.values()
            if (folder is None or n.folder == folder)
            and (q in n.title.lower() or q in n.body.lower())
        ]

    def folders(self) -> list[str]:
        return sorted({n.folder for n in self.notes.values()})


@dataclass
class AppContext:
    store: NoteStore


@asynccontextmanager
async def lifespan(_server: FastMCP) -> AsyncIterator[AppContext]:
    """Open shared resources once; close them when the server stops."""
    store = NoteStore()
    seed = os.environ.get("NOTES_SEED_FILE")
    if seed:
        with open(seed, encoding="utf-8") as fh:
            for raw in json.load(fh):
                store.notes[raw["id"]] = Note(**raw)
    try:
        yield AppContext(store=store)
    finally:
        pass  # close database connections, HTTP clients, etc.


mcp = FastMCP(
    "notes_mcp",
    lifespan=lifespan,
    host=os.environ.get("NOTES_MCP_HOST", "127.0.0.1"),
    port=int(os.environ.get("NOTES_MCP_PORT", "8000")),
)


# ---- input types --------------------------------------------------------

class ResponseFormat(str, Enum):
    markdown = "markdown"
    json = "json"


Query = Annotated[str, Field(min_length=1, max_length=200,
                             description="Text to find in titles and bodies, e.g. 'budget'")]
Folder = Annotated[str | None, Field(
    description="Restrict to one folder path such as 'finance'; omit for all")]
Limit = Annotated[int, Field(ge=1, le=100, description="Maximum notes to return")]
Offset = Annotated[int, Field(ge=0, description="Notes to skip, for pagination")]
Format = Annotated[ResponseFormat, Field(
    description="'markdown' for readable text, 'json' for structured data")]


@dataclass
class SearchParams:
    query: str
    folder: str | None
    limit: int
    offset: int
    response_format: ResponseFormat


# ---- tools ------------------------------------------------------------

@mcp.tool(
    name="notes_search",
    annotations={"readOnlyHint": True, "destructiveHint": False,
                 "idempotentHint": True, "openWorldHint": False},
)
async def notes_search(
    query: Query,
    ctx: Context,
    folder: Folder = None,
    limit: Limit = 20,
    offset: Offset = 0,
    response_format: Format = ResponseFormat.markdown,
) -> str:
    """Search notes by text in title or body, optionally inside one folder.

    Returns a page of matching notes (id, title, folder, tags) with pagination
    fields. To read the full body of one note call notes_get with its id.
    Example: {"query": "budget", "folder": "finance", "limit": 5}
    """
    params = SearchParams(query, folder, limit, offset, response_format)
    store: NoteStore = ctx.request_context.lifespan_context.store
    if params.folder is not None and params.folder not in store.folders():
        return (f"Error: folder '{params.folder}' not found. "
                "Call notes_list_folders to see valid folder paths.")
    matches = store.search(params.query, params.folder)
    page = matches[params.offset: params.offset + params.limit]
    while len(page) > 1 and len(_render(page, matches, params)) > CHARACTER_LIMIT:
        page = page[: len(page) // 2]
    return _render(page, matches, params)


def _render(page: list[Note], matches: list[Note], params: SearchParams) -> str:
    next_offset = params.offset + len(page)
    has_more = next_offset < len(matches)
    if params.response_format is ResponseFormat.json:
        return json.dumps({
            "total": len(matches), "count": len(page), "offset": params.offset,
            "items": [n.__dict__ for n in page],
            "has_more": has_more, "next_offset": next_offset if has_more else None,
        }, indent=2)
    if not page:
        return f"No notes match '{params.query}'" + (
            f" in folder {params.folder}." if params.folder else ".")
    lines = [f"## {len(matches)} note(s) match '{params.query}'", ""]
    for n in page:
        tags = f" [{', '.join(n.tags)}]" if n.tags else ""
        lines.append(f"- {n.title} ({n.id}) in {n.folder}{tags}")
    if has_more:
        lines += ["", f"More results: call again with offset={next_offset}."]
    return "\n".join(lines)


@mcp.tool(name="notes_list_folders",
          annotations={"readOnlyHint": True, "idempotentHint": True})
async def notes_list_folders(ctx: Context) -> str:
    """List every folder path in the workspace, one per line. Use the result
    as the folder argument of notes_search."""
    store: NoteStore = ctx.request_context.lifespan_context.store
    folders = store.folders()
    return "\n".join(folders) if folders else "The workspace has no folders yet."


# ---- resources and prompts ------------------------------------------------

@mcp.resource("notes://{note_id}")
async def note_resource(note_id: str) -> str:
    """Full markdown body of one note."""
    ctx = mcp.get_context()
    store: NoteStore = ctx.request_context.lifespan_context.store
    note = store.notes.get(note_id)
    if note is None:
        raise ValueError(f"note {note_id} not found")
    return f"# {note.title}\n\n{note.body}"


@mcp.prompt(name="summarise_folder")
def summarise_folder(folder: str) -> str:
    """Instructions to summarise every note of one folder."""
    return (f"Call notes_search with folder='{folder}' and an empty-ish query, "
            "read each note resource, then write a five-line summary.")


# ---- entry point --------------------------------------------------------

def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    print(f"notes_mcp starting on {transport}", file=sys.stderr)
    if transport == "http":
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio


if __name__ == "__main__":
    main()
```

Points to notice:

- `lifespan` creates shared state once and exposes it through
  `ctx.request_context.lifespan_context`. Put database pools and HTTP clients
  there, not at import time.
- The docstring of the tool function becomes the description the model
  reads. Write it for the model.
- Each argument is an `Annotated` type with a pydantic `Field`: constraints
  and descriptions land in the JSON schema, and the schema stays flat (the
  model sends `{"query": ..., "limit": ...}`, not a nested object). Use a
  `BaseModel` argument only for genuinely nested structures such as a list of
  objects.
- Expected failures return an `Error: ...` string with the next step. Raising
  an exception also works: FastMCP converts it into a tool error with the
  exception message, so keep messages clean.
- All logging goes to `sys.stderr`.

## Running

stdio (what Navin uses for a plugin-shipped server):

```bash
python3 server.py
# or, after install: notes-mcp
```

Streamable HTTP on 127.0.0.1:8000, endpoint `/mcp`:

```bash
MCP_TRANSPORT=http NOTES_MCP_PORT=8000 python3 server.py
```

For HTTP in production, put TLS and bearer-token checks in front (reverse
proxy or a Starlette middleware) and keep the bind address local.

## Testing

```bash
python3 -m py_compile server.py
python3 navin/skills/mcp-builder/scripts/connections.py list-tools \
  --transport stdio --command python3 --arg server.py
python3 navin/skills/mcp-builder/scripts/connections.py call \
  --transport stdio --command python3 --arg server.py \
  --tool notes_search --args '{"query": "budget", "limit": 2}'
```

For unit tests, drive the store and the render function directly, and run
one end-to-end test through `connections.Connection` in pytest with
`asyncio.run`.

## Calling a remote API

Replace `NoteStore` with a thin client:

```python
import httpx

class ApiClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url, timeout=30.0,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )

    async def get(self, path: str, **params) -> dict:
        response = await self._http.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def aclose(self) -> None:
        await self._http.aclose()


def describe_http_error(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code in (401, 403):
            return "Error: authentication failed. Check the NOTES_API_TOKEN variable."
        if code == 404:
            return "Error: the requested object does not exist. Check the id."
        if code == 429:
            return "Error: rate limit reached. Wait a minute before retrying."
        return f"Error: upstream API returned HTTP {code}. Retry later."
    if isinstance(exc, httpx.TimeoutException):
        return "Error: the upstream API timed out. Retry with a smaller limit."
    return f"Error: {type(exc).__name__}"
```

Create the client in `lifespan`, read the token from the environment and fail
at startup when it is missing:

```python
token = os.environ.get("NOTES_API_TOKEN")
if not token:
    print("NOTES_API_TOKEN is required", file=sys.stderr)
    sys.exit(1)
```

## Quality checklist

- Server name `<service>_mcp`; tool names `<service>_<verb>_<object>`.
- Every tool: docstring for the model, pydantic input with descriptions,
  annotations, pagination where it lists, `response_format` where it returns
  data.
- Size cap enforced by returning fewer items.
- Errors are strings with a next step; nothing internal leaks.
- No `print` to stdout; `py_compile` passes; `connections.py list-tools`
  shows the expected schemas.
