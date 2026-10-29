#!/usr/bin/env python3
"""Build the theme showcase page from themes/*.md.

Usage:
    build_showcase.py                 write assets/theme-showcase.html
    build_showcase.py --out x.html    write elsewhere
    build_showcase.py --json          print the parsed themes as JSON
    build_showcase.py --check         exit 1 if the committed showcase is stale

No network, no external CSS: the page is self-contained. Fonts are requested by
name and fall back to system faces when not installed.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
THEMES_DIR = SKILL_DIR / "themes"
DEFAULT_OUT = SKILL_DIR / "assets" / "theme-showcase.html"

COLOR_RE = re.compile(r"^- \*\*(?P<role>[^*]+)\*\*:\s*`(?P<hex>#[0-9a-fA-F]{6})`\s*-\s*(?P<note>.*)$")
FONT_RE = re.compile(r"^- \*\*(?P<role>Heading|Body)\*\*:\s*(?P<font>.+)$")
REQUIRED_ROLES = ("Background", "Surface", "Text", "Accent", "Secondary")


def parse_theme(path: Path) -> dict:
    """Parse one theme file into a dict; raises ValueError when a field is missing."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    name = next((ln[2:].strip() for ln in lines if ln.startswith("# ")), None)
    if not name:
        raise ValueError(f"{path.name}: missing '# Name' heading")
    mood = next((ln.split(":", 1)[1].strip() for ln in lines if ln.startswith("Mood:")), "")
    colors: dict[str, dict] = {}
    fonts: dict[str, str] = {}
    section = ""
    mode_lines: list[str] = []
    best_lines: list[str] = []
    for ln in lines:
        if ln.startswith("## "):
            section = ln[3:].strip().lower()
            continue
        m = COLOR_RE.match(ln)
        if m and section == "colors":
            colors[m["role"]] = {"hex": m["hex"].lower(), "note": m["note"].strip()}
            continue
        f = FONT_RE.match(ln)
        if f and section == "typography":
            fonts[f["role"].lower()] = f["font"].strip()
            continue
        if section == "mode" and ln.strip():
            mode_lines.append(ln.strip())
        if section == "best for" and ln.strip():
            best_lines.append(ln.strip())
    missing = [r for r in REQUIRED_ROLES if r not in colors]
    if missing:
        raise ValueError(f"{path.name}: missing color roles {missing}")
    if "heading" not in fonts or "body" not in fonts:
        raise ValueError(f"{path.name}: missing Heading or Body font")
    mode = " ".join(mode_lines)
    dark = mode.lower().startswith("dark")
    return {
        "id": path.stem,
        "name": name,
        "mood": mood,
        "colors": colors,
        "fonts": fonts,
        "mode": mode,
        "dark": dark,
        "best_for": " ".join(best_lines),
    }


def load_themes(themes_dir: Path = THEMES_DIR) -> list[dict]:
    files = sorted(themes_dir.glob("*.md"))
    themes = [parse_theme(p) for p in files]
    # The house theme leads; the rest stay alphabetical.
    themes.sort(key=lambda t: (t["id"] != "navin-studio", t["id"]))
    return themes


CSS = """
:root { --ink:#17171a; --graphite:#121214; --paper:#f4f3ee; --cream:#faf9f6; --lime:#c6e88a; --muted:#5f5f63; --hair:#d9d8d3; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: var(--paper); color: var(--ink); font-family: Inter, "IBM Plex Sans", system-ui, sans-serif; padding: 64px; line-height: 1.5; }
header { max-width: 1200px; margin: 0 auto 48px; display: flex; align-items: baseline; justify-content: space-between; border-bottom: 1px solid var(--hair); padding-bottom: 16px; }
header h1 { font-size: 28px; font-weight: 600; letter-spacing: -0.02em; }
header h1 span { color: var(--lime); }
header p { color: var(--muted); font-size: 14px; }
.grid { max-width: 1200px; margin: 0 auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 24px; }
.card { background: var(--cream); border: 1px solid var(--hair); border-radius: 8px; padding: 24px; display: flex; flex-direction: column; gap: 16px; }
.card h2 { font-size: 20px; font-weight: 600; letter-spacing: -0.01em; display: flex; justify-content: space-between; align-items: baseline; }
.card h2 code { font-family: "IBM Plex Mono", "JetBrains Mono", monospace; font-size: 12px; color: var(--muted); font-weight: 400; }
.mood { font-size: 14px; color: var(--muted); }
.swatches { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
.swatch { display: flex; flex-direction: column; gap: 6px; font-size: 11px; color: var(--muted); }
.swatch i { display: block; height: 40px; border-radius: 6px; border: 1px solid rgba(23,23,26,.08); }
.swatch b { font-weight: 500; color: var(--ink); }
.swatch code { font-family: "IBM Plex Mono", monospace; }
.slide { aspect-ratio: 16 / 9; border-radius: 6px; padding: 7% 8%; display: flex; flex-direction: column; justify-content: space-between; border: 1px solid rgba(23,23,26,.08); }
.slide .kicker { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; opacity: .7; }
.slide .title { font-size: 26px; line-height: 1.1; font-weight: 600; letter-spacing: -0.02em; }
.slide .body { font-size: 12px; opacity: .85; max-width: 80%; }
.slide .rule { height: 3px; width: 56px; border-radius: 2px; }
.slide .foot { display: flex; justify-content: space-between; align-items: center; font-size: 10px; opacity: .7; }
.slide .pill { padding: 4px 10px; border-radius: 6px; font-size: 10px; font-weight: 600; }
.type { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; font-size: 12px; color: var(--muted); }
.type b { display: block; color: var(--ink); font-weight: 500; }
.meta { font-size: 12px; color: var(--muted); border-top: 1px solid var(--hair); padding-top: 12px; }
footer { max-width: 1200px; margin: 48px auto 0; color: var(--muted); font-size: 12px; }
"""


