# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Brand kit: the contract every motion template reads.

JSON shape (shared with the other marketing agents)::

    {
      "name": "Acme",
      "logo_path": "/abs/or/relative/logo.png",
      "logo_dark_path": "logo-on-dark.png",          # optional
      "colors": {"primary": "#6366F1", "secondary": "#22D3EE",
                 "accent": "#F472B6", "background": "#0A0B14",
                 "text": "#F5F7FF"},
      "fonts": {"heading": "Inter", "body": "Inter"},
      "font_files": ["fonts/Inter-Bold.ttf"],         # optional
      "tagline": "Ship faster",                       # optional
      "url": "acme.com"                               # optional
    }

Templates receive it as CSS variables (``--c-primary``, ``--c-secondary``,
``--c-accent``, ``--c-bg``, ``--c-text``, ``--font-heading``,
``--font-body``) plus ``NavinMotion.brand`` in JavaScript. Font files are
registered with the FontFace API from loopback, never from a CDN.

A kit is passed explicitly (dict or JSON path) or built from the marketing desk
store (read only) with :func:`brand_kit_from_marketing_store`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

COLOR_KEYS = ("primary", "secondary", "accent", "background", "text")

DEFAULT_COLORS: dict[str, str] = {
    "primary": "#6366F1",
    "secondary": "#22D3EE",
    "accent": "#F472B6",
    "background": "#0A0B14",
    "text": "#F5F7FF",
}

FONT_SUFFIXES = frozenset({".ttf", ".otf", ".woff", ".woff2"})
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".avif"})

_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_FUNC = re.compile(r"^(rgb|rgba|hsl|hsla)\([0-9.,%\s/]+\)$")

_WEIGHTS = (
    ("extralight", 200),
    ("ultralight", 200),
    ("semibold", 600),
    ("demibold", 600),
    ("extrabold", 800),
    ("ultrabold", 800),
    ("thin", 100),
    ("light", 300),
    ("regular", 400),
    ("book", 400),
    ("medium", 500),
    ("bold", 700),
    ("black", 900),
    ("heavy", 900),
)


class BrandKitError(ValueError):
    """The brand kit cannot be read."""


def normalize_color(value: Any) -> str | None:
    """``#RRGGBB`` (or a CSS rgb()/hsl() string) or ``None`` when unusable."""
    text = str(value or "").strip()
    if not text:
        return None
    match = _HEX.match(text)
    if match:
        digits = match.group(1)
        if len(digits) in (3, 4):
            digits = "".join(ch * 2 for ch in digits[:3])
        return "#" + digits[:6].upper()
    if _FUNC.match(text.lower()):
        return text
    return None


def _luminance(hex_color: str) -> float:
    if not hex_color.startswith("#") or len(hex_color) != 7:
        return 0.0
    channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


