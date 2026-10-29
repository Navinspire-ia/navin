---
name: slack-gif-creator
description: Builds small looping animated GIFs for Slack (custom emoji at 128x128, message GIFs up to 2 MB) from code with Pillow, applying size budgets, palette reduction and easing so the result reads well at thumbnail size. Use when the user asks to "make a Slack emoji GIF", "animate this text for Slack", "create a bouncing GIF", "make a spinner emoji" or wants a reaction GIF that fits Slack's limits. In French "crée un GIF animé pour Slack", "anime ce texte en emoji" or "fais un GIF qui rebondit".
metadata: {"navin":{"emoji":"🎞️","category":"design"}}
---

# Slack GIF Creator

Generate every frame in Python with Pillow, assemble them with the bundled builder, then validate against the Slack target before showing the file. There is no sprite library and no emoji font: draw with shapes, text and pasted images, and lean on contrast and motion rather than detail.

## Slack constraints

- Custom emoji: render at 128x128, square, aim under 64 KB. Slack scales emoji down to about 22 px in messages, so one word, one shape or one gesture is the limit.
- Message GIFs: 320 to 640 px per side, under 2 MB. Width can exceed height, but keep the aspect ratio under 2:1 or the preview is cropped.
- Frames: 10 to 30 per loop. 12 to 16 frames at 60 to 90 ms per frame is the sweet spot for emoji; message GIFs can take 24 frames at 50 to 70 ms.
- Loop forever (`loop(0)`). Slack ignores play-once GIFs and users expect motion to continue.
- Palette: quantize to 64 to 128 colors for message GIFs, 32 to 48 for emoji. Flat fills compress far better than gradients or anti-aliased photos.
- What reads well small: bold sans text of 2 to 6 characters, a single saturated accent on a dark or light field, motion that moves at least 10 percent of the canvas. Thin lines (width 1) vanish; use width 3 or more.
- High contrast: pair Ink text on Lime or Paper, or Paper text on Ink, Teal or Violet. Avoid mid-grey on mid-grey.

## Navin palette (defaults)

Ink `#17171a` (background or text), Paper `#f4f3ee` (light background or text), Lime `#c6e88a` (primary accent), Teal `#0e7490`, Violet `#7c3aed`, Coral `#ff5f57` (alerts, celebration), Amber `#febc2e` (warning, highlight). Default to Ink background with Lime accent and Ink text on the accent; switch to Paper background when the emoji will sit on dark Slack themes most of the time.

## Workflow

1. Clarify the target: emoji (default when the user says "emoji", "reaction", "custom emoji") or message GIF (anything to post in a channel). Pick the preset from `core/validators.py` `TARGETS`.
2. Write a short script, or reuse `examples/bounce_text.py` as a base. Import from `core/`: `FrameComposer` (`core/frame_composer.py`) to draw each frame, `ease` from `core/easing.py` for motion curves, `GifBuilder` (`core/gif_builder.py`) to assemble and quantize, `validate_gif` (`core/validators.py`) to check the result.
3. Loop over `n` frames with `t = i / (n - 1)`, derive positions, scale or opacity from `ease(name, t)`, draw, and call `builder.add_frame(frame.to_image())`.
4. Configure the builder: `set_duration(ms)` (or a list for a hold on the last frame), `loop(0)`, `quantize(colors)`, `optimize(True)`, then `save(path)`. The dict it returns has `path, bytes, frames, width, height, duration_ms`; `size_report(info, budget_bytes)` turns it into one line for the user.
5. Validate: `validate_gif(path, "emoji")` or run `python3 core/validators.py out.gif --target emoji`. Fix every entry in `issues` before presenting.
6. Show the result: call `present_artifact` on the GIF when available, otherwise `read_file` the GIF (or a PNG export of the first frame) so the user sees the actual output, and quote the size report.

Example run: `python3 examples/bounce_text.py --text "GO" --out /tmp/go.gif --target emoji` prints the validator JSON and exits 1 when the file does not fit.

## Composition recipes

- Bouncing text: start the word above the canvas and move it down with `ease_out_bounce` over the first 60 percent of the frames, then hold. Add a soft shadow whose opacity grows as the word lands (see `examples/bounce_text.py`).
- Pulse: scale a circle or badge between 0.85 and 1.15 using `0.5 - 0.5 * cos(2 * pi * t)`; for a heartbeat, do two short pulses and a longer rest.
- Spinner: draw an arc or 8 dots around the center and rotate by `360 * t` degrees per loop; use `ease_in_out_quad` on the angle for a mechanical feel, linear for a smooth one. Make the frame count divide the rotation evenly so the loop has no seam.
- Typewriter: reveal `text[:k]` with `k = round(len(text) * t)` and draw a caret rectangle that blinks every 3 frames; hold the full word for 4 to 6 frames at the end by passing a per-frame duration list.
- Confetti: seed `random.Random(7)` so the output is reproducible, spawn 20 to 40 rectangles in Lime, Coral, Amber and Violet with random velocity, add gravity each frame, and fade them out with `paste(..., alpha=1 - t)`.
- Combine: bounce plus pulse, or spin plus fade, when a single effect feels flat. Keep one focal element.

## Iterating on size

When the validator reports the file is over budget, apply changes in this order and re-run:

1. Fewer frames: drop from 24 to 16, then 12; remove near-duplicate frames (holds can be expressed as longer durations instead of repeated frames).
2. Fewer colors: `quantize(64)` then `quantize(32)`; disable dithering (`quantize(32, dither=False)`) on flat artwork.
3. Smaller canvas: 480 to 400 to 320 for message GIFs; emoji stay at 128 but can drop to 96 if still too heavy.
4. Simplify artwork: replace gradients with flat fills, remove the shadow, thicken strokes instead of adding detail.

Never upscale a small canvas to meet the size target: Slack would blur it. Rendering at the final size with high-contrast flat shapes gives both the smallest file and the sharpest result.

## Guardrails

- Pillow only; `numpy` is optional and never required.
- No network calls, no `input()`, deterministic randomness (seeded) so a re-run reproduces the same file.
- Write outputs to a path the user names or to `/tmp`, never into the skill folder.
- If the user uploads an image, decide whether it is the subject (animate it, split it) or the inspiration (match its colors and mood) and say which interpretation was taken.
