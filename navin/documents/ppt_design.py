# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Presentation design system: tokens, layouts, Visual Director, materialize.

The LLM emits a semantic slide. This module picks a layout and a component
variant, then writes a self-contained 1920x1080 HTML file whose colors come
only from the selected theme's ``design-system.json``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from navin.documents import ppt_assets, ppt_render

ENGINE_DIR = Path(__file__).resolve().parents[2] / "templates" / "ppt" / "_engine"
THEMES_DIR = ENGINE_DIR.parent
CATALOG_NAME = "catalog.json"
QUALITY_NAME = "quality.json"
THEMES_NAME = "themes.json"

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3,8})")
_LAYOUT_SHELL = """\
<!DOCTYPE html>
<html lang="und" data-language-mode="dynamic" data-layout="{layout}">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
{css}
</style>
<style id="navin-dynamic-language">
  html[dir="rtl"] body {{ direction: rtl; }}
  html[dir="rtl"] p, html[dir="rtl"] li, html[dir="rtl"] td,
  html[dir="rtl"] th, html[dir="rtl"] label {{ direction: rtl; text-align: right; }}
</style>
</head>
<body data-localize="all-visible-text">
{body}
</body>
</html>
"""


def engine_dir() -> Path:
    candidates = [
        ENGINE_DIR,
        Path.cwd() / "document-templates" / "ppt" / "_engine",
        Path.cwd() / ".navin" / "resources" / "document-templates" / "ppt" / "_engine",
    ]
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidates.append(parent / "templates" / "ppt" / "_engine")
    # A packaged build keeps the library where templates_root() says (bundle,
    # resources folder); the script may run from a workspace copy.
    try:
        from navin.utils.document_templates import templates_root

        root = templates_root()
        if root is not None:
            candidates.append(root / "ppt" / "_engine")
    except Exception:  # pragma: no cover - plain-folder execution without navin
        pass
    for candidate in candidates:
        if candidate.is_dir() and (candidate / CATALOG_NAME).is_file():
            return candidate
    raise FileNotFoundError("templates/ppt/_engine is missing")


def load_catalog() -> dict[str, Any]:
    return json.loads((engine_dir() / CATALOG_NAME).read_text(encoding="utf-8"))


def load_quality() -> dict[str, Any]:
    return json.loads((engine_dir() / QUALITY_NAME).read_text(encoding="utf-8"))


def load_theme_catalog() -> dict[str, Any]:
    path = engine_dir() / THEMES_NAME
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list):
        raise ValueError("invalid PPT themes.json")
    return data


def theme_names() -> list[str]:
    """Official PPT themes that still have a folder on disk."""
    root = engine_dir().parent
    names: list[str] = []
    for name in load_theme_catalog()["themes"]:
        folder = root / str(name)
        if folder.is_dir() and (folder / "design-system.json").is_file():
            names.append(str(name))
    return names


def resolve_theme(name: str) -> str:
    """Map a retired picker name onto the theme that replaced it."""
    raw = (name or "").strip()
    catalog = load_theme_catalog()
    aliases = catalog.get("aliases") if isinstance(catalog.get("aliases"), dict) else {}
    resolved = str(aliases.get(raw, raw))
    return resolved


def theme_dir(name: str) -> Path:
    resolved = resolve_theme(name)
    path = engine_dir().parent / resolved
    if not path.is_dir():
        raise FileNotFoundError(f"unknown PPT theme: {name}")
    return path


def load_theme(name: str) -> dict[str, Any]:
    resolved = resolve_theme(name)
    path = theme_dir(resolved) / "design-system.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data.get("colors"):
        raise ValueError(f"invalid design-system.json for {resolved}")
    return data