def render_card(theme: dict) -> str:
    c = {k: v["hex"] for k, v in theme["colors"].items()}
    e = html.escape
    swatches = "".join(
        f'<div class="swatch"><i style="background:{c[role]}"></i><b>{e(role)}</b><code>{c[role]}</code></div>'
        for role in REQUIRED_ROLES
    )
    heading = e(theme["fonts"]["heading"])
    body = e(theme["fonts"]["body"])
    slide = (
        f'<div class="slide" style="background:{c["Background"]};color:{c["Text"]};font-family:{body}, Inter, sans-serif">'
        f'<div><div class="kicker">{e(theme["name"])}</div>'
        f'<div class="rule" style="background:{c["Accent"]};margin:8px 0 10px"></div>'
        f'<div class="title" style="font-family:{heading}, Inter, serif">Quarterly review</div></div>'
        f'<div class="body">Three decisions, one number that matters, and the plan for next quarter.</div>'
        f'<div class="foot"><span style="color:{c["Secondary"]}">navin.studio</span>'
        f'<span class="pill" style="background:{c["Accent"]};color:{c["Background"] if theme["dark"] else c["Text"]}">Open</span></div>'
        f"</div>"
    )
    return (
        f'<article class="card" id="{e(theme["id"])}">'
        f'<h2>{e(theme["name"])}<code>themes/{e(theme["id"])}.md</code></h2>'
        f'<p class="mood">{e(theme["mood"])}</p>'
        f'<div class="swatches">{swatches}</div>'
        f"{slide}"
        f'<div class="type"><div><b style="font-family:{heading}, serif;font-size:18px">Heading Aa</b>{heading}</div>'
        f'<div><b style="font-family:{body}, sans-serif;font-size:18px">Body Aa</b>{body}</div></div>'
        f'<p class="meta">{e(theme["mode"])}</p>'
        f"</article>"
    )


def render_page(themes: list[dict]) -> str:
    cards = "\n".join(render_card(t) for t in themes)
    return (
        "<!DOCTYPE html>\n<html lang=\"en\">\n<head>\n<meta charset=\"UTF-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Navin theme showcase</title>\n"
        f"<style>{CSS}</style>\n</head>\n<body>\n"
        "<header><h1>Navin<span>.</span> theme showcase</h1>"
        f"<p>{len(themes)} themes, generated by scripts/build_showcase.py from themes/*.md</p></header>\n"
        f'<main class="grid">\n{cards}\n</main>\n'
        "<footer>Pick a theme by its file name, then apply its five colors and two fonts to the artifact. "
        "Fonts render from the system when installed; otherwise the browser substitutes.</footer>\n"
        "</body>\n</html>\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--themes", type=Path, default=THEMES_DIR, help="folder with theme .md files")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output HTML path")
    parser.add_argument("--json", action="store_true", help="print parsed themes as JSON instead of writing HTML")
    parser.add_argument("--check", action="store_true", help="do not write; exit 1 if --out differs from a fresh build")
    args = parser.parse_args(argv)

    themes = load_themes(args.themes)
    if args.json:
        print(json.dumps(themes, indent=2))
        return 0
    page = render_page(themes)
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != page:
            print(f"stale: {args.out} differs from a fresh build", file=sys.stderr)
            return 1
        print(f"up to date: {args.out}")
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    print(f"wrote {args.out} ({len(themes)} themes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
