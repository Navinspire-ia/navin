"""Agent tool over the user's recorded meetings (the Meetings module).

Meetings are where decisions are actually made. Without this tool the agent
could only reach them if the user pasted a transcript into the chat; now
"what did we decide with Ana last Tuesday?" resolves against the minutes and
the transcript of the right meeting, and the follow-up ("save that as a note",
"turn the actions into tasks") lands in the Notes module through
:mod:`navin.meetings.notes_bridge`.

Reads never block the event loop: the store is plain files and a search
re-reads every record, so it runs on a worker thread.
"""

from __future__ import annotations

import asyncio
from typing import Any

from navin.agent.tools.base import Tool, ToolResult, tool_parameters

_SEARCH_LIMIT = 8
_LIST_LIMIT = 20
_MAX_SECTION_CHARS = 24_000
_SECTIONS = ("summary", "notes", "transcript", "all")


@tool_parameters(
    {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["search", "read", "list", "save_note"],
                "description": (
                    "search: meetings whose title, minutes, notes or transcript match; "
                    "read: one meeting (minutes by default, or a section); "
                    "list: most recent meetings; "
                    "save_note: write the meeting into the Notes module as a linked note "
                    "with its action items as checkable tasks."
                ),
            },
            "query": {
                "type": "string",
                "description": "Keywords for action=search (title, people, topics, decisions).",
            },
            "id": {
                "type": "string",
                "description": "Meeting id for read/save_note (from search/list results).",
            },
            "title": {
                "type": "string",
                "description": "Meeting title for read/save_note when the id is unknown.",
            },
            "section": {
                "type": "string",
                "enum": list(_SECTIONS),
                "description": (
                    "For action=read: summary (default, the minutes), notes (taken live), "
                    "transcript (verbatim, long), or all."
                ),
            },
            "include_transcript": {
                "type": "boolean",
                "description": "For action=save_note: also copy the transcript into the note.",
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
class MeetingsTool(Tool):
    """Search, read and file the user's meetings."""

    _scopes = {"core", "subagent"}

    @property
    def name(self) -> str:
        return "meetings"

    @property
    def description(self) -> str:
        return (
            "Search and read the user's recorded meetings (Navin Meetings module: "
            "minutes, decisions, action items, live notes, transcripts). Use "
            "action=search when the user refers to a meeting, a call or something "
            "that was said or decided in one ('what did we agree with Ana about "
            "pricing?'), then action=read for the minutes. action=save_note files "
            "a meeting into Notes with its action items as tasks. Cite the meeting "
            "title and date when answering."
        )

    @property
    def read_only(self) -> bool:
        # save_note writes to the Notes module; reads dominate, and a note
        # save is an append-only, history-backed operation.
        return False

    def call_read_only(self, arguments: Any) -> bool:
        action = arguments.get("action") if isinstance(arguments, dict) else None
        return action in {"search", "read", "list"}

    def call_concurrency_safe(self, arguments: Any) -> bool:
        return self.call_read_only(arguments)

    async def execute(self, **kwargs: Any) -> Any:
        from navin.meetings.store import MeetingStoreError

        action = kwargs.get("action")
        try:
            if action == "search":
                return await asyncio.to_thread(
                    self._search, kwargs.get("query"), int(kwargs.get("limit") or _SEARCH_LIMIT)
                )
            if action == "read":
                return await asyncio.to_thread(
                    self._read, kwargs.get("id"), kwargs.get("title"), kwargs.get("section")
                )
            if action == "list":
                return await asyncio.to_thread(
                    self._list, int(kwargs.get("limit") or _LIST_LIMIT)
                )
            if action == "save_note":
                return await asyncio.to_thread(
                    self._save_note,
                    kwargs.get("id"),
                    kwargs.get("title"),
                    bool(kwargs.get("include_transcript")),
                )
        except MeetingStoreError as exc:
            return ToolResult.error(f"Error: {exc}")
        except OSError as exc:
            return ToolResult.error(f"Error: meetings storage failed: {exc}")
        return self.unknown_action(action)

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _store() -> Any:
        from navin.meetings.store import default_meeting_store

        return default_meeting_store()

    @staticmethod
    def _when(row: dict[str, Any]) -> str:
        for key in ("started_at", "date", "created_at", "updated_at"):
            value = row.get(key)
            if isinstance(value, str) and value:
                return value[:16].replace("T", " ")
        return "?"

    def _resolve_id(self, meeting_id: str | None, title: str | None) -> str | ToolResult:
        resolved = (meeting_id or "").strip()
        if resolved:
            return resolved
        wanted = (title or "").strip().casefold()
        if not wanted:
            return ToolResult.error("Error: this action requires 'id' or 'title'")
        store = self._store()
        rows = store.list(limit=500)
        exact = [row for row in rows if str(row.get("title") or "").strip().casefold() == wanted]
        if len(exact) == 1:
            return str(exact[0]["id"])
        partial = [row for row in rows if wanted in str(row.get("title") or "").casefold()]
        candidates = exact or partial
        if len(candidates) == 1:
            return str(candidates[0]["id"])
        if not candidates:
            return ToolResult.error(
                f"Error: no meeting titled {title!r}. Use action=search or action=list first."
            )
        lines = [f"Several meetings match {title!r}; pass the id:"]
        for row in candidates[:10]:
            lines.append(f"- {row.get('title') or 'Meeting'} ({self._when(row)}, id={row['id']})")
        return ToolResult.error("\n".join(lines))

    def _search(self, query: str | None, limit: int) -> Any:
        text = (query or "").strip()
        if not text:
            return ToolResult.error("Error: action=search requires 'query'")
        rows = self._store().search(text, limit=max(1, min(limit, 50)))
        if not rows:
            return (
                "No meeting matched. Try fewer or different keywords, or action=list "
                "to browse the most recent meetings."
            )
        lines = [f"{len(rows)} meeting(s) matching {text!r} (best first):"]
        for row in rows:
            lines.append(
                f"- {row.get('title') or 'Meeting'} ({self._when(row)}, id={row['id']}, "
                f"score={row.get('score', 0)})"
            )
        lines.append("Use meetings action=read with an id for the minutes.")
        return "\n".join(lines)

    def _list(self, limit: int) -> str:
        rows = self._store().list(limit=max(1, min(limit, 50)))
        if not rows:
            return "No meetings recorded yet."
        lines = [f"{len(rows)} most recent meeting(s):"]
        for row in rows:
            status = row.get("status")
            suffix = f", status={status}" if status else ""
            lines.append(f"- {row.get('title') or 'Meeting'} ({self._when(row)}, id={row['id']}{suffix})")
        return "\n".join(lines)

    def _read(self, meeting_id: str | None, title: str | None, section: str | None) -> Any:
        resolved = self._resolve_id(meeting_id, title)
        if isinstance(resolved, ToolResult):
            return resolved
        wanted = (section or "summary").strip().lower()
        if wanted not in _SECTIONS:
            return ToolResult.error(f"Error: unknown section {section!r}; use one of {', '.join(_SECTIONS)}")
        record = self._store().get(resolved)
        meta = record.get("meta") or {}
        header = [f"# {meta.get('title') or 'Meeting'}", f"id: {record['id']}  date: {self._when(meta)}"]
        speakers = meta.get("speakers")
        if isinstance(speakers, list) and speakers:
            header.append("participants: " + ", ".join(str(name) for name in speakers if str(name).strip()))
        if meta.get("note_id"):
            header.append(f"linked note: id={meta['note_id']}")
        if isinstance(meta.get("duration_sec"), (int, float)) and meta["duration_sec"] > 0:
            header.append(f"duration: {int(meta['duration_sec'] // 60)} min")

        blocks: list[str] = []
        order = ("summary", "notes", "transcript") if wanted == "all" else (wanted,)
        for name in order:
            body = str(record.get(name) or "").strip()
            label = {"summary": "Minutes", "notes": "Live notes", "transcript": "Transcript"}[name]
            if not body:
                if wanted != "all":
                    blocks.append(
                        f"## {label}\n_(empty)_"
                        + (
                            "\n\nNo minutes yet: read section=transcript or section=notes instead."
                            if name == "summary"
                            else ""
                        )
                    )
                continue
            if len(body) > _MAX_SECTION_CHARS:
                body = body[:_MAX_SECTION_CHARS] + "\n... (truncated)"
            blocks.append(f"## {label}\n{body}")
        return "\n".join(header) + "\n\n" + "\n\n".join(blocks)

    def _save_note(self, meeting_id: str | None, title: str | None, include_transcript: bool) -> Any:
        from navin.meetings.notes_bridge import save_meeting_to_note

        resolved = self._resolve_id(meeting_id, title)
        if isinstance(resolved, ToolResult):
            return resolved
        result = save_meeting_to_note(
            resolved, include_transcript=include_transcript, actor="agent"
        )
        verb = "Created" if result["created"] else "Updated"
        return (
            f"{verb} note '{result['title']}' (id={result['note_id']}, "
            f"~/.navin/notes/{result['path']}) with {result['action_items']} action item(s) "
            "as tasks. It is linked to the meeting (props.meeting_id) and searchable with the "
            "notes tool."
        )


__all__ = ["MeetingsTool"]
