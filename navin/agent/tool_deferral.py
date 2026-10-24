# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Send the core tool schemas every step; load the rest on demand.

The schemas of every enabled tool cost ~19.5k prompt tokens on each model
call, and a dozen tools make 99% of the calls. Prefill time grows with the
prompt, so the long tail is held back: its names are listed in the
``tool_search`` schema, and a tool joins the list sent to the model once it
is loaded (by ``tool_search``, by being called directly, or because the
user's message named it). A loaded tool stays for the conversation, so the
tool list only grows and the provider's prompt cache survives.

Deferral never grants anything: denylists and allowlists are applied before
it, and calling a deferred tool by name still runs it.
"""

from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

TOOL_SEARCH_NAME = "tool_search"

# Always sent: the default code loop plus the tools runtime nudges ask for
# by name (verify, goals, composer mode). Everything else is deferred.
CORE_TOOLS: frozenset[str] = frozenset({
    TOOL_SEARCH_NAME,
    "apply_patch",
    "ask_user",
    "code_index",
    "request_credentials",
    "create_goal",
    "edit_file",
    "exec",
    "find_files",
    "git",
    "grep",
    "list_dir",
    "manage_files",
    "message",
    "open_preview",
    "read_file",
    "set_composer_mode",
    "skill",
    "project",
    "spawn",
    "start_app",
    "test_run",
    "update_goal",
    "verify",
    "web_fetch",
    "web_search",
    "write_file",
})

_MAX_SESSIONS = 512
_loaded: OrderedDict[str, list[str]] = OrderedDict()
# Deferred schemas visible to each session on its latest request, so
# tool_search answers from exactly what this turn may use.
_catalogs: dict[str, dict[str, dict[str, Any]]] = {}
_lock = threading.Lock()


def deferral_enabled() -> bool:
    return os.environ.get("NAVIN_DEFER_TOOLS", "1").strip().lower() not in {"0", "false", "no", "off"}


def schema_name(schema: dict[str, Any]) -> str:
    fn = schema.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        return fn["name"]
    name = schema.get("name")
    return name if isinstance(name, str) else ""


def _schema_description(schema: dict[str, Any]) -> str:
    fn = schema.get("function") if isinstance(schema.get("function"), dict) else schema
    value = fn.get("description") if isinstance(fn, dict) else ""
    return value if isinstance(value, str) else ""


def loaded_tools(session_key: str | None) -> list[str]:
    if not session_key:
        return []
    with _lock:
        return list(_loaded.get(session_key, ()))


def mark_loaded(session_key: str | None, names: Iterable[str]) -> list[str]:
    """Add tools to the session's loaded list; return the newly added ones."""
    if not session_key:
        return []
    added: list[str] = []
    with _lock:
        current = _loaded.get(session_key)
        if current is None:
            current = []
            _loaded[session_key] = current
        for name in names:
            if name and name not in CORE_TOOLS and name not in current:
                current.append(name)
                added.append(name)
        _loaded.move_to_end(session_key)
        while len(_loaded) > _MAX_SESSIONS:
            evicted, _ = _loaded.popitem(last=False)
            _catalogs.pop(evicted, None)
    return added


def catalog(session_key: str | None) -> dict[str, dict[str, Any]]:
    if not session_key:
        return {}
    with _lock:
        return dict(_catalogs.get(session_key, {}))


def _hint(description: str, limit: int = 70) -> str:
    first = re.split(r"(?<=[.:;])\s|\n", description.strip(), maxsplit=1)[0].rstrip(".:;")
    if len(first) <= limit:
        return first
    cut = first.rfind(" ", 0, limit - 3)
    return first[: cut if cut > 20 else limit - 3].rstrip(" ,") + "..."


def _tool_search_schema(base: dict[str, Any], deferred: list[dict[str, Any]]) -> dict[str, Any]:
    lines = [f"- {schema_name(s)}: {_hint(_schema_description(s))}" for s in deferred]
    description = (
        "Load tools that are not in your tool list yet. Their schemas join the "
        "list from your next step, for the rest of the conversation. Pass exact "
        "names with 'select:name1,name2', or keywords to search. You may also "
        "call a listed tool directly. Available to load:\n" + "\n".join(lines)
    )
    out = dict(base)
    fn = dict(out.get("function") or {})
    fn["description"] = description
    out["function"] = fn
    return out


def visible_tool_definitions(
    definitions: list[dict[str, Any]],
    session_key: str | None,
    *,
    always: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Core and loaded schemas, in a stable order; record the deferred rest.

    ``definitions`` is the already filtered (deny/allow) list. Core tools keep
    their registry order and loaded ones follow in load order, so a newly
    loaded tool only appends to the list instead of reshuffling it.
    """
    if not deferral_enabled():
        return definitions
    by_name = {schema_name(schema): schema for schema in definitions}
    search = by_name.get(TOOL_SEARCH_NAME)
    if search is None:
        # Without the loader tool the model could not reach deferred tools.
        return definitions
    keep = set(CORE_TOOLS) | {name for name in always if name}
    order = loaded_tools(session_key)
    keep.update(order)
    # The listing names every loadable tool, loaded or not: it then changes
    # only with the deny/allow lists, never because a tool was just loaded.
    listed = [s for s in definitions if schema_name(s) not in CORE_TOOLS]
    if session_key:
        with _lock:
            _catalogs[session_key] = {schema_name(s): s for s in listed}
    core = [
        _tool_search_schema(s, listed) if schema_name(s) == TOOL_SEARCH_NAME else s
        for s in definitions
        if schema_name(s) in keep and schema_name(s) not in order
    ]
    if not listed:
        core = [s for s in core if schema_name(s) != TOOL_SEARCH_NAME]
    loaded = [by_name[name] for name in order if name in by_name]
    return core + loaded


def search(session_key: str | None, query: str) -> tuple[list[str], list[str]]:
    """Resolve a tool_search query to (loaded names, unknown names)."""
    available = catalog(session_key)
    query = (query or "").strip()
    if query.lower().startswith("select:"):
        wanted = [part.strip() for part in query[7:].split(",") if part.strip()]
        found = [name for name in wanted if name in available]
        missing = [name for name in wanted if name not in available]
        return found, missing
    terms = [t for t in re.split(r"[\s,]+", query.lower()) if len(t) > 1]
    if not terms:
        return [], []
    scored: list[tuple[int, str]] = []
    for name, schema in available.items():
        haystack = f"{name} {_schema_description(schema)}".lower()
        score = sum(3 if term in name.lower() else 1 for term in terms if term in haystack)
        if score:
            scored.append((score, name))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[:5]], []


def reset_for_tests() -> None:
    with _lock:
        _loaded.clear()
        _catalogs.clear()