@dataclass(slots=True)
class BrandKit:
    name: str = ""
    logo_path: str | None = None
    logo_dark_path: str | None = None
    colors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLORS))
    fonts: dict[str, str] = field(default_factory=lambda: {"heading": "", "body": ""})
    font_files: list[str] = field(default_factory=list)
    tagline: str = ""
    url: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any], *, base_dir: Path | None = None) -> BrandKit:
        if not isinstance(raw, dict):
            raise BrandKitError("brand kit must be a JSON object")
        colors = dict(DEFAULT_COLORS)
        given = raw.get("colors") or {}
        if isinstance(given, list):  # tolerate a bare palette list
            given = dict(zip(COLOR_KEYS, given, strict=False))
        if not isinstance(given, dict):
            raise BrandKitError("brand.colors must be an object")
        for key in COLOR_KEYS:
            normalized = normalize_color(given.get(key))
            if normalized:
                colors[key] = normalized
        fonts_raw = raw.get("fonts") or {}
        if isinstance(fonts_raw, list):
            fonts_raw = {"heading": fonts_raw[0] if fonts_raw else "", "body": fonts_raw[-1] if fonts_raw else ""}
        if not isinstance(fonts_raw, dict):
            raise BrandKitError("brand.fonts must be an object")
        heading = str(fonts_raw.get("heading") or "").strip()
        body = str(fonts_raw.get("body") or heading).strip()
        font_files = raw.get("font_files") or []
        if isinstance(font_files, str):
            font_files = [font_files]
        return cls(
            name=str(raw.get("name") or "").strip(),
            logo_path=_resolve(raw.get("logo_path") or raw.get("logo"), base_dir),
            logo_dark_path=_resolve(raw.get("logo_dark_path"), base_dir),
            colors=colors,
            fonts={"heading": heading, "body": body},
            font_files=[p for p in (_resolve(f, base_dir) for f in font_files) if p],
            tagline=str(raw.get("tagline") or "").strip(),
            url=str(raw.get("url") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": self.name,
            "logo_path": self.logo_path,
            "colors": dict(self.colors),
            "fonts": dict(self.fonts),
        }
        if self.logo_dark_path:
            row["logo_dark_path"] = self.logo_dark_path
        if self.font_files:
            row["font_files"] = list(self.font_files)
        if self.tagline:
            row["tagline"] = self.tagline
        if self.url:
            row["url"] = self.url
        return row

    def css_variables(self) -> str:
        """``:root`` block for compositions written outside the runtime."""
        lines = [f"  --c-{key if key != 'background' else 'bg'}: {value};" for key, value in self.colors.items()]
        for slot in ("heading", "body"):
            family = self.fonts.get(slot) or ""
            stack = f'"{family}", system-ui, sans-serif' if family else "system-ui, sans-serif"
            lines.append(f"  --font-{slot}: {stack};")
        return ":root {\n" + "\n".join(lines) + "\n}\n"

    def font_faces(self) -> list[dict[str, Any]]:
        """``[{"path","family","weight","style"}]`` inferred from font file names."""
        faces: list[dict[str, Any]] = []
        heading = self.fonts.get("heading") or ""
        body = self.fonts.get("body") or heading
        for index, raw in enumerate(self.font_files):
            path = Path(raw)
            if path.suffix.lower() not in FONT_SUFFIXES or not path.is_file():
                continue
            stem = path.stem
            squashed = re.sub(r"[\s_\-]+", "", stem).lower()
            family = ""
            for candidate in (heading, body):
                if candidate and squashed.startswith(re.sub(r"[\s_\-]+", "", candidate).lower()):
                    family = candidate
                    break
            if not family:
                family = heading if index == 0 and heading else re.split(r"[-_]", stem)[0]
            lowered = stem.lower()
            weight: str = "400"
            if "variable" in lowered or "[wght" in lowered:
                weight = "100 900"
            else:
                for word, value in _WEIGHTS:
                    if word in lowered.replace("-", "").replace("_", ""):
                        weight = str(value)
                        break
            style = "italic" if "italic" in lowered or lowered.endswith("it") else "normal"
            faces.append({"path": str(path), "family": family, "weight": weight, "style": style})
        return faces


def _resolve(value: Any, base_dir: Path | None) -> str | None:
    text = str(value or "").strip()
    if not text or text.startswith(("http://", "https://", "data:")):
        return None
    path = Path(text).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = Path(base_dir) / path
    try:
        path = path.resolve()
    except OSError:
        return None
    return str(path) if path.is_file() else None


def load_brand_kit(
    value: Any = None,
    *,
    base_dir: Path | None = None,
    use_marketing_store: bool = True,
) -> BrandKit:
    """Brand kit from a dict, a JSON file path, a JSON string, or the store.

    ``None`` falls back to the marketing desk store, then to Navin's default
    palette, so a render never fails for lack of brand data.
    """
    if isinstance(value, BrandKit):
        return value
    if isinstance(value, dict):
        return BrandKit.from_dict(value, base_dir=base_dir)
    if isinstance(value, (str, Path)) and str(value).strip():
        text = str(value).strip()
        if text.startswith("{"):
            try:
                return BrandKit.from_dict(json.loads(text), base_dir=base_dir)
            except json.JSONDecodeError as exc:
                raise BrandKitError(f"brand JSON is invalid: {exc}") from exc
        path = Path(text).expanduser()
        if not path.is_absolute() and base_dir is not None:
            path = Path(base_dir) / path
        if not path.is_file():
            raise BrandKitError(f"brand kit file not found: {text}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BrandKitError(f"brand kit file is not valid JSON: {exc}") from exc
        return BrandKit.from_dict(raw, base_dir=path.parent)
    if use_marketing_store:
        found = brand_kit_from_marketing_store()
        if found is not None:
            return found
    return BrandKit()


def brand_kit_from_marketing_store(store: Any = None) -> BrandKit | None:
    """Build a kit from the marketing desk (brand.json + harvest.json), read only.

    Returns ``None`` when the desk holds no brand identity yet.
    """
    try:
        if store is None:
            from navin.marketing.store import MarketingStore

            store = MarketingStore()
        brand = store.load_brand()
        try:
            harvest = store.load_harvest()
        except Exception:  # noqa: BLE001 - harvest is optional enrichment
            harvest = {}
        try:
            product = store.load_product()
        except Exception:  # noqa: BLE001
            product = {}
    except Exception:  # noqa: BLE001 - a broken desk must not break rendering
        return None
    name = (
        str(brand.get("company") or "").strip()
        or str(brand.get("product") or "").strip()
        or str(harvest.get("name") or "").strip()
        or str(product.get("name") or "").strip()
    )
    palette = [c for c in (normalize_color(v) for v in (brand.get("colors") or [])) if c]
    palette += [
        c
        for c in (normalize_color(v) for v in (harvest.get("colors") or []))
        if c and c not in palette
    ]
    fonts = [str(f).strip() for f in (brand.get("fonts") or harvest.get("fonts") or []) if str(f).strip()]
    logo = _resolve(brand.get("logo"), None) or _resolve(harvest.get("logo"), None)
    if not (name or palette or logo):
        return None
    colors = dict(DEFAULT_COLORS)
    hexes = [c for c in palette if c.startswith("#")]
    darks = [c for c in hexes if _luminance(c) < 0.06]
    lights = [c for c in hexes if _luminance(c) > 0.8]
    vivid = [c for c in hexes if c not in darks and c not in lights]
    for key, value in zip(("primary", "secondary", "accent"), vivid, strict=False):
        colors[key] = value
    if darks:
        colors["background"] = darks[0]
        colors["text"] = lights[0] if lights else DEFAULT_COLORS["text"]
    elif lights:
        colors["background"] = lights[0]
        colors["text"] = "#0F1222"
    tagline = (
        str(product.get("one_liner") or "").strip()
        or str(harvest.get("one_liner") or "").strip()
    )
    url = str(brand.get("site") or harvest.get("site") or product.get("site") or "").strip()
    url = re.sub(r"^https?://", "", url).rstrip("/")
    return BrandKit(
        name=name,
        logo_path=logo,
        colors=colors,
        fonts={"heading": fonts[0] if fonts else "", "body": fonts[1] if len(fonts) > 1 else (fonts[0] if fonts else "")},
        tagline=tagline[:140],
        url=url,
    )


__all__ = [
    "COLOR_KEYS",
    "DEFAULT_COLORS",
    "BrandKit",
    "BrandKitError",
    "brand_kit_from_marketing_store",
    "load_brand_kit",
    "normalize_color",
]
