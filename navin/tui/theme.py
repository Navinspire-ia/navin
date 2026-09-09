"""Navin color themes for the terminal UI.

Brand chrome uses a sky blue that stays readable on black without the
neon punch of ``#0369FF``. Teal ``#54D4CD`` stays for accents. File paths
use sage green; inline code that is not a path stays with the body text.
Built-in Textual themes stay selectable from the theme picker.

``navin`` (Navin Night) is the default; ``navin-light`` (Navin Day) is the
same scale on paper white.
"""

from __future__ import annotations

from textual.theme import Theme

# Sky blue: same family as the brand mark, lighter so labels do not glare.
PRIMARY_INK = "#5EA8FF"
PRIMARY_INK_LIGHT = "#3D82FF"

NAVIN_DARK = Theme(
    name="navin",
    primary=PRIMARY_INK,
    secondary="#54D4CD",
    accent="#54D4CD",
    warning="#E6B84C",
    error="#E85D4C",
    success="#54D4CD",
    foreground="#F2F2F2",
    background="#000000",
    surface="#262626",
    panel="#1E1E1E",
    dark=True,
    variables={
        "text-muted": "#9A9A9A",
        "border": "#3A3A3A",
        "border-blurred": "#2E2E2E",
        "scrollbar": "#3A3A3A",
        "scrollbar-hover": "#4A4A4A",
        "scrollbar-active": "#8A8A8A",
        "scrollbar-background": "#000000",
        "scrollbar-background-hover": "#000000",
        "scrollbar-background-active": "#000000",
        "block-cursor-foreground": "#000000",
        "block-cursor-background": "#F2F2F2",
        "input-cursor-foreground": "#000000",
        "input-cursor-background": "#F2F2F2",
        "input-selection-background": "#F2F2F2 25%",
        "footer-background": "#000000",
        "footer-key-foreground": "#8A8A8A",
        "footer-description-foreground": "#8A8A8A",
        "button-color-foreground": "#111111",
        "markdown-code-inline-background": "transparent",
        "markdown-code-block-background": "#121212",
        "link-color": "#7AA8A2",
        "link-color-hover": "#8FBC8F",
        "path": "#8FBC8F",
    },
)

NAVIN_LIGHT = Theme(
    name="navin-light",
    primary=PRIMARY_INK_LIGHT,
    secondary="#0F766E",
    accent="#0F766E",
    warning="#B45309",
    error="#B42318",
    success="#0F766E",
    foreground="#111111",
    background="#F5F5F5",
    surface="#FFFFFF",
    panel="#E8E8E8",
    dark=False,
    variables={
        "text-muted": "#5A5A5A",
        "border": "#D0D0D0",
        "border-blurred": "#E0E0E0",
        "scrollbar-active": "#2E2E2E",
        "block-cursor-foreground": "#FFFFFF",
        "block-cursor-background": "#2E2E2E",
        "input-cursor-foreground": "#FFFFFF",
        "input-cursor-background": "#2E2E2E",
        "input-selection-background": "#2E2E2E 20%",
        "footer-background": "#F5F5F5",
        "footer-key-foreground": "#2E2E2E",
        "footer-description-foreground": "#6A6A6A",
        "button-color-foreground": "#FFFFFF",
        "markdown-code-inline-background": "transparent",
        "markdown-code-block-background": "#FFFFFF",
        "link-color": "#0F766E",
        "link-color-hover": "#2D6A4F",
        "path": "#2D6A4F",
    },
)

NAVIN_THEMES: tuple[Theme, ...] = (NAVIN_DARK, NAVIN_LIGHT)