def _rgb(value: str | None) -> tuple[float, float, float] | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _luminance(rgb: tuple[float, float, float]) -> float:
    channels = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def readable_ink(accent: str, *surfaces: str | None, target: float = 4.5) -> str:
    """The accent, moved just far enough from the theme's surfaces to be read.

    Hue and saturation are kept, only lightness travels, and it travels away
    from the background: the ink stays recognisably the brand colour instead of
    becoming a generic grey. A colour that already clears the target comes back
    untouched.
    """
    import colorsys

    ink = _rgb(accent)
    backgrounds = [found for found in (_rgb(value) for value in surfaces) if found]
    if ink is None or not backgrounds:
        return accent
    if all(_contrast(ink, background) >= target for background in backgrounds):
        return accent
    hue, lightness, saturation = colorsys.rgb_to_hls(*ink)

    def weakest(rgb: tuple[float, float, float]) -> float:
        return min(_contrast(rgb, background) for background in backgrounds)

    # Both directions are tried and the best kept, because an accent can sit
    # between two surfaces where only one way out clears the target. A vivid
    # colour caught between a pale page and a dark card cannot get there on
    # lightness alone, so saturation gives way next - a duller version of the
    # brand colour still reads as the brand, an unreadable one reads as
    # nothing. The original competes throughout, so the result is never worse
    # than what it replaces.
    best, best_ratio = ink, weakest(ink)
    for strength in (1.0, 0.6, 0.3):
        for direction in (1, -1):
            for step in range(1, 101):
                moved = lightness + direction * step / 100
                if not 0 <= moved <= 1:
                    break
                candidate = colorsys.hls_to_rgb(hue, moved, saturation * strength)
                ratio = weakest(candidate)
                if ratio > best_ratio:
                    best, best_ratio = candidate, ratio
                if ratio >= target:
                    break
            if best_ratio >= target:
                break
        if best_ratio >= target:
            break
    return "#" + "".join(f"{int(round(channel * 255)):02X}" for channel in best)


def on_surface(text: str, surface: str, *, target: float = 4.5) -> str:
    """The ink a card needs: the page text when it reads there, else plain ink.

    Black or white rather than a tinted grey, because a card is where a deck
    puts the details somebody has to read.
    """
    ink, paper = _rgb(text), _rgb(surface)
    if ink is None or paper is None:
        return text
    if _contrast(ink, paper) >= target:
        return text
    # Measured rather than guessed from a lightness threshold: a mid grey card
    # is brighter than it looks and takes dark ink, not white.
    black, white = _rgb("#111111"), _rgb("#FFFFFF")
    return "#111111" if _contrast(black, paper) >= _contrast(white, paper) else "#FFFFFF"


def muted_ink(ink: str, surface: str, *, target: float = 4.6) -> str:
    """Secondary copy for a card: the card's ink, softened but still readable.

    It walks toward the surface and stops at the last step that still clears
    the target, so the text reads as secondary without becoming a rumour.
    """
    import colorsys

    start, paper = _rgb(ink), _rgb(surface)
    if start is None or paper is None:
        return ink
    hue, lightness, saturation = colorsys.rgb_to_hls(*start)
    toward = 1 if _luminance(paper) > _luminance(start) else -1
    best = start
    for step in range(1, 60):
        moved = lightness + toward * step / 100
        if not 0 <= moved <= 1:
            break
        candidate = colorsys.hls_to_rgb(hue, moved, saturation)
        if _contrast(candidate, paper) < target:
            break
        best = candidate
    return "#" + "".join(f"{int(round(channel * 255)):02X}" for channel in best)


