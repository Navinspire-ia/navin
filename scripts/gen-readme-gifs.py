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


NAV = [
    ("code", "Code", False),
    (None, "Studio", False),
    ("tenders", "Tenders", True),
    ("career", "Career", True),
    ("trading", "Trading", True),
    ("leads", "Leads", True),
    ("marketing", "Marketing", True),
    ("ads", "Ads", True),
    ("seo", "SEO", True),
    ("scraping", "Scraping", True),
    ("montage", "Montage", True),
    ("notes", "Notes", True),
    ("meeting", "Meeting", True),
    ("documents", "Documents", True),
    ("risklens", "RiskLens", True),
]

TITLES = {
    "code": "Navin Code",
    "tenders": "Navin Tenders",
    "career": "Navin Career",
    "trading": "Navin Trading",
    "leads": "Navin Leads",
    "marketing": "Navin Marketing",
    "ads": "Navin Ads",
    "seo": "Navin SEO",
    "scraping": "Navin Scraping",
    "montage": "Navin Montage",
    "notes": "Navin Notes",
    "meeting": "Navin Meeting",
    "documents": "Navin Documents",
    "risklens": "Navin RiskLens",
}

CYCLE = [key for key, _label, _child in NAV if key]


def kpi_row(d: ImageDraw.ImageDraw, x: int, y: int, w: int, items: list[tuple[str, str]]) -> None:
    gap = 12
    cw = (w - gap * (len(items) - 1)) // len(items)
    for i, (label, value) in enumerate(items):
        bx = x + i * (cw + gap)
        rr(d, (bx, y, bx + cw, y + 78), 14, fill=CARD, outline=LINE, width=1)
        d.text((bx + 16, y + 12), label, font=font(14), fill=MUTED)
        d.text((bx + 16, y + 36), value, font=font(24, bold=True), fill=TEXT)


