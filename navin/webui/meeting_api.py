"""In-app meeting synthesis: minutes and grounded answers, without the chat.

The Meeting desk used to seed a prompt into the agent composer for every
summary. That routes a document task through a coding agent with tools, which
is slow, expensive, and free to wander off. These are bounded, tool-less model
calls that return Markdown straight to the desk. Long reports extract source
evidence from every segment before synthesis; rendering and export stay local.

Route preference follows the ``/meeting`` command role (``docs``), then
``dev``, then the default agent model.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

from navin.agent.skill_routing import ActionSkillContext, build_action_skill_context

# Reports above the single-call input budget use bounded evidence extraction.
_MAX_TRANSCRIPT_CHARS = 80000
_MAX_NOTES_CHARS = 6000
_MAX_QUESTION_CHARS = 1000
_MAX_REPORT_TOKENS = 4000
_REPORT_SEGMENT_CHARS = 40000
_MAX_REPORT_SEGMENTS = 8
_REPORT_EXTRACT_TOKENS = 2200
_MAX_REPORT_EXTRACT_CHARS = 8000
_REPORT_SYNTHESIS_RESERVE_S = 45.0
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

_REPORT_EXTRACT_SYSTEM = (
    "Extract evidence from this one transcript segment for a later meeting report. "
    "Do not write the final report. Preserve every decision, action, owner, deadline, "
    "proposal, disagreement and open question stated in the segment. Keep enough topic "
    "excerpts to explain the discussion. Do not infer a decision from a proposal. "
    "Return JSON only: {\"complete\": true, \"items\": [{\"kind\": "
    "\"decision|action|proposal|opinion|question|topic\", \"quote\": \"exact transcript excerpt\"}]}. "
    "Every quote must occur verbatim in this segment, including stated labels and timecodes "
    "when relevant. Each quote must be at most 1000 characters. Do not paraphrase quotes or "
    "invent labels. Mark complete false if you cannot capture all consequential facts within "
    "the output budget. An empty items list is valid only when there is nothing substantive."
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
        finish_reason = str(getattr(response, "finish_reason", "") or "").lower()
        if finish_reason in {"length", "max_tokens"}:
            raise MeetingError(
                "the model output was truncated; reduce the input or use smaller sections",
                status=502,
            )
        if finish_reason in {"error", "content_filter"}:
            raise MeetingError("the model did not produce a complete usable answer", status=502)
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
    except MeetingError:
        raise
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
    clean_notes = notes.strip()
    if len(clean_notes) > _MAX_NOTES_CHARS:
        raise MeetingError(f"meeting notes exceed {_MAX_NOTES_CHARS} characters; split the notes first", status=413)
    if clean_notes:
        parts += ["", "## Notes taken during the meeting", clean_notes]
    clean_transcript = transcript.strip()
    if len(clean_transcript) > _MAX_TRANSCRIPT_CHARS:
        raise MeetingError("the report input requires segmented analysis", status=413)
    parts += ["", "## Transcript", clean_transcript or "(no transcript)"]
    return "\n".join(parts)


def _report_segments(transcript: str) -> list[dict[str, Any]]:
    """Partition all characters, preferring speaker/sentence boundaries."""
    if len(transcript) > _REPORT_SEGMENT_CHARS * _MAX_REPORT_SEGMENTS:
        raise MeetingError(
            f"transcript exceeds the report limit of {_REPORT_SEGMENT_CHARS * _MAX_REPORT_SEGMENTS} "
            "characters; split the meeting into shorter reports. No report was generated.",
            status=413,
        )
    segments = []
    start = 0
    while start < len(transcript):
        end = min(start + _REPORT_SEGMENT_CHARS, len(transcript))
        if end < len(transcript):
            # Leave enough capacity for the rest even near the total limit.
            remaining_slots = _MAX_REPORT_SEGMENTS - len(segments) - 1
            boundary_start = max(
                start + _REPORT_SEGMENT_CHARS * 3 // 4,
                len(transcript) - remaining_slots * _REPORT_SEGMENT_CHARS - 1,
            )
            boundary = transcript.rfind("\n", boundary_start, end)
            if boundary < 0:
                boundary = transcript.rfind(". ", boundary_start, end)
            if boundary >= 0:
                end = boundary + 1
        segments.append({"start_char": start, "end_char": end, "text": transcript[start:end]})
        start = end
    if len(segments) > _MAX_REPORT_SEGMENTS:
        raise MeetingError(
            f"transcript requires more than {_MAX_REPORT_SEGMENTS} report segments; "
            "split the meeting into shorter reports. No report was generated.", status=413,
        )
    return segments


def _report_evidence(text: str, segment: str) -> tuple[list[dict[str, str]], bool]:
    """Only literal source excerpts can reach the final synthesis."""
    if len(text) > _MAX_REPORT_EXTRACT_CHARS:
        raise MeetingError("segment evidence exceeds the synthesis budget", status=502)
    try:
        data = json.loads(strip_report_fences(text))
    except (TypeError, ValueError) as exc:
        raise MeetingError("segment evidence was not valid JSON", status=502) from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise MeetingError("segment evidence has no valid items list", status=502)
    source = " ".join(segment.split())
    complete = data.get("complete") is True
    items = []
    for row in data["items"]:
        if not isinstance(row, dict):
            complete = False
            continue
        quote = row.get("quote")
        kind = row.get("kind")
        if (
            not isinstance(kind, str)
            or kind not in {"decision", "action", "proposal", "opinion", "question", "topic"}
            or not isinstance(quote, str) or not quote.strip() or len(quote) > 1000
            or " ".join(quote.split()) not in source
        ):
            complete = False
            continue
        items.append({"kind": kind, "quote": quote.strip()})
    return items, complete


async def _segmented_report(
    transcript: str,
    prompt_options: dict[str, Any],
    skills: ActionSkillContext,
) -> dict[str, Any]:
    segments = _report_segments(transcript)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _REPORT_TIMEOUT_S
    extraction_deadline = deadline - _REPORT_SYNTHESIS_RESERVE_S
    semaphore = asyncio.Semaphore(2)

    async def extract(index: int, segment: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in segment.items() if key != "text"}
        result.update({"index": index + 1, "status": "failed", "items": []})
        async with semaphore:
            remaining = extraction_deadline - loop.time()
            if remaining <= 0:
                result["error"] = "segment analysis time budget exhausted"
                return result
            user = (
                f"Segment {index + 1}/{len(segments)}; original character interval "
                f"[{segment['start_char']}, {segment['end_char']}).\n"
                f"Requested report outline: {prompt_options['template_instructions']}\n"
                f"<transcript_segment>\n{segment['text']}\n</transcript_segment>"
            )
            try:
                text, model, route = await asyncio.wait_for(
                    _ask(
                        skills.augment_system(_REPORT_EXTRACT_SYSTEM), user,
                        max_tokens=_REPORT_EXTRACT_TOKENS, timeout_s=min(45.0, remaining),
                    ), timeout=remaining,
                )
                items, complete = _report_evidence(text, segment["text"])
                result.update({"status": "complete" if complete else "partial", "items": items, "model": model, "route": route})
                if not complete:
                    result["error"] = "segment coverage unconfirmed or unsupported excerpts rejected"
            except (MeetingError, TimeoutError) as exc:
                result["error"] = str(exc) or "segment analysis timed out"
        return result

    results = await asyncio.gather(*(extract(index, segment) for index, segment in enumerate(segments)))
    processed = [row for row in results if row["status"] != "failed"]
    if not processed:
        raise MeetingError(
            "no transcript segment could be analyzed; no report was generated. " + str(results[0].get("error") or ""),
            status=502,
        )
    evidence = []
    for row in processed:
        label = f"Segment {row['index']}/{len(segments)} [{row['start_char']}, {row['end_char']})"
        excerpts = "\n".join(f"- {item['kind']}: {json.dumps(item['quote'], ensure_ascii=False)}" for item in row["items"])
        evidence.append(f"{label}\n{excerpts or '(no substantive evidence extracted)'}")
    material = "\n\n".join(evidence)
    models = list(dict.fromkeys(row["model"] for row in processed if row.get("model")))
    routes = list(dict.fromkeys(row["route"] for row in processed if row.get("route")))
    missing = [row["index"] for row in results if row["status"] != "complete"]
    synthesis_error = ""
    markdown = ""
    try:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise MeetingError("final synthesis time budget exhausted", status=504)
        user = _report_prompt(transcript=material, **prompt_options)
        user += (
            "\n\nThe transcript block contains source-validated excerpts from the original segments. "
            "Every quoted decision and action must be represented in the final report. "
            "Use the supplied timecodes, never character offsets as timecodes. "
            f"Segments without confirmed full coverage: {missing or 'none'}. "
            "Do not infer anything about unavailable content or claim full coverage when a segment is incomplete."
        )
        text, model, route = await asyncio.wait_for(
            _ask(
                skills.augment_system(_REPORT_SYSTEM), user,
                max_tokens=_MAX_REPORT_TOKENS, timeout_s=remaining,
            ), timeout=remaining,
        )
        markdown = strip_report_fences(text)
        if not markdown:
            raise MeetingError("the model returned an empty synthesis", status=502)
        if model and model not in models:
            models.append(model)
        if route and route not in routes:
            routes.append(route)
    except (MeetingError, TimeoutError) as exc:
        synthesis_error = str(exc) or "final synthesis timed out"
    complete = not missing and not synthesis_error
    fr = str(prompt_options.get("language") or "").lower().startswith("fr")
    if not markdown:
        heading = "## Extraits sources disponibles" if fr else "## Available source excerpts"
        markdown = heading + "\n\n" + "\n".join("    " + line for line in material.splitlines())
    if not complete:
        warning = (
            f"Compte rendu partiel: {len(processed)}/{len(segments)} segments analysés. "
            f"Couverture incomplète des segments: {', '.join(map(str, missing)) or 'aucun'}. "
            "Relancer le traitement avant de considérer ce compte rendu comme complet."
            if fr else
            f"Partial meeting report: {len(processed)}/{len(segments)} segments analyzed. "
            f"Incomplete segment coverage: {', '.join(map(str, missing)) or 'none'}. "
            "Rerun processing before treating this report as complete."
        )
        if synthesis_error:
            warning += " Synthèse finale indisponible; extraits conservés." if fr else " Final synthesis unavailable; source excerpts retained."
        markdown = f"> **{warning}**\n\n{markdown}"
    return {
        "markdown": markdown, "model": ", ".join(models), "route": ", ".join(routes),
        "coverage": {
            "mode": "segmented", "complete": complete, "input_chars": len(transcript),
            "processed_chars": sum(row["end_char"] - row["start_char"] for row in processed),
            "segment_count": len(segments), "processed_segments": len(processed),
            "segments": [{key: value for key, value in row.items() if key not in {"items", "model", "route"}} for row in results],
            "synthesis_complete": not synthesis_error, "synthesis_error": synthesis_error,
        },
    }


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

    prompt_options = dict(
        title=title,
        template_name=template_name,
        template_instructions=template_instructions,
        notes=notes,
        speakers=speakers,
        language=language,
        meeting_date=meeting_date,
        duration_min=duration_min,
    )
    started = time.perf_counter()
    skills = await asyncio.to_thread(build_action_skill_context, "meeting", "report")
    clean_transcript = transcript.strip()
    if len(clean_transcript) > _MAX_TRANSCRIPT_CHARS:
        # Validate ancillary input before spending any provider calls.
        _report_prompt(transcript="", **prompt_options)
        result = await _segmented_report(clean_transcript, prompt_options, skills)
        result.update({"skill_context": skills.metadata, "latency_ms": int((time.perf_counter() - started) * 1000)})
        return result
    user = _report_prompt(transcript=clean_transcript, **prompt_options)
    text, model, route = await _ask(
        skills.augment_system(_REPORT_SYSTEM),
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
        "skill_context": skills.metadata,
        "coverage": {"mode": "single", "complete": True, "input_chars": len(clean_transcript), "processed_chars": len(clean_transcript)},
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
    skills = await asyncio.to_thread(build_action_skill_context, "meeting", "speakers")

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
            skills.augment_system(_SPEAKER_SYSTEM),
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
        "skill_context": skills.metadata,
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
    skills = await asyncio.to_thread(build_action_skill_context, "meeting", "answer")
    text, model, route = await _ask(
        skills.augment_system(_ANSWER_SYSTEM),
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
        "skill_context": skills.metadata,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }
