"""Mapping web fonts onto fonts Office can actually render.

Templates use Google fonts that the browser downloads but that are absent from
most Office installs, where Word or PowerPoint would silently substitute
something arbitrary. Choosing the substitute here keeps the output
predictable, and CSS stacks usually name a sensible system fallback already.
"""

from __future__ import annotations

from typing import Sequence

OFFICE_FONTS = {
    "arial", "arial black", "helvetica", "helvetica neue", "segoe ui", "segoe ui semibold",
    "calibri", "calibri light", "aptos", "aptos display", "verdana", "tahoma", "trebuchet ms",
    "georgia", "times new roman", "times", "garamond", "cambria", "candara", "corbel",
    "constantia", "century gothic", "franklin gothic book", "palatino linotype", "book antiqua",
    "impact", "courier new", "consolas", "lucida console", "noto sans", "noto serif",
    "noto naskh arabic", "noto sans arabic", "amiri", "system-ui",
}
SUBSTITUTES = {
    "inter": "Calibri", "sora": "Segoe UI", "poppins": "Century Gothic",
    "montserrat": "Segoe UI", "manrope": "Segoe UI", "dm sans": "Calibri",
    "work sans": "Calibri", "plus jakarta sans": "Segoe UI", "figtree": "Calibri",
    "outfit": "Century Gothic", "space grotesk": "Segoe UI", "roboto": "Arial",
    "open sans": "Calibri", "lato": "Calibri", "nunito": "Calibri", "nunito sans": "Calibri",
    "source sans pro": "Calibri", "source sans 3": "Calibri", "raleway": "Segoe UI",
    "rubik": "Segoe UI", "karla": "Calibri", "barlow": "Calibri", "urbanist": "Segoe UI",
    "archivo": "Segoe UI", "epilogue": "Segoe UI", "satoshi": "Segoe UI",
    "playfair display": "Georgia", "merriweather": "Georgia", "lora": "Georgia",
    "libre baskerville": "Georgia", "cormorant garamond": "Garamond", "eb garamond": "Garamond",
    "crimson text": "Georgia", "dm serif display": "Georgia", "fraunces": "Georgia",
    "jetbrains mono": "Consolas", "fira code": "Consolas", "ibm plex mono": "Consolas",
    "roboto mono": "Consolas", "space mono": "Consolas", "source code pro": "Consolas",
}
GENERIC = {
    "serif": "Georgia",
    "sans-serif": "Calibri",
    "monospace": "Consolas",
    "cursive": "Segoe Script",
    "system-ui": "Calibri",
    "ui-sans-serif": "Calibri",
}


def office_font(families: Sequence[str], fallback: str, keep: bool = False) -> str:
    """Map a CSS font stack onto a font Office can actually render."""
    stack = [name for name in (families or []) if name] or [fallback]
    if keep:
        return stack[0]
    for name in stack:
        key = name.strip().lower()
        if key in OFFICE_FONTS:
            return name.strip()
        substitute = SUBSTITUTES.get(key)
        if substitute:
            return substitute
    for name in reversed(stack):
        generic = GENERIC.get(name.strip().lower())
        if generic:
            return generic
    return stack[0].strip() or "Calibri"
