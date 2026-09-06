---
name: project-metadata
description: Create and maintain the .navin/metadata project knowledge base - file roles, dependencies, and the metagraph index - so questions map instantly to the right files without grepping. Use at project start and whenever files are added, moved, or repurposed.
metadata: {"navin":{"emoji":"🗺️","category":"development"}}
---

# Project Metadata (.navin/metadata)

## Overview

Every serious project gets a `.navin/metadata/` folder at its root: a machine-readable
map of what each file is, what it does, and what it depends on. The Dev
workbench renders it as an interactive **metagraph** (nodes = files, colored by
nature; edges = dependencies). Your job: build it once, keep it truthful, and
**consult it before grepping** when the user asks "where is X handled?".

## The `metagraph` tool

You have a dedicated tool named `metagraph` that builds this graph live
(parsed imports + your `.navin/metadata/index.json` annotations). Use it FIRST:

- `metagraph(action="overview")` - file counts by kind, the most connected
  hub files, and whether `.navin/metadata/` exists yet.
- `metagraph(action="file", path="navin/agent/loop.py")` - one file's kind,
  role, what it **depends on**, and what **depends on it**.
- `metagraph(action="find", query="checkpoint", kind="back")` - locate files
  by path fragment, role text, and/or kind
  (`front`/`back`/`sql`/`config`/`test`/`docs`).
- `metagraph(action="annotate", files={...})` - record roles. This is the only
  way to write `.navin/metadata/index.json`: it merges the entries you pass into
  whatever is already there, validates them, and stamps each one with the file's
  current size so a later run can tell which roles went stale. Never write that
  file with `write_file` - a hand-written entry carries no stamp and is invisible
  to staleness reporting.

Reach for `grep` only when the graph cannot answer (string literals, exact
code contents). The tool reads roles from `.navin/metadata/index.json`, so the
richer you keep the index, the better `find` answers become.

## Files

```
.navin/metadata/
  index.json        # the knowledge base (format below)
  ARCHITECTURE.md   # human-readable summary: layers, entry points, data flow
```

## index.json format

```json
{
  "version": 1,
  "updated": "2026-07-22T10:00:00Z",
  "files": {
    "webui/src/App.tsx": {
      "kind": "front",
      "role": "Root React component: routing, view state, session wiring",
      "depends_on": ["webui/src/lib/api.ts", "webui/src/components/Sidebar.tsx"],
      "tags": ["entry"]
    },
    "navin/webui/ws_http.py": {
      "kind": "back",
      "role": "Gateway HTTP routes: sessions, files, settings, studio APIs",
      "depends_on": ["navin/webui/file_tree.py"],
      "tags": ["api"]
    }
  }
}
```

- `kind`: one of `front`, `back`, `sql`, `config`, `test`, `docs`, `asset`, `other`.
- `role`: ONE sentence, concrete - what the file does, not what it is named.
- `depends_on`: project-relative paths this file imports/reads/calls. The
  backend already parses Python/JS imports automatically; only list what static
  parsing cannot see (SQL tables used, config files read, templates rendered,
  HTTP endpoints called).
- `tags`: optional, short (`entry`, `api`, `schema`, `hot-path`, `deprecated`).

## Workflow

**Init (new or existing project)** - when starting work on a project that has
no `.navin/metadata/`:
1. Call `metagraph(action="overview")` and read the root and discovery line it
   prints back. A count only means "the whole project" if the root is the project
   you were asked about; when discovery says gitignored files were included, or
   the file count is far below what you expect from the tree, say so instead of
   reporting coverage as if the map were complete.
2. For each significant source file, read enough to write an honest one-line
   role. Batch-read; don't summarize files you haven't opened.
3. Record them with `metagraph(action="annotate", files={...})`, in batches, then
   write `ARCHITECTURE.md`. Report coverage against the root you named in step 1
   ("214 of 226 files described, 12 skipped as assets").

**Maintain** - the runtime context tells you, every turn, which recorded roles
have gone stale and which code files have none. Annotate the files you touched as
part of finishing the work, in the same turn. Do not stop a task to work through
the whole backlog. A stale index is worse than none.

**Answer questions** - when the user asks where something lives or how parts
connect: call `metagraph(action="find", ...)` or `metagraph(action="file", ...)`
first, answer with the exact file list and the dependency chain. Grep only to
verify or when the graph lacks the answer, then backfill what you learned into
the index.

## Rules

- Never index secrets or copy file contents into the index - roles only.
- Cap `role` at ~120 chars; this is a map, not documentation.
- Don't index vendored/generated code (`dist/`, lockfiles get kind `config`, no role needed).
- `ARCHITECTURE.md` stays under one page: layers, entry points, main flows, where to start reading.
- The metagraph view in the Dev workbench reads this file live - after a big
  refresh, tell the user to open the Graph tab to see it.