def theme_css(tokens: Mapping[str, Any]) -> str:
    fonts = tokens.get("fonts") or {}
    colors = tokens.get("colors") or {}
    space = tokens.get("space") or {}
    motion = tokens.get("motion") or {}
    import_url = fonts.get("import") or ""
    lines = []
    if import_url:
        lines.append(f"@import url('{import_url}');")
    lines.append(":root {")
    lines.append(f"  --nv-font-heading: {fonts.get('heading', 'Inter')};")
    lines.append(f"  --nv-font-body: {fonts.get('body', 'Inter')};")
    lines.append(f"  --nv-font-mono: {fonts.get('mono', 'ui-monospace')};")
    lines.append(f"  --nv-bg: {colors.get('bg', '#111')};")
    surface = colors.get("surface", "#222")
    text = colors.get("text", "#fff")
    lines.append(f"  --nv-surface: {surface};")
    lines.append(f"  --nv-text: {text};")
    # The ink for anything painted on a card. A theme can perfectly well put a
    # white page text on a coloured background and still want white cards, and
    # then --nv-text on --nv-surface is white on white.
    ink = colors.get("onSurface") or on_surface(text, surface)
    lines.append(f"  --nv-on-surface: {ink};")
    # Secondary copy inside a card. --nv-muted answers to the page background,
    # so a card that inverts it (white card, dark page) would carry secondary
    # text at 1.2:1. Card components rebind --nv-muted to this.
    lines.append(f"  --nv-on-surface-muted: {colors.get('onSurfaceMuted') or muted_ink(ink, surface)};")
    lines.append(f"  --nv-muted: {colors.get('muted', '#999')};")
    accent = colors.get("accent", "#fff")
    lines.append(f"  --nv-accent: {accent};")
    lines.append(f"  --nv-accent-2: {colors.get('accent2', accent)};")
    # An accent is picked to be seen, not to be read: a vivid blue that carries
    # a filled band beautifully lands at 4.3:1 as an 18px kicker. The ink
    # variant is the same colour taken far enough from its background to be
    # read at small sizes. There are two, because a colour caught between a
    # pale page and a dark card cannot read on both: each ground gets its own,
    # and card components rebind the token.
    lines.append(f"  --nv-accent-ink: {colors.get('accentInk') or readable_ink(accent, colors.get('bg'))};")
    lines.append(
        f"  --nv-accent-ink-surface: "
        f"{colors.get('accentInkSurface') or readable_ink(accent, colors.get('surface'))};"
    )
    # An inverted slide (nv-tone-dark) paints on --nv-text: the accent needs
    # an ink that reads on that paper too.
    lines.append(
        f"  --nv-accent-ink-inverted: "
        f"{colors.get('accentInkInverted') or readable_ink(accent, text)};"
    )
    lines.append(f"  --nv-border: {colors.get('border', 'transparent')};")
    lines.append(
        f"  --nv-on-accent: {colors.get('onAccent') or on_surface('#FFFFFF', accent)};"
    )
    lines.append(f"  --nv-margin: {int(space.get('margin', 90))}px;")
    lines.append(f"  --nv-gap: {int(space.get('gap', 24))}px;")
    lines.append(f"  --nv-radius: {tokens.get('radius', '16px')};")
    lines.append(f"  --nv-shadow: {tokens.get('shadow', 'none')};")
    lines.append(f"  --nv-duration: {motion.get('duration', '420ms')};")
    lines.append(f"  --nv-ease: {motion.get('easing', 'cubic-bezier(.2,.7,.2,1)')};")
    lines.append("}")
    chrome = tokens.get("chrome")
    if isinstance(chrome, str) and chrome.strip():
        lines.append(chrome.strip())
    return "\n".join(lines) + "\n"


def layout_ids() -> list[str]:
    return [str(item["id"]) for item in load_catalog()["layouts"]]


_POSITION_LAYOUT = {
    "left": "image-text",
    "right": "text-image",
    "full": "full-image",
    "background": "full-bleed-hero",
    "top": "hero",
}

_TYPE_LAYOUT = {
    "image": "text-image",
    "chart": "chart",
    "table": "table",
    "kpi": "kpi",
    "process": "process",
    "cycle": "cycle",
    "timeline": "timeline",
    "roadmap": "roadmap",
    "funnel": "funnel",
    "hierarchy": "hierarchy",
    "matrix": "matrix",
    "map": "map",
    "architecture": "architecture",
    "comparison": "comparison",
    "quote": "quote",
    "people": "team",
    "product": "product-hero",
    "logos": "feature-grid",
    "list": "cards",
}


def choose_visual(kind: str, catalog: Mapping[str, Any] | None = None) -> list[str]:
    """Layouts the Visual Director prefers for a content kind."""
    data = catalog or load_catalog()
    mapping = data.get("visualDirector") or {}
    found = mapping.get(kind)
    if isinstance(found, list) and found:
        return [str(item) for item in found]
    return ["text"]


def choose_layout(
    kind: str = "",
    visual: Mapping[str, Any] | None = None,
) -> str:
    """Pick one master layout from a content kind and a semantic visual."""
    visual = visual or {}
    position = visual.get("position")
    if position in _POSITION_LAYOUT:
        return _POSITION_LAYOUT[position]
    visual_type = visual.get("type")
    if visual_type in _TYPE_LAYOUT:
        return _TYPE_LAYOUT[visual_type]
    return choose_visual(kind or "abstract")[0]


