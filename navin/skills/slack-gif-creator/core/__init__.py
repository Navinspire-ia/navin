#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Small toolkit for building Slack-ready animated GIFs with Pillow only."""

from .easing import EASINGS, ease
from .frame_composer import FrameComposer, hex_to_rgb
from .gif_builder import GifBuilder
from .validators import TARGETS, validate_gif

NAVIN_PALETTE = {
    "ink": "#17171a",
    "paper": "#f4f3ee",
    "lime": "#c6e88a",
    "teal": "#0e7490",
    "violet": "#7c3aed",
    "coral": "#ff5f57",
    "amber": "#febc2e",
}

__all__ = [
    "EASINGS",
    "FrameComposer",
    "GifBuilder",
    "NAVIN_PALETTE",
    "TARGETS",
    "ease",
    "hex_to_rgb",
    "validate_gif",
]
