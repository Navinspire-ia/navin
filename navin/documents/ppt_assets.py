# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Visual Asset Policy for decks: type first, then a trusted source.

The agent must never scrape Google Images or paste a random copyrighted file.
This module is the gate. It picks the *kind* of visual from the slide meaning,
then resolves it from a short allow-list: Lucide / Tabler / Heroicons / Simple
Icons for marks, Unsplash then Pexels then Pixabay for photos, native SVG for
diagrams, the user's own file when they gave one.

A photo is cached under the instance data dir with a sidecar of who made it
and which licence it carries. An SVG is sanitised before it is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from navin.config.paths import get_data_dir

ALLOWED_IMAGE_SUFFIXES = frozenset({".svg", ".png", ".jpg", ".jpeg", ".webp"})
ALLOWED_MIME = frozenset(
    {"image/svg+xml", "image/png", "image/jpeg", "image/webp", "image/jpg"}
)

# Hosts and path fragments that are never a legal source for a deck visual.
BLOCKED_MARKERS = (
    "google.com/imgres",
    "google.com/images",
    "images.google.",
    "googleusercontent.com",
    "gstatic.com/images",
    "maps.google.",
    "maps.googleapis.com",
    "www.google.com/maps",
    "shutterstock.com",
    "gettyimages.",
    "istockphoto.com",
    "alamy.com",
    "adobestock.com",
    "stock.adobe.com",
    "depositphotos.com",
    "123rf.com",
)

ALLOWED_PHOTO_HOSTS = frozenset(
    {
        "images.unsplash.com",
        "plus.unsplash.com",
        "unsplash.com",
        "images.pexels.com",
        "www.pexels.com",
        "pexels.com",
        "cdn.pixabay.com",
        "pixabay.com",
        "www.pixabay.com",
    }
)

PHOTO_PROVIDERS = ("unsplash", "pexels", "pixabay")

ICON_LIBRARIES = (
    (
        "lucide",
        "https://cdn.jsdelivr.net/npm/lucide-static@0.469.0/icons/{name}.svg",
    ),
    (
        "tabler",
        "https://cdn.jsdelivr.net/npm/@tabler/icons@3.31.0/icons/outline/{name}.svg",
    ),
    (
        "heroicons",
        "https://cdn.jsdelivr.net/npm/heroicons@2.2.0/24/outline/{name}.svg",
    ),
)
SIMPLE_ICONS_URL = "https://cdn.jsdelivr.net/npm/simple-icons@14.12.0/icons/{name}.svg"

# Content kind -> visual type. The type is chosen before any search.
KIND_TO_ASSET = {
    "numbers": "chart",
    "chronology": "diagram",
    "steps": "diagram",
    "loop": "diagram",
    "organization": "diagram",
    "comparison": "diagram",
    "architecture": "diagram",
    "geography": "map",
    "product": "screenshot",
    "testimonial": "photo",
    "people": "photo",
    "funnel": "diagram",
    "closing": "none",
    "abstract": "illustration",
}

# Theme name -> extra words so the photo matches the design system, not just the topic.
THEME_QUERY = {
    "startup": "energetic product office",
    "aurora_glass": "soft light technology glass",
    "neon_impact": "dark neon technology",
    "premium_black": "dark editorial luxury",
    "premium_green": "calm nature professional",
    "textbook": "clean academic workspace",
    "black_and_white_clean": "minimal black white architecture",
    "editorial_luxe": "editorial luxury interior",
    "minimalist_2": "calm light interior",
    "numbers_clean": "data office restrained",
    "architect": "architecture studio",
    "portfolio": "design studio portfolio",
    "competitor_analysis_blue": "corporate meeting analysis",
    "reponse_appel_offre": "formal meeting documents",
}

PHOTO_SLOTS = ("background", "left", "right")
_FAKE_IMAGES = {"", "image.png", "placeholder.png", "photo.png", "screenshot.png"}

