# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Persisted WebUI sidebar workspace state.

This state is UI-only metadata, scoped to the active navin instance data
directory (the directory containing the current config.json). It deliberately
does not modify agent sessions.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger

from navin.config.paths import get_webui_dir, is_navin_internal_path

WEBUI_SIDEBAR_STATE_SCHEMA_VERSION = 1
_MAX_STATE_FILE_BYTES = 256 * 1024
_MAX_LIST_ITEMS = 2_000
_MAX_MAP_ITEMS = 2_000
_MAX_KEY_LEN = 512
_MAX_TITLE_LEN = 160
_MAX_TAG_LEN = 40
_ALLOWED_DENSITIES = {"comfortable", "compact"}
_ALLOWED_SORTS = {"updated_desc", "created_desc", "title_asc"}
# The theme is a browser preference, but the CLI has to know it before any
# browser exists: a Chromium app window paints its own title bar from the
# browser theme, decided by a launch flag, and ignores anything the page says.
# Kept here rather than only in localStorage so the frame matches the app.
_ALLOWED_THEMES = {"dark", "light"}


def webui_sidebar_state_path() -> Path:
    return get_webui_dir() / "sidebar-state.json"


_ALLOWED_CHAT_MODULES = frozenset(
    {
        "chat",
        "dev",
        "code",
        "risklens",
        "scraping",
        "content",
        "marketing",
        "montage",
        "ads",
        "seo",
        "leads",
        "tenders",
        "career",
        "trading",
        "meeting",
        "ops",
        "notes",
        "crm",
    }
)


def default_webui_sidebar_state() -> dict[str, Any]:
    return {
        "schema_version": WEBUI_SIDEBAR_STATE_SCHEMA_VERSION,
        "pinned_keys": [],
        "archived_keys": [],
        "chat_order": [],
        "title_overrides": {},
        "project_name_overrides": {},
        "tags_by_key": {},
        "module_by_key": {},
        "module_by_path": {},
        "collapsed_groups": {},
        "recent_projects": [],
        "view": {
            "density": "comfortable",
            "show_previews": False,
            "show_timestamps": True,
            "show_archived": False,
            "sort": "updated_desc",
            "theme": "dark",
        },
        "updated_at": None,
    }


def _clean_string(value: Any, *, max_len: int = _MAX_KEY_LEN) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned[:max_len]


def _clean_string_list(value: Any, *, max_len: int = _MAX_KEY_LEN) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value[:_MAX_LIST_ITEMS]:
        cleaned = _clean_string(item, max_len=max_len)
        if cleaned is None or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _clean_bool_map(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, bool] = {}
    for key, raw in list(value.items())[:_MAX_MAP_ITEMS]:
        cleaned_key = _clean_string(key)
        if cleaned_key is None:
            continue
        out[cleaned_key] = bool(raw)
    return out


