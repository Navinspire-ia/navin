#!/usr/bin/env python3
"""Generate sharp animated README GIFs: loop + studio."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "assets"
FONT = "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"
FONT_B = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"

BG = (9, 11, 14)
CARD = (18, 20, 26)
LINE = (42, 46, 54)
MUTED = (140, 146, 156)
TEXT = (236, 238, 242)
BLUE = (3, 105, 255)
BLUE_DIM = (20, 48, 92)
GREEN = (111, 191, 106)
TEAL = (45, 212, 191)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_B if bold else FONT, size)


def rr(draw: ImageDraw.ImageDraw, box, radius: int, fill=None, outline=None, width: int = 2) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def save_gif(path: Path, frames: list[Image.Image], duration: int) -> None:
    quantized = [im.convert("P", palette=Image.ADAPTIVE, colors=48, dither=Image.Dither.NONE) for im in frames]
    quantized[0].save(
        path,
        save_all=True,
        append_images=quantized[1:],
        duration=duration,
        loop=0,
        optimize=False,
        disposal=2,
    )
    print(path.name, path.stat().st_size, frames[0].size, "frames", len(frames))


def draw_loop(labels: list[str], continue_label: str, active: int) -> Image.Image:
    w, h = 1400, 420
    im = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(im)
    n = 6
    pad_x = 48
    gap = 22
    pill_h = 72
    pill_w = (w - pad_x * 2 - gap * (n - 1)) // n
    y = 78
    f = font(28, bold=True)
    arrow_f = font(26, bold=True)

    for i, label in enumerate(labels):
        x = pad_x + i * (pill_w + gap)
        box = (x, y, x + pill_w, y + pill_h)
        on = i == active
        done = 0 <= active and i < active and active < n
        fill = (16, 34, 72) if on else CARD
        outline = BLUE if on else (GREEN if done else LINE)
        width = 4 if on else 2
        rr(d, box, 36, fill=fill, outline=outline, width=width)
        tw = d.textlength(label, font=f)
        d.text((x + (pill_w - tw) / 2, y + 20), label, font=f, fill=TEXT if on or done else MUTED)
        if i < n - 1:
            ax = x + pill_w + 3
            d.text((ax, y + 18), ">", font=arrow_f, fill=TEAL if i < active else (70, 78, 88))

    cw, ch = 420, 70
    cx = (w - cw) // 2
    cy = 250
    cont_on = active == n
    rr(d, (cx, cy, cx + cw, cy + ch), 35, fill=BLUE if cont_on else BLUE_DIM, outline=BLUE, width=3)
    cf = font(26, bold=True)
    tw = d.textlength(continue_label, font=cf)
    d.text((cx + (cw - tw) / 2, cy + 20), continue_label, font=cf, fill=TEXT)

    goal_cx = pad_x + pill_w // 2
    color = BLUE if cont_on or active == 0 else (55, 62, 72)
    d.line([(cx, cy + ch // 2), (goal_cx, cy + ch // 2)], fill=color, width=5)
    d.line([(goal_cx, cy + ch // 2), (goal_cx, y + pill_h + 2)], fill=color, width=5)
    d.polygon(
        [(goal_cx - 9, y + pill_h + 14), (goal_cx + 9, y + pill_h + 14), (goal_cx, y + pill_h + 2)],
        fill=color,
    )
    return im


def make_loop_gifs() -> None:
    en = ["Goal", "Plan", "Act", "Verify", "Remember", "Learn"]
    fr = ["Objectif", "Plan", "Action", "Verifier", "Memoire", "Apprendre"]
    frames_en = [draw_loop(en, "Continue (loop)", i) for i in range(7)]
    frames_fr = [draw_loop(fr, "Continuer (boucle)", i) for i in range(7)]
    frames_en.append(frames_en[-1])
    frames_fr.append(frames_fr[-1])
    save_gif(ROOT / "loop.gif", frames_en, 750)
    save_gif(ROOT / "loop-fr.gif", frames_fr, 750)


def draw_studio(step: int) -> Image.Image:
    w, h = 1400, 780
    im = Image.new("RGB", (w, h), (232, 228, 218))
    d = ImageDraw.Draw(im)

    x0, y0, x1, y1 = 28, 28, w - 28, h - 28
    rr(d, (x0, y0, x1, y1), 22, fill=BG, outline=(30, 32, 38), width=2)
    for i, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse((x0 + 22 + i * 22, y0 + 18, x0 + 36 + i * 22, y0 + 32), fill=color)

    title_f = font(20, bold=True)
    d.text((x0 + 110, y0 + 14), "Navin Marketing", font=title_f, fill=TEXT)

    sx1 = x0 + 250
    d.rectangle((x0 + 1, y0 + 52, sx1, y1 - 1), fill=(12, 14, 18))
    d.line([(sx1, y0 + 52), (sx1, y1 - 1)], fill=LINE, width=1)
    small = font(17)
    bold = font(17, bold=True)
    items = [
        ("Code", False),
        ("Studio", False),
        ("  Tenders", False),
        ("  Career", False),
        ("  Trading", False),
        ("  Leads", False),
        ("  Marketing", True),
        ("  Ads", False),
        ("  SEO", False),
        ("  Scraping", False),
        ("  Montage", False),
        ("  Notes", False),
        ("  Meeting", False),
        ("  Documents", False),
        ("RiskLens", False),
    ]
    iy = y0 + 72
    for label, on in items:
        if on:
            rr(d, (x0 + 16, iy - 6, sx1 - 14, iy + 28), 12, fill=(32, 34, 40), outline=LINE, width=1)
            d.ellipse((sx1 - 36, iy + 4, sx1 - 24, iy + 16), fill=GREEN)
        d.text((x0 + 28, iy), label, font=bold if on else small, fill=TEXT if on else MUTED)
        iy += 38

    mx = sx1 + 28
    my = y0 + 70
    d.text((mx, my), "#marketing", font=font(16, bold=True), fill=MUTED)
    loop_on = step >= 2
    bx0, by0 = mx + 170, my - 6
    rr(
        d,
        (bx0, by0, bx0 + 320, by0 + 36),
        18,
        fill=(18, 36, 22) if loop_on else CARD,
        outline=GREEN if loop_on else LINE,
        width=2,
    )
    d.text((bx0 + 18, by0 + 8), "Start loop - weekdays 09:00", font=font(15, bold=True), fill=GREEN if loop_on else MUTED)
    d.text((mx + 520, my), "Winners vs rest", font=font(15), fill=MUTED)
    d.text((mx + 720, my), "measure -> learn", font=font(15), fill=TEAL)

    steps = [
        "1. Use current project",
        "2. Approve campaign",
        "3. Start loop",
        "4. Heartbeat watch",
    ]
    sy = my + 70
    for i, label in enumerate(steps):
        on = i == step
        done = i < step
        box = (mx, sy + i * 88, mx + 520, sy + i * 88 + 72)
        rr(d, box, 16, fill=(14, 28, 20) if on else CARD, outline=GREEN if on or done else LINE, width=3 if on else 2)
        d.text((mx + 24, sy + i * 88 + 22), label, font=font(24, bold=True), fill=TEXT if on or done else MUTED)

    d.text((mx, sy + 370), "Never publish. Never spend ads.", font=font(16), fill=MUTED)

    rx = mx + 560
    ry = sy
    rr(d, (rx, ry, x1 - 36, ry + 280), 18, fill=CARD, outline=LINE, width=2)
    d.text((rx + 28, ry + 22), "Hook", font=font(18, bold=True), fill=MUTED)
    d.text((rx + 320, ry + 22), "Multiple", font=font(18, bold=True), fill=MUTED)
    d.line([(rx + 20, ry + 62), (x1 - 56, ry + 62)], fill=LINE, width=1)
    show = step >= 3
    d.text((rx + 28, ry + 88), "LinkedIn - waitlist", font=font(22, bold=True), fill=TEXT if show else MUTED)
    d.text((rx + 320, ry + 84), "2.4x" if show else "...", font=font(32, bold=True), fill=GREEN if show else LINE)
    return im


def make_studio_gif() -> None:
    frames = [draw_studio(i) for i in range(4)]
    frames.append(draw_studio(3))
    save_gif(ROOT / "navin.gif", frames, 850)


if __name__ == "__main__":
    make_loop_gifs()
    make_studio_gif()