# Three Unsplash stills per theme: full-bleed, image-left, image-right.
# Licence: Unsplash. The engine copies them next to the slides at render time.
# Curation rules: every still is unique across the whole map, matches the
# theme mood, and carries no readable text (no keyboards, book spines, signs,
# whiteboards, or screens with code) so any output language stays clean.
THEME_PHOTO_SEEDS: dict[str, dict[str, str]] = {
    "startup": {
        "background": "1486406146926-c627a92ad1ab",
        "left": "1497215728101-856f4ea42174",
        "right": "1524758631624-e2822e304c36",
    },
    "aurora_glass": {
        "background": "1519681393784-d120267933ba",
        "left": "1557682250-33bd709cbe85",
        "right": "1550859492-d5da9d8e45f3",
    },
    "neon_impact": {
        "background": "1550684848-fac1c5b4e853",
        "left": "1462331940025-496dfbfc7564",
        "right": "1419242902214-272b3f66ee7a",
    },
    "premium_black": {
        "background": "1477959858617-67f85cf4f1df",
        "left": "1493397212122-2b85dda8106b",
        "right": "1511818966892-d7d671e672a2",
    },
    "premium_green": {
        "background": "1470770903676-69b98201ea1c",
        "left": "1447752875215-b2761acb3c5d",
        "right": "1470071459604-3b5ec3a7fe05",
    },
    "textbook": {
        "background": "1580582932707-520aed937b7b",
        "left": "1541339907198-e08756dedf3f",
        "right": "1562774053-701939374585",
    },
    "black_and_white_clean": {
        "background": "1487958449943-2429e8be8625",
        "left": "1488972685288-c3fd157d7c7a",
        "right": "1494891848038-7bd202a2afeb",
    },
    "editorial_luxe": {
        "background": "1618221195710-dd6b41faaea6",
        "left": "1600210492486-724fe5c67fb0",
        "right": "1600585154340-be6161a56a0c",
    },
    "minimalist_2": {
        "background": "1505693416388-ac5ce068fe85",
        "left": "1493663284031-b7e3aefcae8e",
        "right": "1519710164239-da123dc03ef4",
    },
    "numbers_clean": {
        "background": "1554034483-04fda0d3507b",
        "left": "1557672172-298e090bd0f1",
        "right": "1620641788421-7a1c342ea42e",
    },
    "architect": {
        "background": "1449157291145-7efd050a4d0e",
        "left": "1486718448742-163732cd1544",
        "right": "1431576901776-e539bd916ba2",
    },
    "portfolio": {
        "background": "1513364776144-60967b0f800f",
        "left": "1561070791-2526d30994b5",
        "right": "1541961017774-22349e4a1262",
    },
    "competitor_analysis_blue": {
        "background": "1451187580459-43490279c0fa",
        "left": "1444723121867-7a241cacace9",
        "right": "1480714378408-67cf0d13bc1b",
    },
    "reponse_appel_offre": {
        "background": "1507679799987-c73779587ccf",
        "left": "1497366754035-f200968a6e72",
        "right": "1431540015161-0bf868a2d407",
    },
}

_PHOTO_LAYOUTS = frozenset(
    {
        "cover",
        "hero",
        "statement",
        "section-break",
        "closing",
        "full-bleed-hero",
        "full-image",
        "image-text",
        "text-image",
        "case-study",
        "quote",
        "team",
        "gallery",
        "product-hero",
    }
)
_BACKGROUND_LAYOUTS = frozenset(
    {
        "cover",
        "hero",
        "statement",
        "section-break",
        "closing",
        "full-bleed-hero",
        "full-image",
    }
)

# Slide-title words that must never be the search query. Search the scene.
INTENT_SCENES = (
    (("ai", "agent", "llm", "model", "intelligence"), "modern enterprise team digital technology"),
    (("cloud", "kubernetes", "infra", "devops"), "data center cloud infrastructure"),
    (("security", "risk", "threat"), "cybersecurity operations center"),
    (("finance", "revenue", "arr", "budget"), "finance meeting professional"),
    (("team", "people", "leadership", "founder"), "professional leadership portrait office"),
    (("city", "market", "expansion", "region"), "modern city business district"),
    (("office", "work", "enterprise"), "modern office collaboration"),
    (("product", "app", "saas", "screenshot"), "product interface laptop"),
    (("health", "care", "hospital"), "modern healthcare professional"),
    (("factory", "industry", "manufacturing"), "modern industrial facility"),
    (("education", "learn", "training"), "professional workshop classroom"),
)

