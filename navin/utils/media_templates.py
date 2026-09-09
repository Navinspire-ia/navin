# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Marketing / Montage media templates hosted on AWS (S3).

The gallery catalog lives in ``templates/media/catalog.json`` and is overridden
by ``media-templates/v1/catalog.json`` on the public Navin bucket when reachable.
Selecting a template attaches it to the turn; the agent then downloads the
file into the workspace. Nothing ships in the git repo except the catalog.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from navin.runtime_context import RuntimeContextBlock, wrap_runtime_context_lines
from navin.templates.apps.schema import AWS_TEMPLATES_BASE_URL
from navin.utils.workspace_resources import prepare_workspace_resources

if TYPE_CHECKING:
    from navin.agent.tools.context import RequestContext

MEDIA_TEMPLATE_METADATA_KEY = "media_template"
MEDIA_TEMPLATES_METADATA_KEY = "media_templates"
MAX_MEDIA_TEMPLATES_PER_TURN = 6
AWS_MEDIA_PREFIX = "media-templates/v1"
_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
_FAMILIES = (
    "stock",
    "style",
    "character",
    "element",
    "location",
    "structure",
    "color",
    "effects",
    "camera",
)
# Role each family plays when several references ride the same turn.
_FAMILY_ROLE = {
    "stock": "subject / scene reference",
    "style": "look, grade and typography space",
    "character": "face, wardrobe and persona lock",
    "element": "product / prop hero",
    "location": "set and environment",
    "structure": "layout grid and composition",
    "color": "palette lock",
    "effects": "finish (grain, glow, overlay)",
    "camera": "lens, angle and movement",
}
_KINDS = ("image", "video")
_TABS = ("photos", "illustrations", "designs")
_FORMATS = ("16:9", "9:16", "1:1", "21:9", "4:5")
_STUDIOS = ("marketing", "montage")
_BUNDLED_CATALOG = Path(__file__).resolve().parents[2] / "templates" / "media" / "catalog.json"
_WORKSPACE_FOLDER = Path("media-templates")
_CACHE_TTL_S = 120.0
_catalog_cache: tuple[float, list[dict[str, Any]], str] | None = None


def reset_media_template_cache() -> None:
    global _catalog_cache
    _catalog_cache = None


def media_templates_base_url() -> str:
    raw = (
        os.environ.get("NAVIN_MEDIA_TEMPLATES_BASE_URL", "").strip()
        or os.environ.get("NAVIN_TEMPLATES_BASE_URL", "").strip()
        or os.environ.get("UPDATE_BASE_URL", "").strip()
        or AWS_TEMPLATES_BASE_URL
    )
    return raw.rstrip("/")


def media_template_public_url(s3_key: str) -> str:
    key = s3_key.lstrip("/")
    return f"{media_templates_base_url()}/{key}"


def catalog_public_url() -> str:
    return f"{media_templates_base_url()}/{AWS_MEDIA_PREFIX}/catalog.json"