# Names a model writes for a layout that the catalog calls otherwise. Resolved
# before the catalog lookup so a deck never dies on "bullets" or "title".
_LAYOUT_ALIASES = {
    "title": "cover",
    "title-slide": "cover",
    "titre": "cover",
    "intro": "cover",
    "bullets": "text",
    "bullet": "text",
    "bullet-points": "text",
    "list": "cards",
    "liste": "cards",
    "content": "text",
    "body": "text",
    "paragraph": "text",
    "two-column": "comparison",
    "two-columns": "comparison",
    "columns": "comparison",
    "split": "text-image",
    "image": "full-image",
    "photo": "full-image",
    "picture": "full-image",
    "image-left": "image-text",
    "image-right": "text-image",
    "stats": "big-numbers",
    "numbers": "big-numbers",
    "metrics": "kpi",
    "kpis": "kpi",
    "graph": "chart",
    "graphique": "chart",
    "bar-chart": "chart",
    "line-chart": "chart",
    "pie-chart": "chart",
    "data": "data-story",
    "steps": "process",
    "etapes": "process",
    "schedule": "timeline",
    "calendar": "timeline",
    "plan": "roadmap",
    "org": "hierarchy",
    "org-chart": "hierarchy",
    "team-slide": "team",
    "people": "team",
    "testimonial": "quote",
    "citation": "quote",
    "summary": "statement",
    "key-message": "statement",
    "takeaway": "statement",
    "conclusion": "closing",
    "end": "closing",
    "thanks": "closing",
    "thank-you": "closing",
    "merci": "closing",
    "qa": "closing",
    "questions": "closing",
    "section": "section-break",
    "divider": "section-break",
    "chapter": "section-break",
    "sommaire": "agenda",
    "outline": "agenda",
    "toc": "agenda",
    "grid": "feature-grid",
    "features": "feature-grid",
    "icons": "feature-grid",
    "diagram": "architecture",
    "flow": "process",
    "swot": "matrix",
    "case": "case-study",
    "pricing-table": "pricing",
    "offer": "pricing",
    "images": "gallery",
    "mosaic": "gallery",
}


def resolve_layout(slide: Mapping[str, Any], catalog: Mapping[str, Any] | None = None) -> tuple[str, str | None]:
    """The catalog layout id for a slide, plus a note when the name was mended.

    A known id passes as is. An alias is mapped. Anything else falls back to the
    layout the content asks for (chart, table, items...), with a note the
    caller prints so the deck JSON can be fixed; the slide is still rendered.
    """
    data = catalog or load_catalog()
    known = {str(item["id"]) for item in data["layouts"]}
    raw = str(slide.get("layout") or "").strip()
    wanted = re.sub(r"[\s_]+", "-", raw.lower())
    if wanted in known:
        return wanted, None
    if wanted in _LAYOUT_ALIASES:
        return _LAYOUT_ALIASES[wanted], None
    for candidate in (wanted.rstrip("s"), wanted.replace("-slide", ""), wanted.split("-")[0]):
        if candidate in known:
            return candidate, None
        if candidate in _LAYOUT_ALIASES:
            return _LAYOUT_ALIASES[candidate], None
    if isinstance(slide.get("chart"), dict) or slide.get("series"):
        chosen = "chart"
    elif slide.get("table"):
        chosen = "table"
    elif slide.get("image") or slide.get("images"):
        chosen = "text-image"
    elif slide.get("items") or slide.get("bullets") or slide.get("points"):
        chosen = "cards" if len(slide.get("items") or slide.get("bullets") or slide.get("points") or []) <= 4 else "text"
    else:
        chosen = choose_layout(
            str(slide.get("kind") or ""),
            slide.get("visual") if isinstance(slide.get("visual"), dict) else None,
        )
    note = (
        f"unknown layout '{raw}': rendered as '{chosen}' (run `ppt_design layouts` for the ids)"
        if raw
        else None
    )
    return chosen, note


