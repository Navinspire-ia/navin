# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Word design system: theme tokens, instant re-theming, palette audit.

Every template under ``templates/word`` declares its colours and fonts once, in
a ``<style id="navin-theme">`` block holding nothing but ``--nv-*`` custom
properties, and the rest of its CSS reads them through ``var()``. Restyling a
finished document is then the replacement of that one block: the content, the
structure and the layout never move.

``html2docx`` never reads this CSS. It measures the page through Chromium and
works from computed styles, so a token resolves long before the converter sees
it and DOCX output needs no knowledge of any of this.

The audit exists because the scheme only holds while templates stay honest. One
literal ``#1F3A5F`` left in a rule is invisible until somebody switches theme
and a stray navy heading survives on a claret page.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Mapping

ENGINE_DIRNAME = "_engine"
THEMES_NAME = "themes.json"
THEME_STYLE_ID = "navin-theme"

# Colours a theme is not required to own: pure black and white are structural
# (paper, ink) rather than palette, and rgba() shadows carry no hue.
_NEUTRAL_HEX = {"#000", "#fff", "#000000", "#ffffff"}

_STATUS_DEFAULTS = {
    "high": "#B91C1C",
    "high-soft": "#FEE2E2",
    "mid": "#C2410C",
    "mid-soft": "#FFEDD5",
    "low": "#0369A1",
    "low-soft": "#E0F2FE",
    "good": "#15803D",
    "good-soft": "#DCFCE7",
}

_HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3,8})\b")
_THEME_STYLE_RE = re.compile(
    rf'<style\s+id="{THEME_STYLE_ID}"[^>]*>.*?</style>\s*',
    re.S | re.I,
)
_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
_FIRST_STYLE_RE = re.compile(r"<style\b", re.I)
_BODY_RE = re.compile(r"<body[^>]*>(.*)</body>", re.S | re.I)


def engine_dir() -> Path:
    """Locate ``templates/word/_engine``, in the repo or in a workspace copy."""
    here = Path(__file__).resolve()
    candidates = [
        # Repo checkout, packaged install, and the copy that materialize_converters
        # drops in <workspace>/.navin/resources/tools next to the materialized
        # templates. The last one is the path that matters at agent runtime.
        here.parents[2] / "templates" / "word" / ENGINE_DIRNAME,
        here.parents[1] / "resources" / "templates" / "word" / ENGINE_DIRNAME,
        here.parents[1] / "document-templates" / "word" / ENGINE_DIRNAME,
        Path.cwd() / "document-templates" / "word" / ENGINE_DIRNAME,
        Path.cwd() / ".navin" / "resources" / "document-templates" / "word" / ENGINE_DIRNAME,
    ]
    candidates.extend(
        parent / "templates" / "word" / ENGINE_DIRNAME for parent in here.parents
    )
    for candidate in candidates:
        if (candidate / THEMES_NAME).is_file():
            return candidate
    raise FileNotFoundError("templates/word/_engine is missing")


def load_themes() -> dict[str, Any]:
    data = json.loads((engine_dir() / THEMES_NAME).read_text(encoding="utf-8"))
    themes = data.get("themes")
    if not isinstance(themes, dict) or not themes:
        raise ValueError("themes.json declares no theme")
    return themes


def theme_names() -> list[str]:
    return sorted(load_themes())


def load_theme(name: str) -> dict[str, Any]:
    themes = load_themes()
    theme = themes.get(name)
    if not isinstance(theme, dict):
        known = ", ".join(sorted(themes))
        raise KeyError(f"unknown word theme: {name} (known: {known})")
    return theme


def theme_css(theme: Mapping[str, Any]) -> str:
    """Render one theme as the ``:root`` block templates consume."""
    fonts = theme.get("fonts") or {}
    colors = theme.get("colors") or {}
    space = theme.get("space") or {}
    accent = colors.get("accent", "#1F3A5F")
    values = {
        "font-heading": fonts.get("heading", "Georgia"),
        "font-body": fonts.get("body", "Calibri"),
        "font-mono": fonts.get("mono", "Consolas"),
        "bg": colors.get("bg", "#E9EBEE"),
        "surface": colors.get("surface", "#FFFFFF"),
        "text": colors.get("text", "#3A4358"),
        "heading": colors.get("heading", accent),
        "muted": colors.get("muted", "#7A8299"),
        "accent": accent,
        "accent-2": colors.get("accent2", accent),
        # A secondary accent is often chosen to sit on a fill (gold on navy,
        # cyan on slate) where it never has to be read as small text. Where it
        # does, the theme names a darker variant, because a 10px gold caption
        # on white is decoration rather than text.
        "accent-2-ink": colors.get("accent2Ink", colors.get("accent2", accent)),
        "border": colors.get("border", "#E3E7EF"),
        "subtle": colors.get("subtle", "#F7F9FC"),
        "soft": colors.get("soft", "#F5F7FA"),
        "on-accent": colors.get("onAccent", "#FFFFFF"),
        "page-margin": space.get("margin", "22mm 20mm"),
        "radius": theme.get("radius", "0px"),
    }
    # Status colours carry meaning rather than style: a risk stays red and a
    # target stays green whatever the palette, so they default across themes
    # and a theme only overrides them when its own palette would clash.
    status = theme.get("status") or {}
    for key, fallback in _STATUS_DEFAULTS.items():
        values[f"status-{key}"] = status.get(key, fallback)
    body = "\n".join(f"    --nv-{name}: {value};" for name, value in values.items())
    return f"  :root {{\n{body}\n  }}\n"


