# Graph (metagraph)

The Dev module **Graph** tab shows the project's dependency map: files (or packages) as nodes, resolved imports as edges. It builds on the code index (`navin.index`) and `.metadata/index.json` annotations produced by `/atlas`.

This is not an architecture-as-code DSL (C4-style). The source of truth is analyzed code.

## Where to find it

In the Dev workbench (`#/dev`), open the **Graph** tab in the side panel (next to Search, Git, and so on).

## What the graph shows

| Element | Meaning |
| --- | --- |
| Node | One file (Files view) or one top-level folder (Packages view) |
| Color | Kind: front, back, sql, config, test, docs, asset, other |
| Edge | Resolved import / dependency (or manual `depends_on` in `.metadata`) |
| Dot size | Degree (more connected files draw larger) |
| Ring | Role is set (amber when the role is *stale* after the file changed) |

Display ceiling: 6000 nodes (most-connected files are kept first).

## Views

### Files

One node per project file. Click to pin details; double-click to open the file in the editor.

### Packages

Files are aggregated by top-level folder (`webui`, `navin`, `(root)` for root-level files). Double-click (or **Open package files**) switches to the Files view filtered to that folder.

## Interactions

- **Kind filters** - hide / show front, back, sql…
- **Search** - filter by path
- **Connected only** - hide files with no edges
- **Impact** - from a selected file, highlight every file that depends on it (transitive cone)
- **Path to…** - after selecting a source file, click a target to highlight the shortest dependency path
- **Zoom / pan / fit** - wheel, drag, buttons at the top-right of the canvas

The right panel lists role, kind, imports, and imported-by.

## Live updates

The graph refreshes without a manual reload:

1. The agent writes a file → the index is marked dirty → debounced rebuild (~300 ms)
2. A `.metadata` annotation is saved → immediate rebuild
3. The server broadcasts `metagraph_updated` (WebSocket) with a **structured diff** (added / removed / updated nodes and edges) plus positions
4. The client applies the diff when `generation` is contiguous; otherwise it refetches the full snapshot

A 20 s safety-net poll covers missed events.

## Atlas & metadata

| Command | Effect |
| --- | --- |
| `/atlas init` | First build of `.metadata/index.json` |
| `/atlas refresh` | Refresh roles / dependencies |
| `/atlas query …` | Orientation questions against the index |

Without `.metadata`, the graph still works: parsed imports + roles from docstrings. With `.metadata`, manual roles and `depends_on` enrich the map; fingerprints detect stale roles.

## Agent tool `metagraph`

The agent queries the same map as the UI.

| Action | Role |
| --- | --- |
| `overview` | Summary: kinds, hubs, role coverage, stale |
| `file` | Role + dependencies / dependents for one path |
| `find` | Search by path fragment / role / kind |
| `hubs` | Most connected files |
| `path` | Shortest path `from` → `to` |
| `impact` | Transitive dependents of a file (optional `radius`) |
| `cluster` | Members of a package / folder |
| `annotate` | Record a role (and optionally kind, tags, `depends_on`) |

Use `code_index` for symbol-level answers (definition, callers); use `metagraph` at file level.

## Technical architecture

```
navin.index (Python)  →  snapshot  →  Graph engine
                                      ├─ navin-core (Rust) when available
                                      └─ Python fallback otherwise
                                              ↓
                              payload + positions + generation
                                              ↓
                         HTTP GET …/metagraph?view=files|packages
                         WS metagraph_updated (diffs)
                                              ↓
                              DevMetagraph + MetagraphCanvas
```

- **Build / layout / queries**: Rust (`graph_build`, `graph_layout`, `graph_diff`, `graph_query`) via PyO3; deterministic force-directed layout with sticky positions for live diffs.
- **Fallback**: `NAVIN_DISABLE_NATIVE=1` or missing wheel → Python assembly; the canvas recomputes layout in TypeScript when the payload has no `positions`.
- **Install the native engine**: run `make native` from the repo root.

Open the **Graph** tab in Project Home to switch between files and packages views. The map updates live while you work.

## See also

- [Overview](./README.md)
- [Workbench](./workbench.md)
- [Commands](./commands.md) (`/atlas`)