def _clean_title_overrides(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw_title in list(value.items())[:_MAX_MAP_ITEMS]:
        cleaned_key = _clean_string(key)
        cleaned_title = _clean_string(raw_title, max_len=_MAX_TITLE_LEN)
        if cleaned_key is None or cleaned_title is None:
            continue
        out[cleaned_key] = cleaned_title
    return out


def _clean_tags_by_key(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for key, raw_tags in list(value.items())[:_MAX_MAP_ITEMS]:
        cleaned_key = _clean_string(key)
        if cleaned_key is None:
            continue
        tags = _clean_string_list(raw_tags, max_len=_MAX_TAG_LEN)[:12]
        if tags:
            out[cleaned_key] = tags
    return out


def _clean_module_by_key(value: Any) -> dict[str, str]:
    """Last shell module where each chat was used (``dev`` for Code, …)."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw_module in list(value.items())[:_MAX_MAP_ITEMS]:
        cleaned_key = _clean_string(key)
        cleaned_module = _clean_string(raw_module, max_len=40)
        if cleaned_key is None or cleaned_module is None:
            continue
        module = cleaned_module.lower()
        if module not in _ALLOWED_CHAT_MODULES:
            continue
        if module == "code":
            module = "dev"
        out[cleaned_key] = module
    return out


def _clean_module_by_path(value: Any) -> dict[str, str]:
    """Module a project folder belongs to, so launching it opens the right one.

    Keyed by absolute folder path with a trailing slash stripped, because the
    same folder reaches us both ways depending on which picker sent it.
    """
    if not isinstance(value, dict):
        return {}
    out: dict[str, str] = {}
    for key, raw_module in list(value.items())[:_MAX_MAP_ITEMS]:
        cleaned_key = _clean_string(key)
        cleaned_module = _clean_string(raw_module, max_len=40)
        if cleaned_key is None or cleaned_module is None:
            continue
        if is_navin_internal_path(cleaned_key):
            continue
        module = cleaned_module.lower()
        if module not in _ALLOWED_CHAT_MODULES:
            continue
        if module == "code":
            module = "dev"
        path = cleaned_key.replace("\\", "/").rstrip("/") or cleaned_key
        out[path] = module
    return out


_MAX_RECENT_PROJECTS = 20


def _clean_recent_projects(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value[:_MAX_LIST_ITEMS]:
        if not isinstance(item, dict):
            continue
        path = _clean_string(item.get("path"))
        if path is None or path in seen:
            continue
        # Never remember the internal .navin storage as a "project": it kept
        # resurfacing as a recent entry in every project picker.
        if is_navin_internal_path(path):
            continue
        seen.add(path)
        name = _clean_string(item.get("name"), max_len=_MAX_TITLE_LEN) or ""
        out.append({"path": path, "name": name})
        if len(out) >= _MAX_RECENT_PROJECTS:
            break
    return out


def _clean_view(value: Any) -> dict[str, Any]:
    default = default_webui_sidebar_state()["view"]
    if not isinstance(value, dict):
        return dict(default)
    density = value.get("density")
    sort = value.get("sort")
    theme = value.get("theme")
    return {
        "theme": theme if theme in _ALLOWED_THEMES else default["theme"],
        "density": density if density in _ALLOWED_DENSITIES else default["density"],
        "show_previews": bool(value.get("show_previews", default["show_previews"])),
        "show_timestamps": bool(value.get("show_timestamps", default["show_timestamps"])),
        "show_archived": bool(value.get("show_archived", default["show_archived"])),
        "sort": sort if sort in _ALLOWED_SORTS else default["sort"],
    }


def normalize_webui_sidebar_state(raw: Any) -> dict[str, Any]:
    """Return a schema-v1 sidebar state from any older/partial input."""
    if not isinstance(raw, dict):
        raw = {}
    state = default_webui_sidebar_state()
    state["pinned_keys"] = _clean_string_list(raw.get("pinned_keys"))
    state["archived_keys"] = _clean_string_list(raw.get("archived_keys"))
    state["chat_order"] = _clean_string_list(raw.get("chat_order"))
    state["title_overrides"] = _clean_title_overrides(raw.get("title_overrides"))
    state["project_name_overrides"] = _clean_title_overrides(
        raw.get("project_name_overrides")
    )
    state["tags_by_key"] = _clean_tags_by_key(raw.get("tags_by_key"))
    state["module_by_key"] = _clean_module_by_key(raw.get("module_by_key"))
    state["module_by_path"] = _clean_module_by_path(raw.get("module_by_path"))
    state["collapsed_groups"] = _clean_bool_map(raw.get("collapsed_groups"))
    state["recent_projects"] = _clean_recent_projects(raw.get("recent_projects"))
    state["view"] = _clean_view(raw.get("view"))
    updated_at = raw.get("updated_at")
    state["updated_at"] = updated_at if isinstance(updated_at, str) else None
    return state


def read_webui_sidebar_state() -> dict[str, Any]:
    path = webui_sidebar_state_path()
    if not path.is_file():
        return default_webui_sidebar_state()
    try:
        if path.stat().st_size > _MAX_STATE_FILE_BYTES:
            logger.warning("webui sidebar state too large, ignoring: {}", path)
            return default_webui_sidebar_state()
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("read webui sidebar state failed {}: {}", path, e)
        return default_webui_sidebar_state()
    return normalize_webui_sidebar_state(raw)


def webui_theme() -> str:
    """The theme last chosen in the WebUI, for callers that run before it does."""
    view = read_webui_sidebar_state().get("view") or {}
    theme = view.get("theme")
    return theme if theme in _ALLOWED_THEMES else "dark"


def write_webui_sidebar_state(raw: dict[str, Any]) -> dict[str, Any]:
    state = normalize_webui_sidebar_state(raw)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    encoded = json.dumps(
        state,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) > _MAX_STATE_FILE_BYTES:
        raise ValueError("sidebar state is too large")

    path = webui_sidebar_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "wb") as f:
        f.write(encoded)
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
    except OSError:
        return state
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    return state
