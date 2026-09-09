# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Meetings -> Notes bridge: one meeting becomes one linked, durable note.

A meeting record lives in the Meetings store (JSON under ``meetings/``). The
Notes module is where the user actually re-reads decisions, ticks action items
and lets the agent search ("what did we decide about pricing?"). This module
turns a meeting into a note in the ``meetings`` folder, tagged ``meeting`` and
carrying ``props.meeting_id`` so the link survives renames and moves, and it
writes the note id back into the meeting meta so both desks can jump to each
other.

Saving twice updates the same note (the Notes history keeps the previous
version), so the button and the agent action are safe to repeat after a
summary is regenerated.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from navin.meetings.store import MeetingStore, MeetingStoreError, default_meeting_store

NOTES_FOLDER = "meetings"
NOTE_TAG = "meeting"
MEETING_PROP = "meeting_id"

# Header words that identify the action-items table the minutes template asks
# for ("Owner | Action | Deadline"), in the UI languages the desk ships.
_OWNER_WORDS = ("owner", "responsable", "propriétaire", "proprietaire", "qui", "assign")
_ACTION_WORDS = ("action", "tâche", "tache", "task", "quoi")
_DEADLINE_WORDS = ("deadline", "due", "échéance", "echeance", "date", "quand", "delai", "délai")
_UNSET_WORDS = frozenset(
    {
        "",
        "-",
        "unassigned",
        "no deadline",
        "none",
        "n/a",
        "tbd",
        "non assigné",
        "non assigne",
        "sans échéance",
        "sans echeance",
        "aucune",
        "aucun",
    }
)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_ACTION_HEADING_RE = re.compile(r"^#{1,6}\s+.*(action|next step|to-?do|à faire|a faire)", re.I)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")
_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[( |x|X)\]\s+")


def _cell_words(cell: str) -> str:
    return re.sub(r"[*_`]", "", cell).strip().casefold()


def _column_index(headers: list[str], words: tuple[str, ...]) -> int | None:
    for index, header in enumerate(headers):
        folded = _cell_words(header)
        if any(word in folded for word in words):
            return index
    return None


def _split_row(line: str) -> list[str]:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [cell.strip() for cell in row.split("|")]


def _is_separator(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell.strip()) for cell in cells if cell)


def action_items_from_markdown(markdown: str) -> list[dict[str, str]]:
    """Owner / action / deadline triples from minutes.

    Reads the ``Owner | Action | Deadline`` table the report template asks
    for (any column order, English or French headers). When there is no such
    table, bullets under an "Actions" style heading are used with an empty
    owner. Placeholders such as ``unassigned`` and ``no deadline`` become
    empty strings so callers can render them the way they like.
    """
    lines = (markdown or "").replace("\r\n", "\n").splitlines()
    items: list[dict[str, str]] = []

    index = 0
    while index < len(lines):
        line = lines[index]
        if "|" not in line:
            index += 1
            continue
        headers = _split_row(line)
        if index + 1 >= len(lines) or not _is_separator(_split_row(lines[index + 1])):
            index += 1
            continue
        action_col = _column_index(headers, _ACTION_WORDS)
        if action_col is None:
            index += 2
            continue
        owner_col = _column_index(headers, _OWNER_WORDS)
        deadline_col = _column_index(headers, _DEADLINE_WORDS)
        index += 2
        while index < len(lines) and "|" in lines[index]:
            cells = _split_row(lines[index])
            index += 1

            def cell(col: int | None) -> str:
                if col is None or col >= len(cells):
                    return ""
                value = re.sub(r"[*_`]", "", cells[col]).strip()
                return "" if value.casefold() in _UNSET_WORDS else value

            action = cell(action_col)
            if not action:
                continue
            items.append(
                {"owner": cell(owner_col), "action": action, "deadline": cell(deadline_col)}
            )
    if items:
        return items

    in_actions = False
    for line in lines:
        if line.lstrip().startswith("#"):
            in_actions = bool(_ACTION_HEADING_RE.match(line.strip()))
            continue
        if not in_actions:
            continue
        match = _BULLET_RE.match(line)
        if not match:
            continue
        text = _CHECKBOX_RE.sub("", line).strip() if _CHECKBOX_RE.match(line) else match.group(1)
        text = re.sub(r"[*_`]", "", text).strip()
        if text and text.casefold() not in {"not discussed.", "_not discussed._"}:
            items.append({"owner": "", "action": text, "deadline": ""})
    return items


def task_line(item: Mapping[str, str]) -> str:
    """One Notes checkbox for an action item, with the due date the Notes
    task syntax understands when the deadline is an ISO date."""
    text = str(item.get("action") or "").strip()
    owner = str(item.get("owner") or "").strip()
    deadline = str(item.get("deadline") or "").strip()
    parts = [text]
    if owner:
        parts.append(f"({owner})")
    if deadline:
        iso = _ISO_DATE_RE.search(deadline)
        parts.append(f"@due({iso.group(1)})" if iso else f"- {deadline}")
    return "- [ ] " + " ".join(parts)


