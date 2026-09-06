# Notes module - Overview

The **Notes** module (sidebar → **Notes**, route `#/notes`) is Navin's knowledge space: a Notion-style block editor, structured databases, a knowledge graph, tasks, files - and above all a **native AI memory**: your notes become queryable context for the agent, and a note can launch real work (agent blocks).

Everything is **local-first**: each note is a Markdown file with YAML frontmatter under `~/.navin/notes/`. No proprietary format, no lock-in - your notes stay readable with any editor.

## Layout

```
┌────────────┬──────────────────┬──────────────────────────────┐
│ Rail       │  Notes list      │  Editor                      │
│ Sections   │  (search,        │  title, tags, blocks,        │
│ Folders    │   pinned,        │  autosave + conflicts        │
│ Tags       │   previews)      │                              │
└────────────┴──────────────────┴──────────────────────────────┘
```

All three columns scroll independently. **Focus mode** hides the chat for full-screen writing. The Navin chat stays available on the right in the standard view.

## Sections

| Section | Contents |
| --- | --- |
| **All notes** | Full list, full-text search (Ctrl+K), pinned first |
| **Database** | Notes as structured data: **Table** view (editable cells) and **Kanban** board (drag & drop by status), custom properties (text, number, date, status, select...), saved views with filters |
| **Graph** | Knowledge graph: note + tag nodes, `[[wikilink]]` and tag edges; click a node to open the note or filter by tag |
| **Tasks** | Every `- [ ]` item across all notes, toggleable here, quick add without opening a note, `p1`/`p2` priorities |
| **Files** | All attachments (`_files/`), direct upload, which notes reference them, **in-product preview** on click |
| **Trash** | Deleted notes: restore, delete forever, or empty the trash (in-app confirmations, never a browser dialog) |
| **Commands** | Reference of every slash command and tip, generated from the real catalog |

Below the sections: the **folders** tree (inline creation, deletion trashes the contents) and the **tags** list with counts.

## Editor

Block editor (Tiptap) with native Markdown: what you type is what gets stored.

### Slash commands

Type `/` at the start of a line:

| Command | Block |
| --- | --- |
| `/title` `/h2` `/h3` | Headings |
| `/text` | Paragraph |
| `/todo` | Checkbox task list |
| `/bullet` `/numbered` | Lists |
| `/quote` | Quote |
| `/code` | Code block with syntax highlighting |
| `/table` | Table |
| `/divider` | Horizontal rule |
| `/link` | Insert a hyperlink (in-app dialog: text + URL) |
| `/image` | Upload and embed a picture |
| `/file` | Attach a file |
| `/agent` | **Executable agent block** (see below) |

Menu search is accent-tolerant and matches French/English synonyms (`/tache` finds To-do).

### Format bar

Select some text: a floating bar appears with **bold**, **italic**, **link**, six **text colours** and six **highlights** (red, orange, amber, green, blue, purple - handy for marking priorities), plus an eraser to clear colours. Colours survive the Markdown round-trip (inline HTML) and stay readable in both light and dark themes.

### Links and knowledge

- `[[Note title]]` creates a **wikilink**; titles and **aliases** resolve case-insensitively.
- `/link` or the link button in the selection bar inserts a regular `[text](url)` web link; Ctrl+click (Cmd+click on macOS) opens it in the OS browser (via the gateway on packaged desktop / Tauri, where `window.open` is blocked).
- **Backlinks** appear automatically under each note.
- Frontmatter `#tags` feed the Tags section and the Graph.

### Saving

- **Autosave** (800 ms after the last keystroke) plus Ctrl+S to force.
- **Conflict detection**: the version check and write share an inter-process lock, so two writers cannot silently overwrite each other.
- A **version history** is kept per note in `.history/<note-id>/`.

### Attachments

Three ways to add a file to a note: `/image` or `/file`, **drag & drop**, or **paste** (Ctrl+V, handy for screenshots). The file goes to `_files/` and the note keeps a portable relative path (never a token inside the Markdown).

Clicking opens an **in-product preview** (crucial for packaged Windows/macOS/Linux desktop builds: no browser tab, no token-bearing URL):

- images (png, jpg, gif, webp, svg...),
- PDF (embedded Chromium viewer),
- video and audio (players with controls),
- Markdown (rich rendering: headings, tables, highlighted code),
- text/code (monospace view),
- other types: a "no preview" screen with a Download button (blob-based, the token never leaves the app).

## AI memory and Navin IA

The note toolbar has a **Navin IA** menu (next to Export):

| Action | What it does |
| --- | --- |
| **Ask Navin** | Seeds the chat with the note title + `~/.navin/notes/….md` path so you finish the question |
| **Summary** | Sends a prompt to write a `## Summary` section **at the top** of the same file |
| **Translation** | Sends a prompt to append a `## Translation` section **below** the existing text |
| **Correction** | Sends a prompt to **correct in place** (spelling, grammar, clarity) without duplicating the note |

Prompts look like: `Summarize this note : « Untitled » (fichier ~/.navin/notes/sans-titre-3.md)`.

- Notes are **semantically indexed** in the background: chunking by Markdown sections, embeddings (Ollama `nomic-embed-text` by default via the semanticSearch endpoint), **hybrid** semantic + lexical search with RRF fusion.
- Full-text search uses a persistent SQLite FTS5 index over titles, bodies, tags, and aliases. Hosts without FTS5 use the deterministic lexical fallback. External edits are detected by file fingerprints.
- The agent has a read-only `notes` tool: it searches and reads your notes as context during any turn - your notes become the workspace memory.

Markdown folders, Obsidian vaults, and Notion Markdown zip exports can be imported with sandboxed extraction and explicit `rename`, `skip`, or `overwrite` conflict handling.

## Export

Per-note **Export** menu in the toolbar:

| Format | Behaviour |
| --- | --- |
| **Markdown (.md)** | Downloads the note (TipTap colour / highlight HTML preserved for round-trip) |
| **Text (.txt)** | Plain text only (HTML wrappers stripped) |
| **PDF** | Opens the browser print dialog from the live editor HTML so colours, lists, tables and tasks are kept - choose "Save as PDF" |

## Agent blocks (`/agent`)

An agent block is a fenced code block with the `agent` language (perfect Markdown round-trip, readable by the agent with file tools). The inserted template describes **Objective / Sources / Output**. The block renders as a card with a **Run** button: clicking builds a structured prompt (block spec + note title + file path) and places it in the chat composer; the agent executes and appends the result to the note under `## Result`.

This is the "the note no longer describes the work: it launches the work" building block.

## Storage and format

```
~/.navin/notes/
├── <folder>/<note>.md       # Markdown + YAML frontmatter
├── _files/                  # attachments (hash prefix)
├── .history/                # per-note history
├── .notes-manifest.json     # atomic id-to-path lookup
├── .search.sqlite3          # persistent full-text index
├── _views.json              # database views
└── .trash/                  # trash
```

Note frontmatter:

```yaml
---
id: 9f2c1a
title: V3 launch
tags: [product, roadmap]
aliases: [V3]
pinned: false
archived: false
folder: projects
props: { status: "in progress", priority: 1 }
created: 2026-08-10T18:00:00.000000Z
updated: 2026-08-10T19:30:00.000000Z
---
```

Critical files use same-directory temporary files, file `fsync`, atomic `os.replace`, and directory `fsync` where the operating system supports it. Notes, views, history, manifests, and memory-index generations are protected against partial writes.
