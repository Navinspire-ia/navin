# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent tool over the user's personal notes (the Notes module).

Notes are the user's long-term memory: decisions, meetings, projects, ideas,
todo lists, saved research. This tool is how the agent answers "what did we
decide about X?" or "summarize my notes about Y" - it must never guess when
the answer may be written down.

Writes go through the store, not through the file tools: the store keeps the
frontmatter (id, updated, tags) valid, snapshots the previous version into the
history, refreshes the manifest and the full-text index, and needs no
filesystem approval since ``~/.navin/notes`` is the user's own vault. Every
write is reversible from the Notes history panel.

Reads and writes run on a worker thread: the store walks the vault on disk and
the gateway event loop must keep streaming other sessions meanwhile.
"""

from __future__ import annotations

import asyncio
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters

_SEARCH_LIMIT = 8
_LIST_LIMIT = 20
_MAX_NOTE_CHARS = 24_000
_MAX_WRITE_CHARS = 200_000


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "read", "list", "create", "append", "update", "add_task"],
                "description": (
                    "search: best passages across all notes for a question; "
                    "read: full note content; list: browse notes by folder/tag; "
                    "create: new note (title + markdown); append: add markdown at the "
                    "end of a note; update: replace a note's markdown (and/or title, "
                    "tags); add_task: add a '- [ ]' task to a note (or the Tasks inbox)."
                ),
            },
            "query": {
                "type": "string",
                "description": "Question or keywords for action=search.",
            },
            "id": {
                "type": "string",
                "description": "Note id for read/append/update/add_task (from search/list results).",
            },
            "title": {
                "type": "string",
                "description": (
                    "Note title: target for read/append/update/add_task when the id is "
                    "unknown; the new title for create (or the renamed title for update)."
                ),
            },
            "markdown": {
                "type": "string",
                "description": "Markdown body for create/append/update.",
            },
            "text": {
                "type": "string",
                "description": "Task text for action=add_task.",
            },
            "folder": {
                "type": "string",
                "description": (
                    "Folder path such as 'projects/navin': filter for list, destination "
                    "for create."
                ),
            },
            "tag": {
                "type": "string",
                "description": "Restrict action=list to one tag (without '#').",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags to set for create/update (without '#').",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Max results (default 8 for search, 20 for list).",
            },
        },
        "required": ["action"],
    }
)
class NotesTool(Tool):
    """Search, read and write the user's personal notes."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "notes"

    @property
    def description(self) -> str:
        return (
            "The user's personal notes (Navin Notes module: meeting notes, decisions, "
            "projects, research, tasks). Use action=search whenever the user asks about "
            "something they may have written down ('what did we decide about pricing?'), "
            "then action=read to open the full note. To write for the user, prefer "
            "action=create / append / update / add_task over editing files under "
            "~/.navin/notes directly: the store keeps the metadata, history and search "
            "index right. Cite the note title when answering."
        )

    @property
    def read_only(self) -> bool:
        return False

    def call_read_only(self, arguments: Any) -> bool:
        action = arguments.get("action") if isinstance(arguments, dict) else None
        return action in {"search", "read", "list"}

    def call_concurrency_safe(self, arguments: Any) -> bool:
        return self.call_read_only(arguments)

    async def execute(self, **kwargs: Any) -> Any:
        from navin.notes.store import NotesError

        action = kwargs.get("action")
        limit = int(kwargs.get("limit") or 0)
        try:
            if action == "search":
                return await self._search(kwargs.get("query"), limit or _SEARCH_LIMIT)
            if action == "read":
                return await asyncio.to_thread(self._read, kwargs.get("id"), kwargs.get("title"))
            if action == "list":
                return await asyncio.to_thread(
                    self._list, kwargs.get("folder"), kwargs.get("tag"), limit or _LIST_LIMIT
                )
            if action == "create":
                return await asyncio.to_thread(
                    self._create,
                    kwargs.get("title"),
                    kwargs.get("markdown"),
                    kwargs.get("folder"),
                    kwargs.get("tags"),
                )
            if action == "append":
                return await asyncio.to_thread(
                    self._append, kwargs.get("id"), kwargs.get("title"), kwargs.get("markdown")
                )
            if action == "update":
                return await asyncio.to_thread(
                    self._update,
                    kwargs.get("id"),
                    kwargs.get("title"),
                    kwargs.get("markdown"),
                    kwargs.get("tags"),
                )
            if action == "add_task":
                return await asyncio.to_thread(
                    self._add_task, kwargs.get("id"), kwargs.get("title"), kwargs.get("text")
                )
        except NotesError as exc:
            return ToolResult.error(f"Error: {exc.message}")
        except OSError as exc:
            return ToolResult.error(f"Error: notes storage failed: {exc}")
        return self.unknown_action(action)

    # -- reads -----------------------------------------------------------

    async def _search(self, query: str | None, limit: int) -> Any:
        from navin.notes.memory import ask_notes
        from navin.notes.store import notes_root

        text = (query or "").strip()
        if not text:
            return ToolResult.error("Error: action=search requires 'query'")
        payload = await ask_notes(notes_root(), text, limit=max(1, min(limit, 50)))
        passages = payload["passages"]
        if not passages:
            return (
                "No notes matched. The user may not have written this down; "
                "try different keywords or action=list to browse."
            )
        mode = "semantic+lexical" if payload["semantic"] else "lexical (no embedding endpoint)"
        lines = [f"Best passages across all notes ({mode}):"]
        for passage in passages:
            where = f"{passage['title']}"
            if passage["folder"]:
                where += f" [{passage['folder']}]"
            if passage["heading"]:
                where += f" › {passage['heading']}"
            lines.append(f"- {where} (id={passage['note_id']}, line {passage['line']})")
            snippet = " ".join(passage["text"].split())
            lines.append(f"  {snippet[:300]}")
        lines.append("Use notes action=read with an id to open a full note.")
        return "\n".join(lines)

    @staticmethod
    def _resolve_id(note_id: str | None, title: str | None) -> str | ToolResult:
        """Note id from an explicit id, else from a unique (case-insensitive)
        title or alias match; a clear error otherwise."""
        from navin.notes.store import notes_root, scan_notes

        resolved = (note_id or "").strip()
        if resolved:
            return resolved
        wanted = (title or "").strip().lower()
        if not wanted:
            return ToolResult.error("Error: this action requires 'id' or 'title'")
        matches = [
            info
            for info in scan_notes(notes_root())
            if info["title"].strip().lower() == wanted
            or any(alias.strip().lower() == wanted for alias in info["aliases"])
        ]
        if len(matches) == 1:
            return str(matches[0]["id"])
        if not matches:
            return ToolResult.error(
                f"Error: no note titled {title!r}. Use action=search or action=list first."
            )
        lines = [f"Several notes are titled {title!r}; pass the id:"]
        for info in matches[:10]:
            where = f" [{info['folder']}]" if info["folder"] else ""
            lines.append(f"- {info['title']}{where} (id={info['id']})")
        return ToolResult.error("\n".join(lines))

    def _read(self, note_id: str | None, title: str | None) -> Any:
        from navin.notes.store import get_note, notes_root

        resolved = self._resolve_id(note_id, title)
        if isinstance(resolved, ToolResult):
            return resolved
        payload = get_note(notes_root(), resolved)
        note = payload["note"]
        header = [
            f"# {note['title']}",
            f"id: {note['id']}  path: ~/.navin/notes/{note['path']}",
        ]
        if note["tags"]:
            header.append("tags: " + ", ".join(f"#{t}" for t in note["tags"]))
        if note["updated"]:
            header.append(f"updated: {note['updated']}")
        body = payload["markdown"]
        if len(body) > _MAX_NOTE_CHARS:
            body = body[:_MAX_NOTE_CHARS] + "\n… (truncated)"
        return "\n".join(header) + "\n\n" + body

    def _list(self, folder: str | None, tag: str | None, limit: int) -> str:
        from navin.notes.store import list_notes, notes_root

        page = list_notes(
            notes_root(),
            folder=folder,
            tag=tag,
            limit=max(1, min(limit, 50)),
        )
        if not page["notes"]:
            return "No notes found for this filter."
        lines = [f"{page['total']} note(s):"]
        for note in page["notes"]:
            entry = f"- {note['title']} (id={note['id']}"
            if note["folder"]:
                entry += f", folder={note['folder']}"
            if note["tags"]:
                entry += ", tags=" + ",".join(note["tags"][:5])
            entry += f", updated={note['updated'][:10] if note['updated'] else '?'})"
            lines.append(entry)
            if note["snippet"]:
                lines.append(f"  {note['snippet'][:160]}")
        return "\n".join(lines)

    # -- writes ----------------------------------------------------------

    @staticmethod
    def _clean_markdown(markdown: Any, *, required: bool) -> str | ToolResult:
        text = markdown if isinstance(markdown, str) else ""
        if required and not text.strip():
            return ToolResult.error("Error: this action requires non-empty 'markdown'")
        if len(text) > _MAX_WRITE_CHARS:
            return ToolResult.error(
                f"Error: markdown is too long ({len(text)} chars, max {_MAX_WRITE_CHARS}); "
                "split it across notes."
            )
        return text

    @staticmethod
    def _clean_tags(tags: Any) -> list[str] | None:
        if tags is None:
            return None
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            return None
        return [str(tag).strip().lstrip("#") for tag in tags if str(tag).strip()]

    @staticmethod
    def _describe(note: dict[str, Any], verb: str) -> str:
        where = f" in folder '{note['folder']}'" if note.get("folder") else ""
        return (
            f"{verb} note '{note['title']}'{where} (id={note['id']}, "
            f"~/.navin/notes/{note['path']}). The previous version, if any, is in the "
            "Notes history."
        )

    def _create(self, title: str | None, markdown: Any, folder: str | None, tags: Any) -> Any:
        from navin.notes.store import create_note, notes_root, update_note

        cleaned_title = (title or "").strip()
        if not cleaned_title:
            return ToolResult.error("Error: action=create requires 'title'")
        body = self._clean_markdown(markdown, required=False)
        if isinstance(body, ToolResult):
            return body
        root = notes_root()
        page = create_note(root, title=cleaned_title, folder=folder or "", markdown=body)
        cleaned_tags = self._clean_tags(tags)
        if cleaned_tags:
            page = update_note(root, str(page["note"]["id"]), tags=cleaned_tags)
        return self._describe(page["note"], "Created")

    def _append(self, note_id: str | None, title: str | None, markdown: Any) -> Any:
        from navin.notes.store import get_note, notes_root, update_note

        resolved = self._resolve_id(note_id, title)
        if isinstance(resolved, ToolResult):
            return resolved
        addition = self._clean_markdown(markdown, required=True)
        if isinstance(addition, ToolResult):
            return addition
        root = notes_root()
        current = get_note(root, resolved)
        existing = current["markdown"]
        block = addition.strip("\n") + "\n"
        merged = block if not existing.strip() else existing.rstrip("\n") + "\n\n" + block
        if len(merged) > _MAX_WRITE_CHARS:
            return ToolResult.error(
                f"Error: the note would exceed {_MAX_WRITE_CHARS} chars; create a new note instead."
            )
        # base_updated makes the append lose (with a clear error) instead of
        # silently overwriting a save the user made a moment ago.
        page = update_note(
            root,
            resolved,
            markdown=merged,
            base_updated=current["note"].get("updated") or None,
        )
        lines_added = len(addition.strip("\n").splitlines())
        return self._describe(page["note"], f"Appended {lines_added} line(s) to")

    def _update(self, note_id: str | None, title: str | None, markdown: Any, tags: Any) -> Any:
        from navin.notes.store import get_note, notes_root, update_note

        resolved = self._resolve_id(note_id, title)
        if isinstance(resolved, ToolResult):
            return resolved
        body = self._clean_markdown(markdown, required=False) if markdown is not None else None
        if isinstance(body, ToolResult):
            return body
        cleaned_tags = self._clean_tags(tags)
        # With an id, 'title' is the new title; without one it only located the note.
        new_title = (title or "").strip() if (note_id or "").strip() else ""
        if body is None and cleaned_tags is None and not new_title:
            return ToolResult.error(
                "Error: action=update needs 'markdown', 'tags' or a new 'title' (with 'id')."
            )
        root = notes_root()
        current = get_note(root, resolved)
        page = update_note(
            root,
            resolved,
            markdown=body,
            title=new_title or None,
            tags=cleaned_tags,
            base_updated=current["note"].get("updated") or None,
        )
        return self._describe(page["note"], "Updated")

    def _add_task(self, note_id: str | None, title: str | None, text: Any) -> Any:
        from navin.notes.store import add_task, notes_root

        task = " ".join(str(text or "").split())
        if not task:
            return ToolResult.error("Error: action=add_task requires 'text'")
        resolved: str | None = None
        if (note_id or "").strip() or (title or "").strip():
            found = self._resolve_id(note_id, title)
            if isinstance(found, ToolResult):
                return found
            resolved = found
        result = add_task(notes_root(), text=task, note_id=resolved)
        return (
            f"Added task '- [ ] {result['text']}' to note '{result['note_title']}' "
            f"(id={result['note_id']}, line {result['line']}). It shows up in the Notes "
            "Tasks pane; the user can tick it there."
        )


__all__ = ["NotesTool"]
