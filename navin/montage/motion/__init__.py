# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Motion design engine: deterministic HTML/three.js/GSAP renders to video.

- :mod:`.renderer`   Node-free renderer (Playwright Chromium + ffmpeg).
- :mod:`.catalog`    built-in template library and props schemas.
- :mod:`.brand`      brand kit contract (and marketing desk adapter).
- :mod:`.storyboard` storyboard contract: scenes + transitions + audio.
- :mod:`.vendor`     vendored three.js / GSAP runtime assets.
"""

from __future__ import annotations

from navin.montage.motion.brand import BrandKit, brand_kit_from_marketing_store, load_brand_kit
from navin.montage.motion.catalog import TEMPLATES, get_template, list_templates
from navin.montage.motion.renderer import (
    CODECS,
    FORMATS,
    MotionProgress,
    MotionRenderError,
    RenderSettings,
    capture_stills,
    render_motion,
)

__all__ = [
    "CODECS",
    "FORMATS",
    "TEMPLATES",
    "BrandKit",
    "MotionProgress",
    "MotionRenderError",
    "RenderSettings",
    "brand_kit_from_marketing_store",
    "capture_stills",
    "get_template",
    "list_templates",
    "load_brand_kit",
    "render_motion",
]
