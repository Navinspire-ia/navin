#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Assemble PIL frames into a looping, palette-reduced GIF.

Usage:
    builder = GifBuilder(128, 128)
    builder.add_frame(img)          # repeat for every frame
    builder.set_duration(80)        # ms per frame, or a list per frame
    builder.quantize(64)            # palette size
    info = builder.save("out.gif")  # {path, bytes, frames, width, height, duration_ms}

Only Pillow is required.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image


class GifBuilder:
    """Collect frames and write a Slack-friendly GIF."""

    def __init__(self, width: int = 128, height: int = 128):
        self.width = int(width)
        self.height = int(height)
        self.frames: list[Image.Image] = []
        self._durations: int | list[int] = 80
        self._loop = 0
        self._colors = 128
        self._optimize = True
        self._dither = True

    # -- frames -------------------------------------------------------------

    def add_frame(self, frame: Image.Image) -> GifBuilder:
        """Append one frame; it is converted to RGB and resized if needed."""
        img = frame.convert("RGB")
        if img.size != (self.width, self.height):
            img = img.resize((self.width, self.height), Image.Resampling.LANCZOS)
        self.frames.append(img)
        return self

    def add_frames(self, frames: list[Image.Image]) -> GifBuilder:
        for frame in frames:
            self.add_frame(frame)
        return self

    def clear(self) -> None:
        self.frames = []

    # -- settings -----------------------------------------------------------

    def set_duration(self, ms: int | list[int]) -> GifBuilder:
        """Milliseconds per frame: one integer for all frames or one per frame."""
        if isinstance(ms, (list, tuple)):
            self._durations = [max(20, int(v)) for v in ms]
        else:
            self._durations = max(20, int(ms))
        return self

    def set_fps(self, fps: float) -> GifBuilder:
        return self.set_duration(round(1000.0 / max(1.0, float(fps))))

    def loop(self, count: int = 0) -> GifBuilder:
        """0 means loop forever (the Slack default)."""
        self._loop = max(0, int(count))
        return self

    def quantize(self, colors: int = 128, dither: bool = True) -> GifBuilder:
        """Palette size for the whole animation (2 to 256)."""
        self._colors = min(256, max(2, int(colors)))
        self._dither = bool(dither)
        return self

    def optimize(self, enabled: bool = True) -> GifBuilder:
        """Let Pillow drop unused palette entries and crop static areas."""
        self._optimize = bool(enabled)
        return self

    # -- output -------------------------------------------------------------

    def _durations_list(self) -> list[int]:
        n = len(self.frames)
        if isinstance(self._durations, list):
            if not self._durations:
                return [80] * n
            seq = list(self._durations)
            while len(seq) < n:
                seq.append(seq[-1])
            return seq[:n]
        return [self._durations] * n

    def _palette_frames(self) -> list[Image.Image]:
        """Quantize every frame against one shared palette for stable colors."""
        sample_count = min(6, len(self.frames))
        step = max(1, len(self.frames) // sample_count)
        samples = self.frames[::step][:sample_count]
        sheet = Image.new("RGB", (self.width * len(samples), self.height))
        for i, img in enumerate(samples):
            sheet.paste(img, (i * self.width, 0))
        palette = sheet.quantize(colors=self._colors, method=Image.Quantize.MEDIANCUT)
        dither = Image.Dither.FLOYDSTEINBERG if self._dither else Image.Dither.NONE
        return [img.quantize(palette=palette, dither=dither) for img in self.frames]

    def save(self, path: str | Path) -> dict:
        """Write the GIF and return a summary dict."""
        if not self.frames:
            raise ValueError("no frames: call add_frame() before save()")
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        frames = self._palette_frames()
        durations = self._durations_list()
        frames[0].save(
            out,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=self._loop,
            optimize=self._optimize,
            disposal=2,
        )
        # Pillow merges identical consecutive frames when optimizing, so
        # report the frame count actually written.
        with Image.open(out) as written:
            written_frames = getattr(written, "n_frames", len(frames))
        return {
            "path": str(out),
            "bytes": out.stat().st_size,
            "frames": written_frames,
            "source_frames": len(frames),
            "width": self.width,
            "height": self.height,
            "duration_ms": sum(durations),
            "colors": self._colors,
            "loop": self._loop,
        }

    def size_report(self, info: dict, budget_bytes: int | None = None) -> str:
        """Human-readable one-liner about the saved file and how to shrink it."""
        kb = info["bytes"] / 1024
        line = (
            f"{info['path']}: {kb:.1f} KB, {info['frames']} frames, "
            f"{info['width']}x{info['height']}, {info['duration_ms']} ms, {info['colors']} colors"
        )
        if budget_bytes is not None and info["bytes"] > budget_bytes:
            over = (info["bytes"] - budget_bytes) / 1024
            line += (
                f" | OVER budget by {over:.1f} KB: drop frames, reduce colors "
                "(64 then 32) or shrink the canvas"
            )
        return line


if __name__ == "__main__":
    b = GifBuilder(128, 128)
    for i in range(12):
        shade = int(255 * i / 11)
        b.add_frame(Image.new("RGB", (128, 128), (shade, 200, 120)))
    print(b.size_report(b.set_duration(80).quantize(32).save("/tmp/gif_builder_demo.gif")))