_SCRIPTISH = re.compile(
    r"<script\b|javascript:|on\w+\s*=|<foreignObject\b|<iframe\b",
    re.I,
)
_WATERMARK = re.compile(r"watermark|preview[_-]?only|thumb(?:nail)?|sprite", re.I)


def cache_root() -> Path:
    root = get_data_dir() / "assets" / "cache"
    for name in ("photos", "icons", "illustrations", "logos", "maps", "generated"):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def blocked_source(url: str) -> bool:
    """True when this URL must never be downloaded for a deck."""
    lowered = (url or "").strip().lower()
    if not lowered:
        return False
    return any(marker in lowered for marker in BLOCKED_MARKERS)


def allowed_photo_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ALLOWED_PHOTO_HOSTS


def choose_asset_type(
    kind: str = "",
    visual: Mapping[str, Any] | None = None,
    *,
    user_asset: str = "",
) -> str:
    """Pick the visual *type* before any search. User files win."""
    if user_asset:
        return "user"
    visual = visual or {}
    declared = str(visual.get("type") or "").strip().lower()
    if declared in {"chart", "table", "kpi", "process", "cycle", "timeline",
                    "roadmap", "funnel", "hierarchy", "matrix", "architecture",
                    "map", "comparison"}:
        return "diagram" if declared != "chart" else "chart"
    if declared in {"image", "photo"}:
        return "photo"
    if declared in {"icon", "logos", "logo"}:
        return "logo" if declared != "icon" else "icon"
    if declared in {"illustration", "product", "people", "quote"}:
        return {
            "illustration": "illustration",
            "product": "screenshot",
            "people": "photo",
            "quote": "photo",
        }[declared]
    return KIND_TO_ASSET.get((kind or "").strip().lower(), "none")


def fit_for(asset_type: str, *, portrait: bool = False) -> str:
    """How the picture sits in its frame. Never stretch."""
    if portrait or asset_type == "portrait":
        return "cover"
    return {
        "photo": "cover",
        "background": "cover",
        "screenshot": "contain",
        "logo": "contain",
        "icon": "contain",
        "illustration": "contain",
        "diagram": "contain",
        "map": "contain",
        "user": "contain",
    }.get(asset_type, "cover")


def visual_intent(
    title: str = "",
    *,
    kind: str = "",
    theme: str = "",
) -> str:
    """Search the scene the slide means, never the slide title itself."""
    hay = f"{title} {kind}".lower()
    scene = "modern professional workplace"
    for keys, query in INTENT_SCENES:
        if any(key in hay for key in keys):
            scene = query
            break
    extra = THEME_QUERY.get((theme or "").strip(), "")
    return f"{scene} {extra}".strip()


def theme_photos_dir(theme: str) -> Path | None:
    """``templates/ppt/<theme>/photos`` in the repo or a workspace copy."""
    name = (theme or "").strip()
    if not name:
        return None
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "templates" / "ppt" / name / "photos",
        Path.cwd() / "document-templates" / "ppt" / name / "photos",
        Path.cwd() / ".navin" / "resources" / "document-templates" / "ppt" / name / "photos",
    ]
    for parent in here.parents:
        candidates.append(parent / "templates" / "ppt" / name / "photos")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    theme_root = here.parents[2] / "templates" / "ppt" / name
    if theme_root.is_dir():
        return theme_root / "photos"
    return None


def theme_photo_pack(theme: str) -> dict[str, Path]:
    """The three stills that ship with the chosen theme."""
    folder = theme_photos_dir(theme)
    if folder is None:
        return {}
    out: dict[str, Path] = {}
    for slot in PHOTO_SLOTS:
        for suffix in (".jpg", ".jpeg", ".webp", ".png"):
            path = folder / f"{slot}{suffix}"
            if path.is_file() and path.stat().st_size > 1024:
                out[slot] = path
                break
    return out


def photo_slot_for(slide: Mapping[str, Any]) -> str:
    """Where the picture sits: background, left, or right."""
    visual = slide.get("visual") if isinstance(slide.get("visual"), dict) else {}
    declared = str(
        visual.get("slot") or visual.get("position") or visual.get("placement") or ""
    ).strip().lower()
    if declared in {"full-bleed", "full", "back", "bg"}:
        return "background"
    if declared in PHOTO_SLOTS:
        return declared
    layout = str(slide.get("layout") or "").strip()
    if layout in _BACKGROUND_LAYOUTS:
        return "background"
    if layout == "image-text":
        return "left"
    if layout in {"text-image", "case-study", "quote", "team", "product-hero"}:
        return "right"
    return ""


