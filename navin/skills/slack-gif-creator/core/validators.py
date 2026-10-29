#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Check a GIF against Slack's practical limits.

Usage:
    validators.py <file.gif> --target emoji|message

Prints a JSON report and exits 1 when the file does not fit the target.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageSequence

TARGETS: dict[str, dict] = {
    "emoji": {
        "label": "custom emoji",
        "max_width": 128,
        "max_height": 128,
        "min_side": 32,
        "square": True,
        "max_bytes": 64 * 1024,
        "max_frames": 30,
        "max_duration_ms": 3000,
    },
    "message": {
        "label": "message GIF",
        "max_width": 1024,
        "max_height": 1024,
        "min_side": 128,
        "square": False,
        "max_bytes": 2 * 1024 * 1024,
        "max_frames": 90,
        "max_duration_ms": 10000,
    },
}


def inspect_gif(path: str | Path) -> dict:
    """Return width, height, frames, bytes, duration_ms and loop of a GIF."""
    p = Path(path)
    with Image.open(p) as img:
        width, height = img.size
        frames = 0
        duration_ms = 0
        loop = img.info.get("loop")
        for frame in ImageSequence.Iterator(img):
            duration_ms += int(frame.info.get("duration", 0) or 0)
            frames += 1
    return {
        "width": width,
        "height": height,
        "frames": frames,
        "bytes": p.stat().st_size,
        "duration_ms": duration_ms,
        "loop": loop,
    }


def validate_gif(path: str | Path, target: str = "emoji") -> dict:
    """Validate ``path`` for the given Slack target and list every issue."""
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; choose from {sorted(TARGETS)}")
    spec = TARGETS[target]
    p = Path(path)
    issues: list[str] = []
    if not p.is_file():
        return {
            "ok": False,
            "target": target,
            "path": str(p),
            "width": 0,
            "height": 0,
            "frames": 0,
            "bytes": 0,
            "duration_ms": 0,
            "issues": ["file not found"],
        }
    try:
        info = inspect_gif(p)
    except (OSError, ValueError) as exc:
        # UnidentifiedImageError and truncated files are OSError; a non-GIF image
        # that Pillow opens but cannot iterate raises ValueError.
        return {
            "ok": False,
            "target": target,
            "path": str(p),
            "width": 0,
            "height": 0,
            "frames": 0,
            "bytes": p.stat().st_size,
            "duration_ms": 0,
            "issues": [f"cannot read GIF: {exc}"],
        }
    w, h = info["width"], info["height"]
    if w > spec["max_width"] or h > spec["max_height"]:
        issues.append(f"{w}x{h} exceeds {spec['max_width']}x{spec['max_height']} for {spec['label']}")
    if min(w, h) < spec["min_side"]:
        issues.append(f"{w}x{h} is smaller than {spec['min_side']} px on one side; it will look blurry")
    if spec["square"] and w != h:
        issues.append(f"{w}x{h} is not square; Slack emoji are shown in a square box")
    if info["bytes"] > spec["max_bytes"]:
        issues.append(
            f"{info['bytes'] / 1024:.1f} KB exceeds {spec['max_bytes'] / 1024:.0f} KB; "
            "use fewer frames, fewer colors or a smaller canvas"
        )
    if info["frames"] < 2:
        issues.append("only one frame: this is not an animation")
    if info["frames"] > spec["max_frames"]:
        issues.append(f"{info['frames']} frames exceeds {spec['max_frames']}")
    if info["duration_ms"] > spec["max_duration_ms"]:
        issues.append(f"{info['duration_ms']} ms is longer than {spec['max_duration_ms']} ms per loop")
    if info["loop"] not in (0, None):
        issues.append(f"loop count is {info['loop']}; Slack GIFs should loop forever (0)")
    return {
        "ok": not issues,
        "target": target,
        "path": str(p),
        "width": w,
        "height": h,
        "frames": info["frames"],
        "bytes": info["bytes"],
        "duration_ms": info["duration_ms"],
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a GIF for Slack.")
    parser.add_argument("gif", help="path to the GIF")
    parser.add_argument("--target", choices=sorted(TARGETS), default="emoji")
    args = parser.parse_args(argv)
    report = validate_gif(args.gif, args.target)
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