def draw_code(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    mid = x + 430
    d.line([(mid, y), (mid, y1)], fill=LINE, width=1)
    rr(d, (x, y + 8, mid - 16, y + 88), 16, fill=(28, 30, 36), outline=LINE, width=1)
    d.text((x + 16, y + 22), "Create a shop. Web first, then the iPhone app.", font=font(16), fill=TEXT)
    rr(d, (x, y + 104, mid - 16, y + 220), 16, fill=(18, 32, 22), outline=GREEN, width=2)
    d.text((x + 16, y + 118), "writing", font=font(13, bold=True), fill=GREEN)
    d.text((x + 16, y + 146), "Home.tsx  Shop.tsx  Pay.tsx  Cart.tsx", font=font(16, bold=True), fill=TEXT)
    d.text((x + 16, y + 180), "Watch the files spin in.", font=font(15), fill=MUTED)
    d.text((mid + 20, y + 8), "Web shop", font=font(15), fill=MUTED)
    d.text((x1 - 140, y + 8), "6 files", font=font(15, bold=True), fill=GREEN)
    files = [("Home.tsx", "page"), ("Shop.tsx", "grid"), ("Pay.tsx", "pay"), ("Cart.tsx", "bag")]
    for i, (name, kind) in enumerate(files):
        fy = y + 48 + i * 70
        rr(d, (mid + 20, fy, x1, fy + 58), 12, fill=CARD, outline=LINE, width=1)
        d.text((mid + 36, fy + 16), name, font=font(18, bold=True), fill=TEXT)
        d.text((x1 - 80, fy + 18), kind, font=font(15), fill=MUTED)


def draw_desk(
    d: ImageDraw.ImageDraw,
    x: int,
    y: int,
    x1: int,
    y1: int,
    kpis: list[tuple[str, str]],
    left_k: str,
    left_v: str,
    left_s: str,
    right_k: str,
    right_v: str,
    right_s: str,
) -> None:
    kpi_row(d, x, y, x1 - x, kpis)
    top = y + 100
    mid = x + int((x1 - x) * 0.58)
    rr(d, (x, top, mid - 10, y1), 16, fill=CARD, outline=LINE, width=1)
    rr(d, (mid + 10, top, x1, y1), 16, fill=CARD, outline=LINE, width=1)
    d.text((x + 22, top + 20), left_k, font=font(15), fill=MUTED)
    d.text((x + 22, top + 56), left_v, font=font(26, bold=True), fill=TEXT)
    d.text((x + 22, top + 100), left_s, font=font(16), fill=MUTED)
    d.text((mid + 32, top + 20), right_k, font=font(15), fill=MUTED)
    d.text((mid + 32, top + 56), right_v, font=font(32, bold=True), fill=GREEN)
    d.text((mid + 32, top + 110), right_s, font=font(16), fill=MUTED)


def draw_leads(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    mid = x + 400
    rr(d, (x, y, mid - 12, y1), 16, fill=CARD, outline=LINE, width=1)
    d.text((x + 20, y + 18), "https://acme.com/team", font=font(14), fill=MUTED)
    d.ellipse((x + 22, y + 56, x + 70, y + 104), fill=(48, 72, 90))
    d.text((x + 86, y + 62), "Maya Chen", font=font(22, bold=True), fill=TEXT)
    d.text((x + 86, y + 94), "CTO  -  Acme Robotics", font=font(15), fill=MUTED)
    d.text((x + 22, y + 128), "Pulled from the live page", font=font(15, bold=True), fill=GREEN)
    rows = [("Acme Robotics", "92"), ("Nova Health", "88"), ("Orbit SaaS", "91"), ("Harbor AI", "84")]
    d.text((mid + 8, y), "Account", font=font(15, bold=True), fill=MUTED)
    d.text((x1 - 90, y), "Score", font=font(15, bold=True), fill=MUTED)
    for i, (name, score) in enumerate(rows):
        ry = y + 40 + i * 72
        rr(d, (mid + 4, ry, x1, ry + 60), 12, fill=CARD, outline=LINE, width=1)
        d.text((mid + 20, ry + 18), name, font=font(20, bold=True), fill=TEXT)
        d.text((x1 - 80, ry + 16), score, font=font(24, bold=True), fill=GREEN)


def draw_marketing(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    steps = [
        "1. Use current project",
        "2. Approve campaign",
        "3. Start loop",
        "4. Heartbeat watch",
    ]
    left = x + 520
    for i, label in enumerate(steps):
        on = i == 0
        box = (x, y + i * 88, left - 16, y + i * 88 + 72)
        rr(d, box, 16, fill=(14, 28, 20) if on else CARD, outline=GREEN if on else LINE, width=3 if on else 2)
        d.text((x + 24, y + i * 88 + 22), label, font=font(22, bold=True), fill=TEXT if on else MUTED)
    d.text((x, y + 370), "Never publish. Never spend ads.", font=font(16), fill=MUTED)
    rr(d, (left, y, x1, y + 280), 18, fill=CARD, outline=LINE, width=2)
    d.text((left + 28, y + 22), "Hook", font=font(18, bold=True), fill=MUTED)
    d.text((left + 300, y + 22), "Multiple", font=font(18, bold=True), fill=MUTED)
    d.line([(left + 20, y + 62), (x1 - 20, y + 62)], fill=LINE, width=1)
    d.text((left + 28, y + 88), "LinkedIn - waitlist", font=font(22, bold=True), fill=TEXT)
    d.text((left + 300, y + 84), "2.4x", font=font(32, bold=True), fill=GREEN)


def draw_ads(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    cards = [
        ("Google", "Free Cursor alternative", "ready"),
        ("Meta", "The AGI that stays on disk", "writing"),
        ("LinkedIn", "Six coding seats. One desk.", "wait"),
        ("TikTok", "Cut from the product", "wait"),
    ]
    gap = 16
    cw = (x1 - x - gap) // 2
    ch = (y1 - y - gap) // 2
    for i, (name, hook, state) in enumerate(cards):
        cx = x + (i % 2) * (cw + gap)
        cy = y + (i // 2) * (ch + gap)
        on = i == 1
        rr(d, (cx, cy, cx + cw, cy + ch), 16, fill=(18, 32, 22) if on else CARD, outline=GREEN if on else LINE, width=2)
        d.text((cx + 18, cy + 16), name, font=font(20, bold=True), fill=TEXT)
        color = GREEN if state != "wait" else MUTED
        d.text((cx + cw - 110, cy + 18), state, font=font(15, bold=True), fill=color)
        d.text((cx + 18, cy + 64), hook, font=font(18), fill=TEXT)


def draw_seo(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    mid = x + 400
    topics = [("Cursor alternative", "2.4k"), ("Claude Code alternative", "1.8k"), ("Local AI desk", "890")]
    for i, (name, vol) in enumerate(topics):
        ty = y + i * 70
        on = i == 0
        rr(d, (x, ty, mid - 12, ty + 58), 12, fill=(28, 30, 36) if on else CARD, outline=LINE, width=1)
        d.text((x + 16, ty + 16), name, font=font(18, bold=True), fill=TEXT if on else MUTED)
        d.text((mid - 80, ty + 18), vol, font=font(16), fill=MUTED)
    d.text((mid + 8, y), "Free Cursor alternative - Navin", font=font(22, bold=True), fill=TEXT)
    d.text((mid + 8, y + 44), "navin.live/en", font=font(16), fill=GREEN)
    d.text((mid + 8, y + 88), "Local-first AGI. Install free.", font=font(18), fill=MUTED)
    d.text((mid + 8, y + 120), "Keep the files. Open Code when", font=font(18), fill=MUTED)
    d.text((mid + 8, y + 152), "the page is the product.", font=font(18), fill=MUTED)


def draw_scraping(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    rr(d, (x, y, x1, y + 48), 12, fill=CARD, outline=LINE, width=1)
    d.text((x + 18, y + 14), "https://lumen.shop/pricing", font=font(16, bold=True), fill=MUTED)
    plans = [("Free", "$0"), ("Pro", "$29"), ("Desk", "$99")]
    pw = (x1 - x - 400 - 24) // 3
    for i, (name, price) in enumerate(plans):
        px = x + i * (pw + 12)
        on = i == 1
        rr(d, (px, y + 72, px + pw, y + 200), 14, fill=(18, 32, 22) if on else CARD, outline=GREEN if on else LINE, width=2)
        d.text((px + 16, y + 90), name, font=font(18, bold=True), fill=TEXT)
        d.text((px + 16, y + 128), price, font=font(28, bold=True), fill=GREEN if on else TEXT)
    rx = x1 - 380
    d.text((rx, y + 72), "Cleaned", font=font(15, bold=True), fill=GREEN)
    fields = [("plan", "Pro"), ("price", "29 USD / month"), ("seats", "Unlimited seats")]
    for i, (k, v) in enumerate(fields):
        fy = y + 110 + i * 70
        rr(d, (rx, fy, x1, fy + 58), 12, fill=CARD, outline=LINE, width=1)
        d.text((rx + 16, fy + 8), k, font=font(13), fill=MUTED)
        d.text((rx + 16, fy + 28), v, font=font(18, bold=True), fill=GREEN)


def draw_montage(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    rr(d, (x, y, x + 120, y1 - 90), 12, fill=CARD, outline=LINE, width=1)
    for i, name in enumerate(["A Screen", "B Talk", "C Logo", "D VO"]):
        on = i == 0
        d.text((x + 16, y + 20 + i * 48), name, font=font(16, bold=True), fill=TEXT if on else MUTED)
    rr(d, (x + 136, y, x1, y1 - 90), 12, fill=(16, 16, 20), outline=LINE, width=1)
    d.text((x + 156, y + 16), "REC", font=font(14, bold=True), fill=(248, 80, 80))
    d.text((x1 - 100, y + 16), "16:9", font=font(14), fill=MUTED)
    d.text((x + 180, y + 140), "The demo is cut from the product.", font=font(22, bold=True), fill=TEXT)
    rr(d, (x, y1 - 74, x1, y1), 10, fill=CARD, outline=LINE, width=1)
    d.rectangle((x + 20, y1 - 50, x + 260, y1 - 28), fill=(80, 70, 180))
    d.rectangle((x + 268, y1 - 50, x + 480, y1 - 28), fill=(40, 140, 160))
    d.rectangle((x + 488, y1 - 50, x + 640, y1 - 28), fill=(180, 150, 60))
    d.text((x + 28, y1 - 48), "Screen   Talk   Logo", font=font(14, bold=True), fill=TEXT)


def draw_notes(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    notes = ["Target accounts", "Brief", "Map", "Start"]
    for i, name in enumerate(notes):
        on = i == 1
        ny = y + i * 70
        rr(d, (x, ny, x + 240, ny + 56), 12, fill=(28, 30, 36) if on else CARD, outline=LINE, width=1)
        d.text((x + 16, ny + 16), name, font=font(18, bold=True), fill=TEXT if on else MUTED)
    d.text((x + 268, y), "Brief  -  Q3 desk", font=font(24, bold=True), fill=TEXT)
    d.text((x + 268, y + 56), "Target: EU SaaS, 50-200 seats.", font=font(18), fill=MUTED)
    d.text((x + 268, y + 92), "Block -> open a Code mission.", font=font(18), fill=MUTED)
    d.text((x + 268, y + 128), "Files stay on disk.", font=font(18), fill=MUTED)
    rr(d, (x + 268, y + 200, x1, y + 268), 14, fill=(18, 32, 22), outline=GREEN, width=2)
    d.text((x + 288, y + 224), "This block starts a Code mission", font=font(18, bold=True), fill=GREEN)


def draw_meeting(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    bars = 28
    gap = 8
    bw = max(6, (x1 - x - gap * (bars - 1)) // bars)
    for i in range(bars):
        hgt = 18 + ((i * 17) % 70)
        bx = x + i * (bw + gap)
        d.rectangle((bx, y + 90 - hgt, bx + bw, y + 90), fill=GREEN)
    d.text((x, y + 120), "Ship the pricing page. Keep your keys on Free.", font=font(20), fill=TEXT)
    d.text((x, y + 156), "Open a Code mission after the call.", font=font(20), fill=TEXT)
    rr(d, (x, y + 220, x1, y + 278), 12, fill=(18, 32, 22), outline=LINE, width=1)
    d.text((x + 20, y + 240), "Action  -  ship pricing page", font=font(18, bold=True), fill=GREEN)
    rr(d, (x, y + 294, x1, y + 352), 12, fill=(18, 32, 22), outline=LINE, width=1)
    d.text((x + 20, y + 314), "Action  -  open a Code mission", font=font(18, bold=True), fill=GREEN)


def draw_documents(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    kinds = ["PPTX", "DOCX", "XLSX", "PDF"]
    for i, kind in enumerate(kinds):
        on = i == 0
        bx = x + i * 110
        rr(d, (bx, y, bx + 96, y + 36), 10, fill=(28, 30, 36) if on else CARD, outline=LINE, width=1)
        d.text((bx + 18, y + 8), kind, font=font(15, bold=True), fill=TEXT if on else MUTED)
    cx = x + 80
    rr(d, (cx, y + 64, x1 - 80, y1), 18, fill=CARD, outline=LINE, width=2)
    d.text((cx + 36, y + 88), "PPTX", font=font(14, bold=True), fill=MUTED)
    d.text((cx + 36, y + 124), "Cover  -  Navin desk", font=font(28, bold=True), fill=TEXT)
    d.text((cx + 36, y + 176), "One AGI. Six seats gone.", font=font(20), fill=MUTED)


def draw_risk(d: ImageDraw.ImageDraw, x: int, y: int, x1: int, y1: int) -> None:
    d.text((x, y), "Launch score", font=font(16), fill=MUTED)
    d.text((x, y + 28), "94", font=font(56, bold=True), fill=GREEN)
    d.text((x + 200, y + 56), "Reads the plan first", font=font(16), fill=MUTED)
    rows = [
        ("Secret left in settings", "fixed"),
        ("Risky dependency lodash@4.17.15", "fixed"),
        ("Plan breaks in month 6", "rewritten"),
        ("Open redirect on /next", "med"),
    ]
    for i, (title, state) in enumerate(rows):
        ry = y + 120 + i * 72
        rr(d, (x, ry, x1, ry + 60), 12, fill=CARD, outline=LINE, width=1)
        d.text((x + 20, ry + 18), title, font=font(18, bold=True), fill=TEXT)
        color = GREEN if state != "med" else (232, 140, 140)
        d.text((x1 - 140, ry + 18), state, font=font(16, bold=True), fill=color)


MAIN = {
    "code": draw_code,
    "tenders": lambda d, x, y, x1, y1: draw_desk(
        d,
        x,
        y,
        x1,
        y1,
        [("Open", "12"), ("Qualified", "4"), ("Score", "82")],
        "Official collect",
        "Cloud data platform",
        "TED  -  FR",
        "Go / No-Go",
        "GO",
        "Approval before send",
    ),
    "career": lambda d, x, y, x1, y1: draw_desk(
        d,
        x,
        y,
        x1,
        y1,
        [("Live", "18"), ("Prepared", "5"), ("Sent", "3")],
        "Official search",
        "Senior frontend remote",
        "Remotive  -  EU",
        "This mission",
        "Word CV ready",
        "Review, then you click apply",
    ),
    "trading": lambda d, x, y, x1, y1: draw_desk(
        d,
        x,
        y,
        x1,
        y1,
        [("Equity", "EUR 20,000"), ("Cash", "EUR 18,420"), ("Day P/L", "+0.0%")],
        "Paper  -  public APIs",
        "AAPL",
        "Yahoo",
        "Live quote",
        "189.42",
        "Paper only. Never a live order.",
    ),
    "leads": draw_leads,
    "marketing": draw_marketing,
    "ads": draw_ads,
    "seo": draw_seo,
    "scraping": draw_scraping,
    "montage": draw_montage,
    "notes": draw_notes,
    "meeting": draw_meeting,
    "documents": draw_documents,
    "risklens": draw_risk,
}


def draw_studio(active: str) -> Image.Image:
    w, h = 1400, 780
    im = Image.new("RGB", (w, h), (232, 228, 218))
    d = ImageDraw.Draw(im)

    x0, y0, x1, y1 = 28, 28, w - 28, h - 28
    rr(d, (x0, y0, x1, y1), 22, fill=BG, outline=(30, 32, 38), width=2)
    for i, color in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        d.ellipse((x0 + 22 + i * 22, y0 + 18, x0 + 36 + i * 22, y0 + 32), fill=color)

    d.text((x0 + 110, y0 + 14), TITLES[active], font=font(20, bold=True), fill=TEXT)

    sx1 = x0 + 236
    d.rectangle((x0 + 1, y0 + 52, sx1, y1 - 1), fill=(12, 14, 18))
    d.line([(sx1, y0 + 52), (sx1, y1 - 1)], fill=LINE, width=1)
    small = font(16)
    bold = font(16, bold=True)
    iy = y0 + 64
    for key, label, child in NAV:
        on = key == active
        indent = 22 if child else 0
        if on:
            rr(d, (x0 + 10, iy - 6, sx1 - 10, iy + 28), 12, fill=(32, 34, 40), outline=LINE, width=1)
            d.ellipse((sx1 - 28, iy + 6, sx1 - 18, iy + 16), fill=GREEN)
        d.text(
            (x0 + 20 + indent, iy),
            label,
            font=bold if on or key is None else small,
            fill=TEXT if on or key is None else MUTED,
        )
        iy += 42

    mx = sx1 + 24
    my = y0 + 68
    d.text((mx, my), f"#{active}", font=font(16, bold=True), fill=MUTED)
    MAIN[active](d, mx, my + 46, x1 - 28, y1 - 28)
    return im


def make_studio_gif() -> None:
    frames = [draw_studio(key) for key in CYCLE]
    save_gif(ROOT / "navin.gif", frames, 1100)


if __name__ == "__main__":
    make_loop_gifs()
    make_studio_gif()