def _chosen_image(slide: Mapping[str, Any]) -> str:
    visual = slide.get("visual") if isinstance(slide.get("visual"), dict) else {}
    src = str(slide.get("image") or visual.get("src") or visual.get("image") or "").strip()
    return "" if src.lower() in _FAKE_IMAGES else src


def _stage_photo(src: Path, dest_dir: Path | None, slot: str) -> str:
    if dest_dir is None:
        return str(src)
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix.lower()
    if suffix == ".jpeg":
        suffix = ".jpg"
    dest = dest_dir / f"{slot}{suffix}"
    if not dest.is_file() or dest.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dest)
    return f"photos/{dest.name}"


def _should_use_theme_photo(slide: Mapping[str, Any]) -> bool:
    layout = str(slide.get("layout") or "").strip()
    if layout not in _PHOTO_LAYOUTS:
        return False
    visual = slide.get("visual") if isinstance(slide.get("visual"), dict) else {}
    kind = str(visual.get("kind") or visual.get("type") or "").strip().lower()
    if kind in {"screenshot", "product", "ui", "phone"}:
        return False
    return True


def score_photo(hit: Mapping[str, Any], query: str) -> int:
    """0-100. Below 80 the photo is not good enough for a hero."""
    width = int(hit.get("width") or 0)
    height = int(hit.get("height") or 0)
    url = str(hit.get("url") or "")
    if _WATERMARK.search(url) or _WATERMARK.search(str(hit.get("preview_url") or "")):
        return 0
    if blocked_source(url):
        return 0
    semantic = 18
    tokens = [part for part in query.lower().split() if len(part) > 3]
    blob = f"{hit.get('credit', '')} {hit.get('page_url', '')} {query}".lower()
    if tokens:
        hits = sum(1 for token in tokens if token in blob)
        semantic = min(30, 12 + 6 * hits)
    short = min(width, height)
    quality = 20 if short >= 1600 else 14 if short >= 1200 else 8 if short >= 800 else 0
    if width and height:
        ratio = width / height
        composition = 15 if 1.2 <= ratio <= 2.0 else 8
    else:
        composition = 6
    provider = str(hit.get("provider") or "")
    theme_pts = {"unsplash": 15, "pexels": 12, "pixabay": 9}.get(provider, 5)
    resolution = 10 if short >= 2000 else 7 if short >= 1400 else 3
    crop = 10 if short >= 1200 else 4
    return semantic + quality + composition + theme_pts + resolution + crop


def sanitize_svg(raw: str) -> str:
    """Drop script and event handlers. A deck SVG is a picture, not a program."""
    if _SCRIPTISH.search(raw or ""):
        raise ValueError("svg contains executable content")
    cleaned = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", raw or "", flags=re.I)
    return cleaned


def tint_svg(raw: str, color: str) -> str:
    """Paint Lucide-style currentColor icons with the theme accent."""
    if not color:
        return raw
    if "currentColor" in raw:
        return raw.replace("currentColor", color)
    if re.search(r"\bstroke=", raw) or re.search(r"\bfill=", raw):
        painted = re.sub(r'\bstroke="(?!none)[^"]*"', f'stroke="{color}"', raw)
        painted = re.sub(r'\bfill="(?!none)[^"]*"', f'fill="{color}"', painted)
        return painted
    return raw.replace("<svg", f'<svg stroke="{color}" fill="none"', 1)


def write_metadata(path: Path, payload: Mapping[str, Any]) -> Path:
    side = path.with_suffix(path.suffix + ".meta.json")
    data = dict(payload)
    data.setdefault("downloaded_at", datetime.now(timezone.utc).isoformat())
    side.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return side


def _cache_key(*parts: str) -> str:
    joined = "|".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:20]


