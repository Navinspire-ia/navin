"""HTTP API for the Notes module.

Thin request/response layer over :mod:`navin.notes.store`. All routes live
under ``/api/notes/`` and are dispatched from ``ws_http.py`` with a single
call, so the giant dispatcher there stays readable.

Transport notes: the gateway's HTTP layer only accepts GET, so note markdown
and attachment bytes arrive as chunked base64 request headers (the same
mechanism as the Code module's file-save), reassembled by
``file_body_from_headers``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from websockets.http11 import Response

from navin.notes import store
from navin.notes.store import NotesError, notes_root
from navin.webui.file_preview import WebUIFilePreviewError, file_body_from_headers
from navin.webui.http_utils import (
    http_error,
    http_json_response,
    http_response,
    parse_query,
    query_first,
)

NOTES_API_PREFIX = "/api/notes/"


def _flag(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _optional_flag(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return _flag(value)


def _optional_body(request: Any) -> str | None:
    """Markdown from chunked base64 headers; None when the request has none."""
    try:
        return file_body_from_headers(request.headers)
    except WebUIFilePreviewError:
        return None


async def dispatch_notes_route(
    got: str,
    request: Any,
    *,
    check_token: Callable[[Any], bool],
) -> Response | None:
    """Handle a ``/api/notes/*`` route; None when the path is not ours."""
    if not got.startswith(NOTES_API_PREFIX):
        return None
    if not check_token(request):
        return http_error(401, "Unauthorized")
    query = parse_query(request.path)
    root = notes_root()
    action = got[len(NOTES_API_PREFIX) :]
    try:
        if action == "ask":
            # Semantic memory: best passages across all notes, lexical fallback.
            from navin.notes import memory

            return http_json_response(
                await memory.ask_notes(
                    root,
                    query_first(query, "q") or "",
                    limit=_int_or_none(query_first(query, "limit")) or 8,
                )
            )
        # Every other route is disk work (a vault import or a graph rebuild
        # walks the whole tree); on a worker thread it stops stalling the
        # event loop that streams every other session's chat.
        return await asyncio.to_thread(_route, action, request, query, root)
    except NotesError as exc:
        return http_error(exc.status, exc.message)
    except OSError:
        return http_error(500, "notes storage error")


def _route(action: str, request: Any, query: Any, root: Any) -> Response:
    q = lambda key: query_first(query, key)  # noqa: E731

    if action == "list":
        return http_json_response(
            store.list_notes(
                root,
                folder=q("folder"),
                tag=q("tag"),
                query=q("q"),
                sort=q("sort") or "updated_desc",
                archived=_flag(q("archived")),
                limit=_int_or_none(q("limit")),
                cursor=q("cursor"),
            )
        )
    if action == "get":
        return http_json_response(store.get_note(root, q("id") or ""))
    if action == "create":
        return http_json_response(
            store.create_note(
                root,
                title=q("title") or "",
                folder=q("folder") or "",
                markdown=_optional_body(request) or "",
                template=q("template"),
            )
        )
    if action == "update":
        tags = _json_list(q("tags"))
        return http_json_response(
            store.update_note(
                root,
                q("id") or "",
                markdown=_optional_body(request),
                title=q("title"),
                tags=tags,
                pinned=_optional_flag(q("pinned")),
                archived=_optional_flag(q("archived")),
                folder=q("folder"),
                props=_json_dict(q("props")),
                base_updated=q("base_updated"),
            )
        )
    if action == "delete":
        return http_json_response(store.delete_note(root, q("id") or ""))
    if action == "restore":
        return http_json_response(store.restore_note(root, q("id") or ""))
    if action == "purge":
        return http_json_response(store.purge_note(root, q("id") or ""))
    if action == "trash":
        return http_json_response({"notes": store.list_trash(root)})
    if action == "trash/empty":
        return http_json_response(store.empty_trash(root))
    if action == "folders":
        return http_json_response({"folders": store.list_folders(root)})
    if action == "folders/create":
        return http_json_response(store.create_folder(root, q("path") or ""))
    if action == "folders/rename":
        return http_json_response(
            store.rename_folder(root, q("path") or "", q("name") or "")
        )
    if action == "folders/delete":
        return http_json_response(
            store.delete_folder(root, q("path") or "", force=_flag(q("force")))
        )
    if action == "tags":
        return http_json_response({"tags": store.list_tags(root)})
    if action == "tasks":
        return http_json_response(
            store.list_tasks(
                root,
                state=q("state") or "open",
                folder=q("folder"),
                limit=_int_or_none(q("limit")),
                cursor=q("cursor"),
            )
        )
    if action == "tasks/add":
        return http_json_response(
            store.add_task(
                root,
                text=q("text") or "",
                note_id=q("id") or None,
                inbox_title=q("inbox_title") or "Tasks",
            )
        )
    if action == "tasks/toggle":
        return http_json_response(
            store.toggle_task(
                root,
                q("id") or "",
                _int_or_none(q("line")) or 0,
                _flag(q("done")),
            )
        )
    if action == "backlinks":
        return http_json_response(store.note_backlinks(root, q("id") or ""))
    if action == "graph":
        return http_json_response(store.notes_graph(root))
    if action == "history":
        return http_json_response(
            {"snapshots": store.list_history(root, q("id") or "")}
        )
    if action == "history/get":
        return http_json_response(
            store.get_history_snapshot(root, q("id") or "", q("stamp") or "")
        )
    if action == "attachments":
        return http_json_response(
            store.list_attachments(
                root, limit=_int_or_none(q("limit")), cursor=q("cursor")
            )
        )
    if action == "attachments/upload":
        data = _optional_body(request)
        if data is None:
            raise NotesError(400, "missing attachment content")
        return http_json_response(
            store.save_attachment(root, q("name") or "file", data)
        )
    if action == "file":
        body, content_type = store.read_attachment(root, q("path") or "")
        return http_response(
            body,
            content_type=content_type,
            extra_headers=[
                ("X-Content-Type-Options", "nosniff"),
                ("Cache-Control", "private, max-age=3600"),
            ],
        )
    if action == "views":
        return http_json_response({"views": store.list_views(root)})
    if action == "views/save":
        view = _json_dict(q("view"))
        if view is None:
            raise NotesError(400, "missing view payload")
        return http_json_response(store.save_view(root, view))
    if action == "views/delete":
        return http_json_response(store.delete_view(root, q("id") or ""))
    if action == "search":
        return http_json_response(
            store.search_notes(
                root, q("q") or "", limit=_int_or_none(q("limit")) or 30
            )
        )
    if action == "import":
        return http_json_response(
            store.import_vault(
                root,
                q("path") or "",
                conflict=q("conflict") or "rename",
            )
        )
    if action == "rebuild":
        manifest = store.rebuild_manifest(root)
        search = store.rebuild_search_index(root)
        return http_json_response({"ok": True, "manifest": manifest, "search": search})
    return http_error(404, "unknown notes route")


def _int_or_none(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _json_dict(value: str | None) -> dict[str, Any] | None:
    if value is None or value == "":
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _json_list(value: str | None) -> list[str] | None:
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, list):
        return None
    return [str(item) for item in decoded]