def _meta_str(meta: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _duration_label(meta: Mapping[str, Any]) -> str:
    seconds = meta.get("duration_sec")
    if not isinstance(seconds, (int, float)) or seconds <= 0:
        return ""
    minutes = int(round(seconds / 60))
    if minutes < 1:
        return "< 1 min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d}" if hours else f"{minutes} min"


def meeting_note_title(record: Mapping[str, Any]) -> str:
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    title = _meta_str(meta, "title") or "Meeting"
    date = _meta_str(meta, "started_at", "date", "created_at")[:10]
    return f"{date} {title}".strip() if date and not title.startswith(date) else title


def meeting_note_body(record: Mapping[str, Any], *, include_transcript: bool = False) -> str:
    """Markdown for the note: facts header, summary, checkable actions,
    live notes and (optionally) the transcript."""
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    summary = str(record.get("summary") or "").strip()
    notes = str(record.get("notes") or "").strip()
    transcript = str(record.get("transcript") or "").strip()

    facts: list[str] = []
    when = _meta_str(meta, "started_at", "date", "created_at")
    if when:
        facts.append(f"- Date: {when[:16].replace('T', ' ')}")
    duration = _duration_label(meta)
    if duration:
        facts.append(f"- Duration: {duration}")
    speakers = meta.get("speakers")
    if isinstance(speakers, list):
        names = [str(name).strip() for name in speakers if str(name).strip()]
        if names:
            facts.append("- Participants: " + ", ".join(names))
    url = _meta_str(meta, "conference_url")
    if url:
        facts.append(f"- Link: {url}")

    out: list[str] = []
    if facts:
        out += facts
        out.append("")

    items = action_items_from_markdown(summary)
    if items:
        out.append("## Action items")
        out += [task_line(item) for item in items]
        out.append("")

    if summary:
        out.append("## Minutes")
        out.append(summary)
        out.append("")
    if notes:
        out.append("## Notes taken during the meeting")
        out.append(notes)
        out.append("")
    if include_transcript and transcript:
        out.append("## Transcript")
        out.append(transcript)
        out.append("")
    if not (summary or notes or (include_transcript and transcript)):
        out.append("_No summary or notes yet._")
    return "\n".join(out).rstrip() + "\n"


def find_meeting_note(root: Path, meeting_id: str) -> dict[str, Any] | None:
    """The note previously saved for this meeting, matched on ``props``."""
    from navin.notes.store import scan_notes

    for info in scan_notes(root):
        props = info.get("props") if isinstance(info.get("props"), Mapping) else {}
        if str(props.get(MEETING_PROP) or "") == meeting_id:
            return info
    return None


def save_meeting_to_note(
    meeting_id: str,
    *,
    include_transcript: bool = False,
    folder: str | None = None,
    store: MeetingStore | None = None,
    notes_root: Path | None = None,
    actor: str = "local",
) -> dict[str, Any]:
    """Create or refresh the note for *meeting_id* and link it both ways."""
    from navin.notes.store import create_note, update_note
    from navin.notes.store import notes_root as default_notes_root

    meeting_store = store or default_meeting_store()
    root = notes_root or default_notes_root()
    record = meeting_store.get(meeting_id)
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}

    title = meeting_note_title(record)
    body = meeting_note_body(record, include_transcript=include_transcript)
    props = {
        MEETING_PROP: record["id"],
        "meeting_date": _meta_str(meta, "started_at", "date", "created_at")[:10],
        "source": "meetings",
    }

    existing = find_meeting_note(root, record["id"])
    created = existing is None
    if existing is None:
        page = create_note(
            root, title=title, folder=folder if folder is not None else NOTES_FOLDER, markdown=body
        )
        note_id = str(page["note"]["id"])
        page = update_note(root, note_id, tags=[NOTE_TAG], props=props)
    else:
        note_id = str(existing["id"])
        tags = list(existing.get("tags") or [])
        if NOTE_TAG not in tags:
            tags.append(NOTE_TAG)
        # The user may have renamed or moved the note: only the body is
        # refreshed (the Notes history keeps the previous version).
        page = update_note(root, note_id, markdown=body, tags=tags, props=props)

    note = page["note"]
    if str(meta.get("note_id") or "") != note_id:
        try:
            meeting_store.update(
                record["id"], {"meta": {"note_id": note_id}}, audit_actor=actor
            )
        except MeetingStoreError:
            pass
    meeting_store.append_audit(
        record["id"],
        "meeting.note_saved",
        actor=actor,
        details={"note_id": note_id, "created": created},
    )
    return {
        "note_id": note_id,
        "title": note["title"],
        "path": note["path"],
        "folder": note["folder"],
        "created": created,
        "action_items": len(action_items_from_markdown(str(record.get("summary") or ""))),
        "meeting_id": record["id"],
    }


__all__ = [
    "MEETING_PROP",
    "NOTES_FOLDER",
    "NOTE_TAG",
    "action_items_from_markdown",
    "find_meeting_note",
    "meeting_note_body",
    "meeting_note_title",
    "save_meeting_to_note",
    "task_line",
]