def resolve_icon(
    name: str,
    *,
    color: str = "",
    brand: bool = False,
    dest: Path | None = None,
    fetch: Any | None = None,
) -> Path:
    """Lucide first, then Tabler, then Heroicons. Simple Icons for brands."""
    slug = re.sub(r"[^a-z0-9-]", "", (name or "").strip().lower().replace("_", "-"))
    if not slug:
        raise ValueError("icon name required")
    libraries = (("simple-icons", SIMPLE_ICONS_URL),) if brand else ICON_LIBRARIES
    getter = fetch or _http_get
    last_error = "not found"
    for library, template in libraries:
        url = template.format(name=slug)
        try:
            body, content_type = getter(url)
        except Exception as exc:  # noqa: BLE001 - try the next library
            last_error = str(exc)
            continue
        if "svg" not in (content_type or "") and not body.lstrip().startswith(b"<svg"):
            last_error = f"{library} did not return svg"
            continue
        text = sanitize_svg(body.decode("utf-8", errors="replace"))
        text = tint_svg(text, color)
        target = dest or (cache_root() / "icons" / f"{library}-{slug}.svg")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        write_metadata(
            target,
            {
                "provider": library,
                "source_url": url,
                "license": "MIT or CC0 (icon library)",
                "asset_type": "logo" if brand else "icon",
                "name": slug,
            },
        )
        return target
    raise FileNotFoundError(f"no trusted icon named {slug!r}: {last_error}")


def resolve_photo(
    query: str,
    *,
    dest: Path | None = None,
    theme: str = "",
    config: Any | None = None,
    search: Any | None = None,
    fetch: Any | None = None,
    min_score: int = 80,
) -> Path:
    """Unsplash, then Pexels, then Pixabay. Never Google Images."""
    intent = visual_intent(query, theme=theme) if query else visual_intent(theme=theme)
    searcher = search or _search_stock
    getter = fetch or _http_get
    best: dict[str, Any] | None = None
    best_score = -1
    for provider in PHOTO_PROVIDERS:
        result = searcher(provider, intent, config=config)
        if not result.get("ok"):
            continue
        for hit in result.get("hits") or []:
            if blocked_source(str(hit.get("url") or "")):
                continue
            if not allowed_photo_host(str(hit.get("url") or "")):
                continue
            scored = score_photo(hit, intent)
            if scored > best_score:
                best, best_score = hit, scored
        if best is not None and best_score >= min_score:
            break
    if best is None or best_score < min_score:
        raise FileNotFoundError(
            f"no trusted photo scored {min_score}+ for {intent!r} "
            "(configure Unsplash / Pexels / Pixabay keys, or generate)"
        )
    url = str(best["url"])
    body, content_type = getter(url)
    suffix = _suffix_for(url, content_type)
    if suffix not in ALLOWED_IMAGE_SUFFIXES or suffix == ".svg":
        raise ValueError(f"photo has unexpected type {content_type}")
    target = dest or (cache_root() / "photos" / f"{_cache_key(intent, url)}{suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    write_metadata(
        target,
        {
            "provider": best.get("provider"),
            "source_url": best.get("page_url") or url,
            "author": best.get("credit") or "",
            "license": "provider licence (Unsplash / Pexels / Pixabay)",
            "asset_type": "photography",
            "query": intent,
            "score": best_score,
            "width": best.get("width"),
            "height": best.get("height"),
        },
    )
    return target


def resolve_user_asset(path: str | Path) -> Path:
    """Highest priority: a file the user already gave us."""
    resolved = Path(path).expanduser()
    if not resolved.is_file():
        raise FileNotFoundError(f"user asset missing: {resolved}")
    if resolved.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError(f"unsupported user asset type: {resolved.suffix}")
    if resolved.suffix.lower() == ".svg":
        sanitize_svg(resolved.read_text(encoding="utf-8", errors="replace"))
    return resolved