def canonical_layouts(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve every slide's layout name once, before the deck passes run.

    The outline, the stagecraft, the rhythm and the critique all read
    ``layout``; feeding them an alias would make the critique miss the cover it
    is looking at. Notes for mended names go to stderr, one per slide.
    """
    catalog = load_catalog()
    out: list[dict[str, Any]] = []
    for index, slide in enumerate(slides, 1):
        if not isinstance(slide, Mapping):
            continue
        payload = dict(slide)
        if payload.get("layout"):
            layout_id, note = resolve_layout(payload, catalog)
            if note:
                print(f"ppt_design: slide {index}: {note}", file=sys.stderr)
            payload["layout"] = layout_id
        out.append(payload)
    return out


_TITLE_RE = re.compile(r'(<h1 class="nv-title">)(.*?)(</h1>)', re.S)
_KICKER_RE = re.compile(r'(<div class="nv-kicker">)(.*?)(</div>)', re.S)
_BODY_RE = re.compile(r'(<p class="nv-body">)(.*?)(</p>)', re.S)
_SUB_RE = re.compile(r'(<p class="nv-sub">)(.*?)(</p>)', re.S)
_IMG_RE = re.compile(r'(<img\b[^>]*\bsrc=")[^"]*(")', re.I)


def fill_slide(
    html: str,
    *,
    title: str | None = None,
    kicker: str | None = None,
    body: str | None = None,
    image: str | None = None,
) -> str:
    """Legacy regex fill. Prefer :func:`render_slide` so sample copy cannot stay."""
    if title:
        html = _TITLE_RE.sub(rf"\1{title}\3", html, count=1)
    if kicker:
        html = _KICKER_RE.sub(rf"\1{kicker}\3", html, count=1)
    if body:
        replaced, n = _BODY_RE.subn(rf"\1{body}\3", html, count=1)
        html = replaced if n else _SUB_RE.sub(rf"\1{body}\3", html, count=1)
    if image:
        html = _IMG_RE.sub(rf"\1{image}\2", html, count=1)
    return html


def _has_semantic_content(
    *,
    title: str | None,
    kicker: str | None,
    body: str | None,
    image: str | None,
    slide: Mapping[str, Any] | None,
) -> bool:
    if slide:
        return True
    return any(value for value in (title, kicker, body, image))


def render_slide(
    theme: str,
    slide: Mapping[str, Any],
    *,
    dest_dir: Path | None = None,
) -> str:
    """Build a finished themed slide from semantic JSON. No leftover sample."""
    catalog = load_catalog()
    titles = {item["id"]: item["title"] for item in catalog["layouts"]}
    layout_id, note = resolve_layout(slide, catalog)
    if note:
        print(f"ppt_design: {note}", file=sys.stderr)
    payload = ppt_render.enrich_slide({**dict(slide), "layout": layout_id})
    payload["layout"] = str(payload.get("layout") or layout_id)
    payload = ppt_assets.attach_visual(payload, theme=theme, dest_dir=dest_dir)
    layout_id = str(payload.get("layout") or layout_id)
    if layout_id not in titles:
        raise ValueError(f"unknown layout: {layout_id}")
    tokens = load_theme(theme)
    engine_css = (engine_dir() / "navin-ppt.css").read_text(encoding="utf-8")
    css = theme_css(tokens) + "\n" + engine_css
    return _LAYOUT_SHELL.format(
        layout=layout_id,
        title=html_escape(str(payload.get("title") or titles[layout_id])),
        css=css,
        body=ppt_render.render_body(payload),
    )


def html_escape(value: str) -> str:
    from html import escape

    return escape(value, quote=True)


def _read_layout_body(layout_id: str) -> str:
    path = engine_dir() / "layouts" / f"{layout_id}.html"
    if not path.is_file():
        raise FileNotFoundError(f"missing master layout: {layout_id}")
    html = path.read_text(encoding="utf-8")
    match = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    if not match:
        raise ValueError(f"layout {layout_id} has no body")
    return match.group(1).strip()


def materialize(
    theme: str,
    layout_id: str,
    *,
    title: str | None = None,
    kicker: str | None = None,
    body: str | None = None,
    image: str | None = None,
    slide: Mapping[str, Any] | None = None,
) -> str:
    """Return a self-contained HTML slide for ``theme`` + ``layout_id``.

    With semantic content the body is built from scratch so lookbook sample
    copy and ``image.png`` cannot leak into the deck. Without content this
    still returns the lookbook master, for the picker and for QA of the
    empty shell.
    """
    if _has_semantic_content(
        title=title, kicker=kicker, body=body, image=image, slide=slide
    ):
        payload: dict[str, Any] = dict(slide or {})
        payload.setdefault("layout", layout_id)
        if title:
            payload["title"] = title
        if kicker:
            payload["kicker"] = kicker
        if body:
            payload.setdefault("body", body)
        if image:
            payload.setdefault("image", image)
        return render_slide(theme, payload)
    catalog = load_catalog()
    titles = {item["id"]: item["title"] for item in catalog["layouts"]}
    if layout_id not in titles:
        raise ValueError(f"unknown layout: {layout_id}")
    tokens = load_theme(theme)
    engine_css = (engine_dir() / "navin-ppt.css").read_text(encoding="utf-8")
    css = theme_css(tokens) + "\n" + engine_css
    body_html = _read_layout_body(layout_id)
    return _LAYOUT_SHELL.format(
        layout=layout_id,
        title=titles[layout_id],
        css=css,
        body=body_html,
    )


def token_hexes(tokens: Mapping[str, Any]) -> set[str]:
    colors = tokens.get("colors") or {}
    found: set[str] = set()
    for value in colors.values():
        if isinstance(value, str):
            found.update(item.lower() for item in _HEX_RE.findall(value))
    return found


_ONE_IDEA_LAYOUTS = {
    "cover",
    "hero",
    "statement",
    "section-break",
    "closing",
    "quote",
    "full-image",
    "full-bleed-hero",
}
_RICH_MARKERS = (
    "nv-item",
    "nv-step",
    "nv-stat",
    "nv-img",
    "nv-panel",
    "nv-chart",
    "nv-bar",
    "nv-card",
    "nv-node",
    "nv-cell",
    "nv-pin",
    "nv-compare",
)


def _is_thin_slide(html: str) -> bool:
    """A content slide that is still just a title and one line."""
    match = re.search(r'data-layout="([^"]+)"', html)
    layout = match.group(1) if match else ""
    if layout in _ONE_IDEA_LAYOUTS:
        return False
    if not any(marker in html for marker in _RICH_MARKERS):
        return True
    blocks = sum(html.count(f'class="{name}"') + html.count(f'class="{name} ') for name in (
        "nv-item",
        "nv-step",
        "nv-stat",
        "nv-card",
        "nv-cell",
        "nv-node",
    ))
    has_photo_or_chart = any(
        name in html for name in ("nv-img", "nv-chart", "nv-bar", "nv-shot")
    )
    if has_photo_or_chart:
        return False
    has_copy = bool(
        re.search(r'class="nv-body"|class="nv-item"|class="nv-step"|class="nv-stat"', html)
    )
    has_panel = "nv-panel" in html
    if has_panel and has_copy:
        return False
    if has_panel and not has_copy:
        return True
    return blocks < 2


def quality_check(html: str, tokens: Mapping[str, Any]) -> dict[str, Any]:
    """Cheap structural QA. Chromium still owns overflow/contrast at convert time."""
    quality = load_quality()
    failures: list[str] = []
    sample = ("lorem ipsum", "title here", "xx%", "placeholder")
    lowered = html.lower()
    if any(marker in lowered for marker in sample):
        failures.append("empty")
    filled = 'data-filled="1"' in html
    if filled and ppt_render.leftover_sample_phrases(html):
        failures.append("empty")
    if filled and ppt_render.has_placeholder_image(html):
        failures.append("empty")
    title_hits = _TITLE_RE.findall(html)
    if len(title_hits) > 1:
        failures.append("duplicate-title")
    allowed = token_hexes(tokens)
    body = re.search(r"<body[^>]*>(.*)</body>", html, re.S | re.I)
    body_html = body.group(1) if body else html
    stray = {
        item.lower()
        for item in _HEX_RE.findall(body_html)
        if item.lower() not in allowed
        and item.lower() not in {"#000", "#fff", "#ffffff", "#000000"}
    }
    if stray:
        failures.append("theme-colors")
    if filled and _is_thin_slide(html):
        failures.append("thin-slide")
    score = 100 - 12 * len(failures)
    threshold = int(quality.get("threshold") or 85)
    hard = {"empty", "overflow", "theme-colors", "thin-slide"}
    passed = score >= threshold and not (hard & set(failures))
    return {
        "score": max(0, score),
        "pass": passed,
        "failures": failures,
        "threshold": threshold,
    }


def write_theme_css(name: str) -> Path:
    tokens = load_theme(name)
    path = theme_dir(name) / "theme.css"
    path.write_text(theme_css(tokens), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Navin PPT design system")
    sub = parser.add_subparsers(dest="cmd", required=True)
    mat = sub.add_parser("materialize", help="Write one themed master slide")
    mat.add_argument("--theme", required=True)
    mat.add_argument("--layout", default="")
    mat.add_argument("-o", "--out", required=True)
    mat.add_argument("--title")
    mat.add_argument("--kicker")
    mat.add_argument("--body")
    mat.add_argument("--image")
    mat.add_argument(
        "--slide",
        help="Semantic slide JSON. Builds the body from scratch. No leftover sample.",
    )
    listed = sub.add_parser("layouts", help="Print layout ids")
    listed.add_argument("--json", action="store_true")
    css = sub.add_parser("theme-css", help="Rewrite theme.css from design-system.json")
    css.add_argument("--theme", required=True)
    deck = sub.add_parser("deck", help="Write every slide of a semantic deck JSON")
    deck.add_argument("--theme", default="")
    deck.add_argument("--slides", required=True, help="JSON object {theme, slides} or an array")
    deck.add_argument("-o", "--out", required=True, help="Folder for slide_01.html ...")
    notes = sub.add_parser("notes", help="Write speaker notes into a converted PPTX")
    notes.add_argument("--dir", required=True, help="Folder of slide_XX.html (data-notes)")
    notes.add_argument("--pptx", required=True, help="Editable PPTX to annotate")
    args = parser.parse_args(argv)
    if args.cmd == "notes":
        written = ppt_render.apply_speaker_notes(
            Path(args.pptx),
            ppt_render.notes_from_html_dir(Path(args.dir)),
        )
        print(written)
        return 0
    if args.cmd == "layouts":
        ids = layout_ids()
        print(json.dumps(ids) if args.json else "\n".join(ids))
        return 0
    if args.cmd == "theme-css":
        print(write_theme_css(args.theme))
        return 0
    if args.cmd == "deck":
        theme, slides, meta = ppt_render.load_deck_json(Path(args.slides))
        theme = args.theme or theme
        if not theme:
            raise SystemExit("deck needs --theme or a theme field in the JSON")
        slides = canonical_layouts(slides)
        slides = ppt_render.ensure_outline(
            slides,
            language=meta.get("language") or "",
            outline_title=meta.get("outline_title") or "",
        )
        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        photos_dir = out_dir / "photos"
        slides = [
            ppt_assets.attach_visual(
                ppt_render.enrich_slide(slide),
                theme=theme,
                dest_dir=photos_dir,
            )
            for slide in slides
        ]
        slides = ppt_render.ensure_stagecraft(
            slides,
            language=str(meta.get("language") or ""),
        )
        slides = ppt_render.ensure_rhythm(slides)
        written: list[str] = []
        tokens = load_theme(theme)
        slide_failures: list[str] = []
        for index, slide in enumerate(slides, 1):
            path = out_dir / f"slide_{index:02d}.html"
            html = render_slide(theme, slide, dest_dir=photos_dir)
            path.write_text(html, encoding="utf-8")
            written.append(str(path))
            qa = quality_check(html, tokens)
            if not qa["pass"]:
                slide_failures.append(
                    f"slide_{index:02d}: " + ", ".join(qa["failures"])
                )
        notes = [str(slide.get("notes") or "") for slide in slides]
        preview = ppt_render.write_swipe_preview(
            out_dir,
            title=str((slides[0].get("title") if slides else "") or "Deck preview"),
            notes=notes,
        )
        (out_dir / "notes.json").write_text(
            json.dumps(notes, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        critique = ppt_render.critique_deck(slides)
        if slide_failures:
            critique["pass"] = False
            critique["notes"] = list(critique.get("notes") or []) + slide_failures
        critique_path = out_dir / "critique.json"
        critique_path.write_text(
            json.dumps(critique, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("\n".join(written + [str(preview), str(critique_path)]))
        if not critique["pass"]:
            print(
                f"critique {critique['score']}/100 below {critique['threshold']}",
                file=sys.stderr,
            )
            if slide_failures:
                print("\n".join(slide_failures), file=sys.stderr)
            return 1
        return 0
    slide = ppt_render.load_slide_json(Path(args.slide)) if args.slide else None
    layout_id = args.layout or (slide or {}).get("layout") or ""
    if not layout_id:
        raise SystemExit("materialize needs --layout or a layout field in --slide")
    html = materialize(
        args.theme,
        str(layout_id),
        title=args.title,
        kicker=args.kicker,
        body=args.body,
        image=args.image,
        slide=slide,
    )
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