def theme_style_block(name: str) -> str:
    """The full ``<style id="navin-theme">`` element for a theme."""
    theme = load_theme(name)
    return (
        f'<style id="{THEME_STYLE_ID}" data-theme="{name}">\n'
        f"{theme_css(theme)}"
        "</style>\n"
    )


def apply_theme(html: str, name: str) -> str:
    """Swap a document onto another theme, leaving its content untouched."""
    block = theme_style_block(name)
    replaced, count = _THEME_STYLE_RE.subn(block, html, count=1)
    if count:
        return replaced
    # A template that has never been themed: the block goes in front of the
    # component CSS, so its var() lookups resolve.
    match = _FIRST_STYLE_RE.search(html)
    if not match:
        raise ValueError("document has no <style> block to theme")
    return html[: match.start()] + block + html[match.start() :]


def current_theme(html: str) -> str | None:
    match = re.search(
        rf'<style\s+id="{THEME_STYLE_ID}"[^>]*\bdata-theme="([^"]+)"',
        html,
        re.I,
    )
    return match.group(1) if match else None


def stray_colors(html: str) -> list[str]:
    """Literal colours that would survive a theme change.

    Only the theme block may name a colour. Anything else - a rule in the
    component CSS, an inline ``style`` attribute - stays put when the theme
    changes and breaks the document in a way that is hard to see and easy to
    ship.
    """
    without_theme = _THEME_STYLE_RE.sub("", html)
    found: list[str] = []
    for css in _STYLE_RE.findall(without_theme):
        found.extend(_HEX_RE.findall(css))
    body = _BODY_RE.search(without_theme)
    if body:
        found.extend(_HEX_RE.findall(body.group(1)))
    seen: dict[str, None] = {}
    for item in found:
        lowered = item.lower()
        if lowered not in _NEUTRAL_HEX:
            seen.setdefault(lowered, None)
    return list(seen)


def audit(html: str) -> dict[str, Any]:
    """Report whether a document can actually change theme."""
    themed = _THEME_STYLE_RE.search(html) is not None
    stray = stray_colors(html)
    return {
        "themed": themed,
        "theme": current_theme(html),
        "stray_colors": stray,
        "pass": themed and not stray,
    }


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Navin Word design system")
    sub = parser.add_subparsers(dest="cmd", required=True)

    listed = sub.add_parser("themes", help="Print the available theme names")
    listed.add_argument("--json", action="store_true")

    css = sub.add_parser("theme-css", help="Print one theme's :root block")
    css.add_argument("--theme", required=True)

    applied = sub.add_parser("apply", help="Switch a document onto another theme")
    applied.add_argument("file")
    applied.add_argument("--theme", required=True)
    applied.add_argument("-o", "--out", help="Defaults to editing the file in place")

    checked = sub.add_parser("audit", help="Check a document is fully tokenized")
    checked.add_argument("file")

    rendered = sub.add_parser(
        "render",
        help="Write a finished document from semantic JSON. No leftover sample.",
    )
    rendered.add_argument("--document", required=True, help="Semantic document JSON")
    rendered.add_argument("--theme", default="")
    rendered.add_argument("-o", "--out", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "themes":
        themes = load_themes()
        if args.json:
            print(json.dumps(themes, indent=2, ensure_ascii=False))
        else:
            for name in sorted(themes):
                print(f"{name:15} {themes[name].get('character', '')}")
        return 0

    if args.cmd == "theme-css":
        print(theme_style_block(args.theme), end="")
        return 0

    if args.cmd == "apply":
        path = Path(args.file)
        out = Path(args.out) if args.out else path
        out.write_text(apply_theme(_read(path), args.theme), encoding="utf-8")
        print(out)
        return 0

    if args.cmd == "render":
        from navin.documents import word_render

        payload = word_render.load_document_json(Path(args.document))
        html = word_render.render_document(payload, theme=args.theme or None)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(out)
        return 0

    report = audit(_read(Path(args.file)))
    print(json.dumps(report, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