def attach_visual(
    slide: Mapping[str, Any],
    *,
    theme: str = "",
    dest_dir: Path | None = None,
    user_asset: str = "",
) -> dict[str, Any]:
    """Fill ``image`` / ``visual.fit`` from the policy.

    Order: a file the user joined, then an image the agent set, then the
    three stills that ship with the chosen theme (background / left / right).
    Charts, processes and screenshots stay native. The agent may change a
    slot with ``visual.slot`` or replace ``image`` on request.
    """
    payload = dict(slide)
    visual = dict(payload.get("visual") or {})
    chosen = user_asset or _chosen_image(payload)
    asset_type = choose_asset_type(
        str(payload.get("kind") or ""),
        visual,
        user_asset=chosen,
    )
    if asset_type in {"none", "illustration"} and _should_use_theme_photo(payload):
        asset_type = "photo"
    visual["fit"] = visual.get("fit") or fit_for(
        asset_type,
        portrait=str(visual.get("kind") or "") == "portrait",
    )
    visual["asset_type"] = asset_type
    if asset_type in {"chart", "diagram", "map", "none"}:
        if str(payload.get("image") or "").lower() in _FAKE_IMAGES:
            payload.pop("image", None)
        payload["visual"] = visual
        return payload
    if user_asset:
        payload["image"] = str(resolve_user_asset(user_asset))
        visual["source"] = "user"
        payload["visual"] = visual
        return payload
    if chosen:
        payload["visual"] = visual
        return payload
    pack = theme_photo_pack(theme)
    if pack and _should_use_theme_photo(payload):
        layout = str(payload.get("layout") or "")
        if layout == "gallery":
            extras = payload.get("images")
            if not (isinstance(extras, list) and extras):
                staged = [
                    _stage_photo(pack[slot], dest_dir, slot)
                    for slot in PHOTO_SLOTS
                    if slot in pack
                ]
                if staged:
                    payload["images"] = staged
                    visual["source"] = "theme"
                    visual["slot"] = "gallery"
        else:
            slot = photo_slot_for(payload) or "right"
            src = pack.get(slot) or pack.get("right") or pack.get("left") or pack.get("background")
            if src is not None:
                payload["image"] = _stage_photo(src, dest_dir, slot)
                visual["source"] = "theme"
                visual["slot"] = slot
                visual["fit"] = visual.get("fit") or "cover"
                visual["asset_type"] = "photo"
        payload["visual"] = visual
        return payload
    payload["visual"] = visual
    return payload


def unsplash_photo_url(photo_id: str, *, width: int = 1920, height: int = 1080) -> str:
    slug = (photo_id or "").strip().lstrip("/")
    if slug.startswith("photo-"):
        slug = slug[len("photo-"):]
    return (
        f"https://images.unsplash.com/photo-{slug}"
        f"?auto=format&fit=crop&w={width}&h={height}&q=80"
    )


# Text-free spares (nature, sky, water, architecture): never a keyboard, a
# book spine, a whiteboard, or a screen that would bake a language into a deck.
_FALLBACK_PHOTO_IDS = (
    "1506905925346-21bda4d32df4",
    "1501785888041-af3ef285b470",
    "1433086966358-54859d0ed716",
    "1472214103451-9374bd1c798e",
    "1518837695005-2083093ee35b",
    "1505144808419-1957a94ca61e",
    "1469474968028-56623f02e42e",
    "1500534314209-a25ddb2bd429",
    "1439066615861-d1af74d74000",
    "1476514525535-07fb3b4ae5f1",
)


def _download_still(photo_id: str, getter: Any) -> tuple[str, bytes]:
    url = unsplash_photo_url(photo_id)
    body, content_type = getter(url)
    if body[:3] != b"\xff\xd8\xff" and "jpeg" not in content_type and "jpg" not in content_type:
        raise ValueError(f"{photo_id} is not a jpeg ({content_type})")
    if len(body) < 20_000:
        raise ValueError(f"{photo_id} is too small ({len(body)} bytes)")
    return photo_id, body


