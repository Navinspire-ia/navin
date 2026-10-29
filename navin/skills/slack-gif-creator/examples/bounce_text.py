#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Bouncing text GIF built with the core modules.

Usage:
    bounce_text.py --text "SHIP IT" --out /tmp/ship.gif --target emoji
    bounce_text.py --text "Deployed" --out /tmp/deployed.gif --target message

Prints the validator report as JSON; exit code 1 when the GIF does not fit.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

INK = "#17171a"
PAPER = "#f4f3ee"
LIME = "#c6e88a"


def _core():
    """Import the sibling ``core`` package of this skill (the example lives in examples/)."""
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from core import easing, frame_composer, gif_builder, validators

    return easing, frame_composer, gif_builder, validators


PRESETS = {
    "emoji": {"size": 128, "frames": 16, "colors": 48, "duration": 70},
    "message": {"size": 480, "frames": 24, "colors": 96, "duration": 60},
}


def fit_font_size(text: str, canvas: int) -> int:
    """Pick a font size so the word spans roughly 80 percent of the canvas."""
    per_char = max(1, len(text))
    return max(10, min(int(canvas * 0.55), int(canvas * 1.45 / per_char)))


def build(text: str, out: Path, target: str) -> dict:
    easing, frame_composer, gif_builder, validators = _core()
    preset = PRESETS[target]
    size = preset["size"]
    n = preset["frames"]
    builder = gif_builder.GifBuilder(size, size)
    font_size = fit_font_size(text, size)
    drop = size * 0.28
    for i in range(n):
        t = i / (n - 1)
        # Fall during the first 60 percent with a bounce, then settle.
        if t < 0.6:
            y_off = -drop * (1.0 - easing.ease("ease_out_bounce", t / 0.6))
        else:
            y_off = 0.0
        frame = frame_composer.FrameComposer(size, size, INK)
        frame.circle((size / 2, size / 2), size * 0.42, fill=LIME)
        # Shadow grows as the text lands.
        shadow_alpha = 0.15 + 0.35 * min(1.0, i / max(1, n * 0.6))
        shadow = frame_composer.FrameComposer(size, size, (0, 0, 0, 0))
        shadow.text(text, size=font_size, color=(23, 23, 26, int(255 * shadow_alpha)),
                    position=(size / 2 + size * 0.02, size / 2 + size * 0.03))
        frame.paste(shadow.to_image("RGBA"), (0, 0))
        frame.text(text, size=font_size, color=INK, position=(size / 2, size / 2 + y_off))
        builder.add_frame(frame.to_image())
    builder.set_duration(preset["duration"]).loop(0).quantize(preset["colors"]).optimize(True)
    info = builder.save(out)
    report = validators.validate_gif(out, target)
    report["size_report"] = builder.size_report(info)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bouncing text GIF for Slack.")
    parser.add_argument("--text", default="GO", help="word to animate (short words read best)")
    parser.add_argument("--out", default="/tmp/bounce_text.gif", help="output .gif path")
    parser.add_argument("--target", choices=sorted(PRESETS), default="emoji")
    args = parser.parse_args(argv)
    report = build(args.text, Path(args.out), args.target)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