def _normalize_item(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    ident = str(raw.get("id") or "").strip()
    if not _ID_RE.match(ident):
        return None
    title = str(raw.get("title") or ident).strip()
    kind = str(raw.get("kind") or "").strip().lower()
    family = str(raw.get("family") or "").strip().lower()
    tab = str(raw.get("tab") or "photos").strip().lower()
    fmt = str(raw.get("format") or "16:9").strip()
    s3_key = str(raw.get("s3_key") or "").strip()
    if kind not in _KINDS or family not in _FAMILIES or not s3_key:
        return None
    if tab not in _TABS:
        tab = "photos"
    if fmt not in _FORMATS:
        fmt = "16:9"
    studios_raw = raw.get("studio")
    if isinstance(studios_raw, str):
        studios = [studios_raw]
    elif isinstance(studios_raw, list):
        studios = [str(item) for item in studios_raw]
    else:
        studios = list(_STUDIOS)
    studios = [name for name in studios if name in _STUDIOS]
    if not studios:
        return None
    tags_raw = raw.get("tags")
    tags = (
        [str(tag).strip() for tag in tags_raw if str(tag).strip()]
        if isinstance(tags_raw, list)
        else []
    )
    preview = str(raw.get("preview_url") or "").strip()
    intent = str(raw.get("intent") or "both").strip().lower()
    if intent not in {"marketing", "montage", "both"}:
        intent = "both"
    hint = str(raw.get("hint") or "").strip()
    return {
        "id": ident,
        "title": title,
        "studio": studios,
        "kind": kind,
        "family": family,
        "tab": tab,
        "tags": tags,
        "format": fmt,
        "premium": bool(raw.get("premium")),
        "intent": intent,
        "hint": hint,
        "s3_key": s3_key,
        "url": media_template_public_url(s3_key),
        "preview_url": preview or media_template_public_url(s3_key),
    }


def _load_catalog_dict(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("items")
    if not isinstance(rows, list):
        return []
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        item = _normalize_item(row)
        if item is None or item["id"] in seen:
            continue
        seen.add(item["id"])
        items.append(item)
    return items


def load_bundled_catalog() -> list[dict[str, Any]]:
    if not _BUNDLED_CATALOG.is_file():
        return []
    try:
        payload = json.loads(_BUNDLED_CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    return _load_catalog_dict(payload)


def fetch_remote_catalog(timeout_s: float = 1.5) -> list[dict[str, Any]] | None:
    try:
        with urllib.request.urlopen(catalog_public_url(), timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    items = _load_catalog_dict(payload)
    return items or None


def _catalog_items() -> tuple[list[dict[str, Any]], str]:
    global _catalog_cache
    now = time.monotonic()
    if _catalog_cache and now - _catalog_cache[0] < _CACHE_TTL_S:
        return _catalog_cache[1], _catalog_cache[2]
    remote = fetch_remote_catalog()
    if remote:
        items, source = remote, "s3"
    else:
        items, source = load_bundled_catalog(), "bundled"
    _catalog_cache = (now, items, source)
    return items, source


def list_media_templates(studio: str | None = None) -> dict[str, Any]:
    items, source = _catalog_items()
    wanted = (studio or "").strip().lower()
    if wanted in _STUDIOS:
        items = [item for item in items if wanted in item["studio"]]
    return {
        "schema": "navin-media-template.v1",
        "source": source,
        "s3_prefix": AWS_MEDIA_PREFIX,
        "base_url": media_templates_base_url(),
        "families": list(_FAMILIES),
        "formats": list(_FORMATS),
        "items": items,
    }


def get_media_template(template_id: str) -> dict[str, Any] | None:
    ident = (template_id or "").strip()
    if not _ID_RE.match(ident):
        return None
    for item in list_media_templates()["items"]:
        if item["id"] == ident:
            return item
    return None


def normalize_media_template_mention(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    ident = str(raw.get("id") or raw.get("name") or "").strip()
    item = get_media_template(ident)
    if item is None:
        return None
    return {
        "id": item["id"],
        "title": item["title"],
        "kind": item["kind"],
        "family": item["family"],
        "tab": item.get("tab"),
        "tags": list(item.get("tags") or []),
        "format": item["format"],
        "intent": item.get("intent") or "both",
        "hint": item.get("hint") or "",
        "studio": list(item["studio"]),
        "s3_key": item["s3_key"],
        "url": item["url"],
    }


def normalize_media_template_mentions(raw: object) -> list[dict[str, Any]]:
    """Normalize a client payload into at most MAX_MEDIA_TEMPLATES_PER_TURN mentions."""
    rows: list[object]
    if isinstance(raw, Mapping):
        rows = [raw]
    elif isinstance(raw, list):
        rows = list(raw)
    else:
        return []
    mentions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        mention = normalize_media_template_mention(row)
        if mention is None or mention["id"] in seen:
            continue
        seen.add(mention["id"])
        mentions.append(mention)
        if len(mentions) >= MAX_MEDIA_TEMPLATES_PER_TURN:
            break
    return mentions


def _download(url: str, dest: Path, timeout_s: float = 45.0) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as resp:
            data = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        if dest.exists():
            dest.unlink(missing_ok=True)
        return False
    if not data:
        return False
    dest.write_bytes(data)
    return dest.is_file() and dest.stat().st_size > 0


def materialize_media_template(
    mention: Mapping[str, Any],
    workspace: Path,
) -> dict[str, Any] | None:
    item = get_media_template(str(mention.get("id") or ""))
    if item is None:
        return None
    ext = Path(item["s3_key"]).suffix or (".mp4" if item["kind"] == "video" else ".jpg")
    resources = prepare_workspace_resources(workspace)
    if resources is None:
        return {**item, "local_path": None, "source": "missing", "error": "workspace missing"}
    root = workspace.expanduser().resolve(strict=False)

    def _materialized(path: Path, source: str) -> dict[str, Any]:
        return {
            **item,
            "local_path": path.relative_to(root).as_posix(),
            "source": source,
        }

    dest = resources / _WORKSPACE_FOLDER / f"{item['id']}{ext}"
    # The preview fallback is always an image: it must never wear the master's
    # extension (a JPEG saved as .mp4 breaks every downstream tool).
    preview_dest = resources / _WORKSPACE_FOLDER / f"{item['id']}-preview.jpg"
    if dest.is_file():
        return _materialized(dest, "s3")
    if preview_dest.is_file():
        return _materialized(preview_dest, "preview")
    if _download(item["url"], dest):
        return _materialized(dest, "s3")
    preview = str(item.get("preview_url") or "")
    if preview and preview != item["url"] and _download(preview, preview_dest):
        return _materialized(preview_dest, "preview")
    return {
        **item,
        "local_path": None,
        "source": "missing",
        "error": (
            "AWS file is not uploaded yet "
            f"({item['s3_key']}). Upload it to the navinagent bucket."
        ),
    }


def _mention_lines(materialized: Mapping[str, Any], index: int, total: int) -> list[str]:
    tags = materialized.get("tags") or []
    tag_text = ", ".join(str(tag) for tag in tags) if tags else "-"
    family = str(materialized.get("family") or "")
    role = _FAMILY_ROLE.get(family, "visual reference")
    lines = [
        f"[ref {index}/{total}] {materialized.get('title')} (id: {materialized.get('id')})",
        f"  role: {family} = {role}",
        f"  kind: {materialized.get('kind')}  format: {materialized.get('format')}"
        f"  tab: {materialized.get('tab') or '-'}  tags: {tag_text}",
        f"  s3: {materialized.get('url')}",
    ]
    hint = str(materialized.get("hint") or "").strip()
    if hint:
        lines.append(f"  how to use: {hint}")
    local = materialized.get("local_path")
    source = str(materialized.get("source") or "")
    if local and source == "preview":
        lines.append(
            f"  local workspace path: {local} (official PREVIEW STILL - the AWS "
            "master is not uploaded yet)"
        )
        lines.append(
            "  note: use this still as the visual reference (look, framing, "
            "palette). Do not re-download the s3 url and do not fabricate a "
            "stand-in clip."
        )
    elif local:
        lines.append(f"  local workspace path: {local}")
    else:
        err = materialized.get("error") or "Download failed."
        lines.append(f"  download: missing - {err}")
    return lines


def _normalize_and_materialize(
    raw: object,
    workspace: object,
) -> list[dict[str, Any]]:
    """Blocking part of the provider (catalog fetch + S3 downloads).

    Runs in a worker thread: a single turn can materialize up to
    MAX_MEDIA_TEMPLATES_PER_TURN files at ~45-90s worst case each, which must
    never sit on the gateway event loop.
    """
    mentions = normalize_media_template_mentions(raw)
    materialized_list: list[dict[str, Any]] = []
    for mention in mentions:
        materialized = (
            materialize_media_template(mention, Path(str(workspace)))
            if workspace
            else dict(mention)
        )
        if materialized is not None:
            materialized_list.append(materialized)
    return materialized_list


async def media_template_context_provider(
    ctx: RequestContext,
) -> RuntimeContextBlock | None:
    raw = None
    if ctx.metadata:
        raw = (
            ctx.metadata.get(MEDIA_TEMPLATES_METADATA_KEY)
            or ctx.metadata.get(MEDIA_TEMPLATE_METADATA_KEY)
        )
    if not raw:
        return None
    workspace = getattr(ctx, "workspace", None)
    materialized_list = await asyncio.to_thread(
        _normalize_and_materialize, raw, workspace
    )
    if not materialized_list:
        return None
    total = len(materialized_list)
    plural = total > 1
    lines = [
        (
            f"MEDIA REFERENCES (AWS) - {total} attached, already materialized in "
            "the workspace. Use the local paths below - do NOT re-download."
            if plural
            else "MEDIA TEMPLATE (AWS) - already materialized in the workspace. "
            "Use the local path below as the visual / motion reference - do NOT "
            "re-download."
        ),
    ]
    if plural:
        lines.append(
            "COMBINE ALL references with the user prompt into ONE coherent output. "
            "Each reference plays the role of its family (style = grade, character = "
            "face, color = palette, element = product, location = set, structure = "
            "layout, camera = lens / movement, effects = finish, stock = subject). "
            "Do not produce one output per reference."
        )
    for index, materialized in enumerate(materialized_list, start=1):
        lines.extend(_mention_lines(materialized, index, total))
    formats = [str(item.get("format") or "") for item in materialized_list]
    main_format = formats[0] if formats else "16:9"
    lines.append(
        "GENERATION: pass the local files as reference images to generate_image "
        "(reference_images) and generate_video (reference_image / first frame), "
        f"and keep aspect_ratio={main_format} unless the user asks otherwise. "
        "Video references define motion, cut rhythm and camera - mirror them."
    )
    module = ""
    if ctx.metadata:
        module = str(ctx.metadata.get("product_module") or "").strip().lower()
    if module == "marketing":
        lines.append(
            "STUDIO = MARKETING (product / brand). These files are brand / offer "
            "references. Produce campaign assets (ads, social, landing, packshots). "
            "Generate image / video / music / speech with the same tools Montage uses "
            "(generate_image, generate_video, generate_music, generate_speech, "
            "montage assemble/package/render). Do not drift into a demo-edit desk."
        )
    elif module == "montage":
        lines.append(
            "STUDIO = MONTAGE (edit / composition). These files are cut, camera, "
            "grade or timing references. Record, trim, assemble, package platform "
            "exports. Do not rewrite the offer or invent a full campaign plan."
        )
    if any(item.get("local_path") for item in materialized_list):
        lines.append(
            "Use the local files as-is. Do not re-download them and do not "
            "invent substitute images or clips."
        )
    if any(item.get("source") == "preview" for item in materialized_list):
        lines.append(
            "Some references fell back to their official preview still because "
            "the AWS master is not uploaded yet: the still IS the reference "
            "(look, framing, palette); motion comes from the 'how to use' hint. "
            "NEVER generate placeholder or synthetic stand-in media (ffmpeg "
            "lavfi, solid colors, drawtext) to replace it."
        )
    if any(not item.get("local_path") for item in materialized_list):
        lines.append(
            "STOP on visuals: at least one reference above has NO local file - "
            "the AWS object is missing. Tell the user exactly which reference "
            "is missing and stop the visual work that depends on it. NEVER "
            "generate placeholder or substitute media (no ffmpeg color cards, "
            "no lavfi, no drawtext text cards), and never assemble, package or "
            "deliver anything built from placeholders."
        )
    lines.append("Never bake these into a PPT/Word page as WebGL.")
    return RuntimeContextBlock(
        source="media_template",
        content=wrap_runtime_context_lines(lines),
    )
