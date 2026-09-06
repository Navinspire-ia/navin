"""In-app meeting synthesis: minutes and grounded answers, without the chat.

The Meeting desk used to seed a prompt into the agent composer for every
summary. That routes a document task through a coding agent with tools, which
is slow, expensive, and free to wander off. These are single, tool-less model
calls that return Markdown straight to the desk, so the report renders and
exports locally.

Route preference follows the ``/meeting`` command role (``docs``), then
``dev``, then the default agent model.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Sequence
from typing import Any

# A one-hour meeting is roughly 55k chars; clipping at 24k silently dropped
# half of it from the minutes. Modern default models take 20k+ tokens easily.
_MAX_TRANSCRIPT_CHARS = 80000
_MAX_NOTES_CHARS = 6000
_MAX_QUESTION_CHARS = 1000
_MAX_REPORT_TOKENS = 4000
_MAX_ANSWER_TOKENS = 900
# Diarization rewrites the text, so the answer is as long as the excerpt.
_SPEAKER_CHUNK_CHARS = 6000
_MAX_SPEAKER_TOKENS = 3500
_MAX_SPEAKER_CHUNKS = 12
_SPEAKER_TIMEOUT_S = 120.0
# Stay under the WebUI slow-request budget so the server error wins the race
# and the desk shows a real message instead of a client abort.
_REPORT_TIMEOUT_S = 150.0
_ANSWER_TIMEOUT_S = 90.0

_REPORT_SYSTEM = (
    "You write meeting minutes. You are given a raw transcript, optional notes, "
    "and a template outline.\n"
    "Rules:\n"
    "- Output GitHub-flavoured Markdown only. No preamble, no closing remark, "
    "no code fences around the whole answer.\n"
    "- Start at heading level 2. Never emit a level 1 heading: the document "
    "already has a title.\n"
    "- Follow the template outline section by section, in its order.\n"
    "- Ground every statement in the transcript or the notes. Never invent "
    "attendees, decisions, figures, or dates.\n"
    "- Write a section as '_Not discussed._' when the material does not "
    "support it. That is the expected answer, not a failure.\n"
    "- Render actions as a table with the columns Owner, Action, Deadline. "
    "Use 'unassigned' and 'no deadline' when they were not stated.\n"
    "- Attribute speech only to speakers that are explicitly named or "
    "labelled. Otherwise write 'Speaker 1', 'Speaker 2'.\n"
    "- Transcript lines may start with a timecode like [03:15] or [1:02:40]. "
    "Use them to keep the discussion in chronological order and cite the "
    "timecode of key moments, for example 'Decided at (12:40)'. Never invent "
    "a timecode.\n"
    "- Distinguish what was DECIDED from what was only PROPOSED and from "
    "personal OPINIONS. When the template has sections for proposals or "
    "positions, attribute each one to its speaker.\n"
    "- When the transcript carries no substantive content, say so in one "
    "sentence under the summary section and mark the rest as not discussed. "
    "Do not pad.\n"
    "- Use plain hyphens '-' for dashes; never the en dash or em dash "
    "characters.\n"
    "- Keep it dense: no filler, no restating the instructions."
)

_SPEAKER_SYSTEM = (
    "You split a raw meeting transcript into speaker turns.\n"
    "Rules:\n"
    "- Output the transcript only. One turn per line, each line starting with "
    "a label then ': ', for example 'Speaker 1: ' or 'Ana Ruiz: '.\n"
    "- A line may start with a timecode like [03:15] or [1:02:40]. Keep it "
    "exactly as written, at the start of the line, before the label: "
    "'[03:15] Speaker 1: ...'. When you split one timecoded block into "
    "several turns, only the first turn keeps the timecode.\n"
    "- Keep the spoken words byte-identical. Do not translate, summarize, "
    "reorder, fix grammar, or drop filler.\n"
    "- Use a real name ONLY when this transcript states it: someone introduces "
    "themselves, signs off, or is addressed by name. Otherwise keep the "
    "numbered label. Never guess a name from role, topic, or context.\n"
    "- Keep the same person on the same label for the whole excerpt.\n"
    "- When you cannot tell that the speaker changed, keep the previous label "
    "rather than inventing a new one.\n"
    "- No preamble, no commentary, no code fences, no headings."
)

_ANSWER_SYSTEM = (
    "You answer questions about one meeting, from its transcript and notes "
    "only.\n"
    "Rules:\n"
    "- Output short Markdown. No preamble, no heading, no code fences around "
    "the whole answer.\n"
    "- Answer only from the material given. When it does not contain the "
    "answer, say so in one sentence and stop.\n"
    "- Quote the transcript when a quote settles the question, with '>'.\n"
    "- Never speculate about what participants meant or intended."
)


class MeetingError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _meeting_preset() -> str | None:
    """Preset for meeting synthesis: docs (the /meeting role) → dev → default."""
    from navin.agent.model_routes import resolve_model_route

    return resolve_model_route("docs") or resolve_model_route("dev")


def _load_snapshot(preset_name: str | None) -> Any:
    from navin.providers.factory import load_provider_snapshot

    return load_provider_snapshot(preset_name=preset_name)


async def _ask(
    system: str,
    user: str,
    *,
    max_tokens: int,
    timeout_s: float,
) -> tuple[str, str, str]:
    """One non-streaming, tool-less model call. Returns (text, model, route)."""
    preset = _meeting_preset()
    try:
        snapshot = await asyncio.to_thread(_load_snapshot, preset)
    except Exception as exc:
        raise MeetingError(f"no model configured: {exc}", status=503) from exc

    async def _once() -> str:
        response = await asyncio.wait_for(
            snapshot.provider.chat_with_retry(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=snapshot.model,
                max_tokens=max_tokens,
                temperature=0.2,
            ),
            timeout=timeout_s,
        )
        return response.content or ""

    try:
        text = await _once()
        # Providers occasionally answer with empty content and no error (seen
        # with fast models under load). One retry turns a user-facing failure
        # into a short delay; a second empty answer is a real error upstream.
        if not text.strip():
            text = await _once()
    except TimeoutError as exc:
        raise MeetingError("the model did not answer in time", status=504) from exc
    except Exception as exc:
        raise MeetingError(f"model call failed: {exc}", status=502) from exc

    return text, str(snapshot.model or ""), (preset or "default")


STORE_MODES = frozenset(
    {
        "store_create",
        "store_get",
        "store_update",
        "store_delete",
        "store_list",
        "migrate",
        "search",
        "audio",
        "audit",
        "templates",
        "calendar",
        "emergency_export",
        "to_note",
    }
)


def store_payload(mode: str, body: Mapping[str, Any]) -> dict[str, Any]:
    """Run one disk-store request. Synchronous: call it from a worker thread.

    Everything here touches the filesystem (a search re-reads every record, an
    emergency export zips the audio directory, an audio save decodes base64
    and fsyncs). Running it inline on the gateway event loop froze every other
    session for the duration, so ``ws_http`` hands it to ``asyncio.to_thread``.
    """
    import base64
    import mimetypes

    from navin.meetings.store import MeetingStoreError, default_meeting_store

    store = default_meeting_store()
    meeting_id = str(body.get("id") or body.get("meeting_id") or "")
    try:
        if mode == "store_create":
            return store.create(body.get("record") or body)
        if mode == "store_get":
            return store.get(meeting_id)
        if mode == "store_update":
            return store.update(meeting_id, body.get("changes") or body)
        if mode == "store_delete":
            store.delete(meeting_id)
            return {"ok": True, "id": meeting_id}
        if mode == "store_list":
            limit = int(body.get("limit") or 100)
            offset = int(body.get("offset") or 0)
            rows = store.list(limit=limit, offset=offset)
            payload: dict[str, Any] = {
                "meetings": rows,
                "offset": offset,
                "limit": limit,
                "total": store.count(),
            }
            if body.get("full"):
                # One round trip per page instead of one per meeting: the desk
                # used to issue N `store_get` calls at start-up.
                payload["records"] = [store.get(str(row["id"])) for row in rows]
            return payload
        if mode == "search":
            return {
                "meetings": store.search(
                    str(body.get("query") or ""),
                    limit=int(body.get("limit") or 50),
                )
            }
        if mode == "migrate":
            return store.migrate_local_storage(
                body.get("payload") or body,
                migration_id=str(body.get("migration_id") or "local-storage-v1"),
                version=int(body.get("version") or 1),
            )
        if mode == "audio":
            audio_action = str(body.get("action") or "save")
            if audio_action == "list":
                return {"segments": store.list_audio_segments(meeting_id)}
            if audio_action == "get":
                metadata, data = store.read_audio_segment(
                    meeting_id, str(body.get("segment_id") or "")
                )
                return {
                    "filename": str(metadata.get("file") or "audio.bin"),
                    "content_type": str(
                        metadata.get("mime")
                        or mimetypes.guess_type(str(metadata.get("file") or ""))[0]
                        or "application/octet-stream"
                    ),
                    "data_base64": base64.b64encode(data).decode("ascii"),
                    "bytes": len(data),
                }
            return store.save_audio_data_url(
                meeting_id,
                str(body.get("data_url") or ""),
                metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
            )
        if mode == "audit":
            action = str(body.get("action") or "")
            if action:
                return store.append_audit(
                    meeting_id,
                    action,
                    actor=str(body.get("actor") or "local"),
                    details=body.get("details"),
                )
            return {
                "events": store.read_audit(
                    meeting_id=meeting_id or None,
                    limit=int(body.get("limit") or 500),
                )
            }
        if mode == "templates":
            template_id = str(body.get("template_id") or "")
            if body.get("delete"):
                store.delete_template(template_id)
                return {"ok": True}
            if isinstance(body.get("template"), dict):
                return store.put_template(template_id, body["template"])
            return {"templates": store.list_templates()}
        if mode == "calendar":
            if isinstance(body.get("calendar"), dict):
                return store.set_calendar(body["calendar"])
            return store.get_calendar()
        if mode == "emergency_export":
            data = store.emergency_export()
            return {
                "filename": "navin-meetings-emergency.zip",
                "content_type": "application/zip",
                "data_base64": base64.b64encode(data).decode("ascii"),
                "bytes": len(data),
            }
        if mode == "to_note":
            from navin.meetings.notes_bridge import save_meeting_to_note
            from navin.notes.store import NotesError

            try:
                return save_meeting_to_note(
                    meeting_id,
                    include_transcript=bool(body.get("include_transcript")),
                    store=store,
                )
            except NotesError as exc:
                raise MeetingError(exc.message, status=exc.status) from exc
    except MeetingStoreError as exc:
        raise MeetingError(str(exc), status=404 if "not found" in str(exc) else 400) from exc
    except (TypeError, ValueError) as exc:
        raise MeetingError(f"invalid meeting payload: {exc}") from exc
    raise MeetingError(f"unknown meeting mode: {mode}")


def _clip(text: str, limit: int) -> str:
    """Keep the head and the tail of an over-long block, marking the cut."""
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    head = body[: int(limit * 0.7)].rstrip()
    tail = body[-int(limit * 0.25) :].lstrip()
    return f"{head}\n\n[... transcript truncated for length ...]\n\n{tail}"


def strip_report_fences(text: str) -> str:
    """Drop a fence wrapping the whole answer and any stray H1."""
    body = (text or "").strip()
    if body.startswith("```"):
        lines = body.splitlines()
        if len(lines) >= 2:
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            body = "\n".join(lines).strip()
    out: list[str] = []
    for line in body.splitlines():
        if line.startswith("# ") and not out:
            continue
        out.append(line)
    return "\n".join(out).strip()


def _speaker_line(speakers: Sequence[str] | None) -> str:
    names = [str(s).strip() for s in (speakers or []) if str(s).strip()]
    return ", ".join(names) if names else "not labelled"


def _report_prompt(
    *,
    title: str,
    template_name: str,
    template_instructions: str,
    transcript: str,
    notes: str,
    speakers: Sequence[str] | None,
    language: str,
    meeting_date: str = "",
    duration_min: int | None = None,
) -> str:
    parts = [
        f"Meeting title: {title.strip() or 'Untitled meeting'}",
        f"Template: {template_name.strip() or 'Standard minutes'}",
        f"Template outline: {template_instructions.strip()}",
        f"Known speakers: {_speaker_line(speakers)}",
    ]
    if meeting_date.strip():
        parts.append(
            f"Meeting date and time (ISO, authoritative): {meeting_date.strip()}"
        )
    if duration_min and duration_min > 0:
        parts.append(f"Measured audio duration: about {duration_min} minutes")
    if language.strip():
        parts.append(
            f"Write in {language.strip()} unless the transcript is clearly in "
            "another language, in which case use the transcript language."
        )
    clean_notes = _clip(notes, _MAX_NOTES_CHARS)
    if clean_notes:
        parts += ["", "## Notes taken during the meeting", clean_notes]
    clean_transcript = _clip(transcript, _MAX_TRANSCRIPT_CHARS)
    parts += ["", "## Transcript", clean_transcript or "(no transcript)"]
    return "\n".join(parts)


async def report_payload(
    *,
    title: str = "",
    template_name: str = "",
    template_instructions: str = "",
    transcript: str = "",
    notes: str = "",
    speakers: Sequence[str] | None = None,
    language: str = "",
    meeting_date: str = "",
    duration_min: int | None = None,
) -> dict[str, Any]:
    """Template-driven minutes for one meeting, as Markdown."""
    if not transcript.strip() and not notes.strip():
        raise MeetingError("add a transcript or notes first")

    user = _report_prompt(
        title=title,
        template_name=template_name,
        template_instructions=template_instructions,
        transcript=transcript,
        notes=notes,
        speakers=speakers,
        language=language,
        meeting_date=meeting_date,
        duration_min=duration_min,
    )
    started = time.perf_counter()
    text, model, route = await _ask(
        _REPORT_SYSTEM,
        user,
        max_tokens=_MAX_REPORT_TOKENS,
        timeout_s=_REPORT_TIMEOUT_S,
    )
    markdown = strip_report_fences(text)
    if not markdown:
        raise MeetingError("the model returned an empty report", status=502)
    return {
        "markdown": markdown,
        "model": model,
        "route": route,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


def split_for_diarization(transcript: str, chunk_chars: int = _SPEAKER_CHUNK_CHARS) -> list[str]:
    """Cut a transcript on line, then sentence, boundaries under *chunk_chars*."""
    body = (transcript or "").strip()
    if not body:
        return []
    units: list[str] = []
    for line in body.splitlines():
        row = line.strip()
        if not row:
            continue
        if len(row) <= chunk_chars:
            units.append(row)
            continue
        current = ""
        for piece in re.split(r"(?<=[.!?…])\s+", row):
            if len(current) + len(piece) + 1 > chunk_chars and current:
                units.append(current.strip())
                current = piece
            else:
                current = f"{current} {piece}".strip()
        if current.strip():
            units.append(current.strip())

    chunks: list[str] = []
    buffer = ""
    for unit in units:
        if len(buffer) + len(unit) + 1 > chunk_chars and buffer:
            chunks.append(buffer.strip())
            buffer = unit
        else:
            buffer = f"{buffer}\n{unit}".strip()
    if buffer.strip():
        chunks.append(buffer.strip())
    return chunks


def roster_from_labels(text: str) -> list[str]:
    """Speaker labels found at the start of turns, in order of appearance."""
    out: list[str] = []
    for line in (text or "").splitlines():
        # A capture timecode like [03:15] may precede the label; skip it so it
        # is never mistaken for a speaker name.
        line = re.sub(r"^\s*\[\d{1,2}:\d{2}(?::\d{2})?\]\s*", "", line)
        match = re.match(r"^\s*([^:\n]{1,40}?):\s+\S", line)
        if not match:
            continue
        name = match.group(1).strip()
        if not name or len(name.split()) > 4:
            continue
        if name not in out:
            out.append(name)
    return out


async def speakers_payload(
    *,
    transcript: str = "",
    speakers: Sequence[str] | None = None,
    language: str = "",
) -> dict[str, Any]:
    """Label speaker turns across the whole transcript, chunk by chunk.

    Chunks are processed in order and each one is told which labels the
    previous chunks used, so the same person keeps the same label from start to
    end. Names are only adopted when the transcript states them.
    """
    if not transcript.strip():
        raise MeetingError("add a transcript first")

    chunks = split_for_diarization(transcript)
    if not chunks:
        raise MeetingError("add a transcript first")
    truncated = len(chunks) > _MAX_SPEAKER_CHUNKS
    chunks = chunks[:_MAX_SPEAKER_CHUNKS]

    known = [str(name).strip() for name in (speakers or []) if str(name).strip()]
    labelled: list[str] = []
    model = ""
    route = ""
    started = time.perf_counter()

    for index, chunk in enumerate(chunks):
        parts = []
        if known:
            parts.append(
                "Labels already used earlier in this meeting, reuse them for the "
                f"same people: {', '.join(known)}."
            )
        if language.strip():
            parts.append(
                "The transcript language wins; never translate it to "
                f"{language.strip()}."
            )
        if index:
            parts.append("This is a continuation: the first line may finish a turn.")
        parts += ["", "Transcript excerpt:", chunk]
        text, model, route = await _ask(
            _SPEAKER_SYSTEM,
            "\n".join(parts),
            max_tokens=_MAX_SPEAKER_TOKENS,
            timeout_s=_SPEAKER_TIMEOUT_S,
        )
        piece = strip_report_fences(text).strip()
        if not piece:
            raise MeetingError("the model returned no labelled transcript", status=502)
        labelled.append(piece)
        for name in roster_from_labels(piece):
            if name not in known:
                known.append(name)

    body = "\n".join(labelled).strip()
    if truncated:
        remaining = "\n".join(split_for_diarization(transcript)[_MAX_SPEAKER_CHUNKS:])
        body = f"{body}\n{remaining}".strip()

    return {
        "markdown": body,
        "speakers": known,
        "truncated": truncated,
        "model": model,
        "route": route,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }


async def answer_payload(
    *,
    question: str,
    title: str = "",
    transcript: str = "",
    notes: str = "",
    summary: str = "",
    speakers: Sequence[str] | None = None,
    language: str = "",
) -> dict[str, Any]:
    """Grounded answer about one meeting, as Markdown."""
    clean_question = (question or "").strip()
    if not clean_question:
        raise MeetingError("missing question")
    if len(clean_question) > _MAX_QUESTION_CHARS:
        raise MeetingError("question is too long")
    if not transcript.strip() and not notes.strip():
        raise MeetingError("add a transcript or notes first")

    parts = [
        f"Meeting title: {title.strip() or 'Untitled meeting'}",
        f"Known speakers: {_speaker_line(speakers)}",
    ]
    if language.strip():
        parts.append(f"Answer in {language.strip()}.")
    clean_summary = _clip(summary, _MAX_NOTES_CHARS)
    if clean_summary:
        parts += ["", "## Existing summary", clean_summary]
    clean_notes = _clip(notes, _MAX_NOTES_CHARS)
    if clean_notes:
        parts += ["", "## Notes", clean_notes]
    parts += ["", "## Transcript", _clip(transcript, _MAX_TRANSCRIPT_CHARS)]
    parts += ["", f"Question: {clean_question}"]

    started = time.perf_counter()
    text, model, route = await _ask(
        _ANSWER_SYSTEM,
        "\n".join(parts),
        max_tokens=_MAX_ANSWER_TOKENS,
        timeout_s=_ANSWER_TIMEOUT_S,
    )
    answer = strip_report_fences(text)
    if not answer:
        raise MeetingError("the model returned an empty answer", status=502)
    return {
        "markdown": answer,
        "model": model,
        "route": route,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