def seed_theme_photos(
    theme: str,
    *,
    dest: Path | None = None,
    fetch: Any | None = None,
    force: bool = False,
) -> dict[str, Path]:
    """Download the three stills for one theme into ``photos/``."""
    seeds = THEME_PHOTO_SEEDS.get(theme)
    if not seeds:
        raise ValueError(f"no photo seeds for theme {theme!r}")
    folder = dest or theme_photos_dir(theme)
    if folder is None:
        raise FileNotFoundError(f"unknown PPT theme: {theme}")
    folder.mkdir(parents=True, exist_ok=True)
    getter = fetch or _http_get
    written: dict[str, Path] = {}
    manifest: dict[str, Any] = {"theme": theme, "license": "Unsplash", "slots": {}}
    used = set(seeds.values())
    fallback = 0
    for slot in PHOTO_SLOTS:
        target = folder / f"{slot}.jpg"
        if target.is_file() and target.stat().st_size > 20_000 and not force:
            written[slot] = target
            manifest["slots"][slot] = {"id": seeds[slot], "file": target.name, "cached": True}
            continue
        candidates = [seeds[slot], *_FALLBACK_PHOTO_IDS]
        last_error = "no candidate"
        photo_id = seeds[slot]
        body = b""
        for candidate in candidates:
            if candidate in used and candidate != seeds[slot] and fallback:
                continue
            try:
                photo_id, body = _download_still(candidate, getter)
                used.add(photo_id)
                break
            except Exception as exc:  # noqa: BLE001 - try the next still
                last_error = str(exc)
                fallback += 1
                body = b""
        if not body:
            raise ValueError(f"{theme}/{slot}: {last_error}")
        target.write_bytes(body)
        written[slot] = target
        manifest["slots"][slot] = {
            "id": photo_id,
            "file": target.name,
            "source_url": f"https://unsplash.com/photos/{photo_id}",
            "bytes": len(body),
        }
    (folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return written


def _suffix_for(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for suffix in ALLOWED_IMAGE_SUFFIXES:
        if path.endswith(suffix):
            return ".jpg" if suffix == ".jpeg" else suffix
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    if "png" in content_type:
        return ".png"
    if "webp" in content_type:
        return ".webp"
    if "svg" in content_type:
        return ".svg"
    return ".jpg"


def _http_get(url: str) -> tuple[bytes, str]:
    if blocked_source(url):
        raise ValueError(f"blocked visual source: {url}")
    import httpx

    response = httpx.get(url, timeout=20.0, follow_redirects=True)
    response.raise_for_status()
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
    if content_type and content_type not in ALLOWED_MIME and not content_type.startswith("image/"):
        raise ValueError(f"unexpected content-type {content_type}")
    return response.content, content_type


def _search_stock(provider: str, query: str, *, config: Any | None = None) -> dict[str, Any]:
    from navin.montage.stock import search_stock

    return search_stock(provider, query, kind="image", per_page=8, config=config)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Navin presentation visual assets")
    sub = parser.add_subparsers(dest="cmd", required=True)
    intent = sub.add_parser("intent", help="Print the photo search query for a slide")
    intent.add_argument("--title", default="")
    intent.add_argument("--kind", default="")
    intent.add_argument("--theme", default="")
    icon = sub.add_parser("icon", help="Download a trusted SVG icon")
    icon.add_argument("--name", required=True)
    icon.add_argument("--color", default="")
    icon.add_argument("--brand", action="store_true")
    icon.add_argument("-o", "--out", required=True)
    photo = sub.add_parser("photo", help="Download a trusted stock photo")
    photo.add_argument("--query", required=True)
    photo.add_argument("--theme", default="")
    photo.add_argument("-o", "--out", required=True)
    seed = sub.add_parser("seed-theme", help="Download the 3 stills that ship with a theme")
    seed.add_argument("--theme", default="")
    seed.add_argument("--all", action="store_true")
    seed.add_argument("--force", action="store_true")
    check = sub.add_parser("check-url", help="Exit 1 if the URL is a blocked source")
    check.add_argument("url")
    args = parser.parse_args(argv)
    if args.cmd == "intent":
        print(visual_intent(args.title, kind=args.kind, theme=args.theme))
        return 0
    if args.cmd == "check-url":
        if blocked_source(args.url):
            print("blocked")
            return 1
        print("ok")
        return 0
    if args.cmd == "icon":
        path = resolve_icon(args.name, color=args.color, brand=args.brand, dest=Path(args.out))
        print(path)
        return 0
    if args.cmd == "seed-theme":
        names = list(THEME_PHOTO_SEEDS) if args.all else [args.theme]
        if not names or not names[0]:
            raise SystemExit("seed-theme needs --theme <name> or --all")
        for name in names:
            written = seed_theme_photos(name, force=args.force)
            print(name + ": " + ", ".join(f"{slot}={path}" for slot, path in written.items()))
        return 0
    path = resolve_photo(args.query, dest=Path(args.out), theme=args.theme)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
