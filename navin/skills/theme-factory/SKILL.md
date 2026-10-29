---
name: theme-factory
description: Applies one of ten ready-made color and type themes (Navin Studio as the default, plus Ink and Lime, Harbor, Terracotta, Moss, Slate, Aurora, Dune, Orchard, Midnight) to slides, documents, reports and HTML pages, or derives a new theme in the same format. Use when the user asks to "pick a theme for this deck", "restyle the report", "show me the available themes", "make a custom color scheme" or wants consistent styling across an artifact. In French "applique un thème à ce document", "propose une palette de couleurs", "change le thème du deck" or "montre les thèmes disponibles".
metadata: {"navin":{"emoji":"🎛️","category":"design"}}
---

# Theme Factory

Ten themes, one file each in `themes/`, every file with the same shape: a name, a one-line mood, five colors with roles (Background, Surface, Text, Accent, Secondary), a heading and body font pairing, a light/dark note and a "best for" line. Apply a theme to any artifact the agent is producing: a python-pptx deck, a python-docx or reportlab document, an HTML page, a matplotlib chart. `navin-studio` is the house theme and the default whenever the user has no preference.

## Workflow

1. **Show the choices.** Present `assets/theme-showcase.html` with present_artifact (`type=file`) or open it in the browser tool and screenshot it. The page has one card per theme: swatches, a type sample and a mini slide mock. If the file is missing or themes changed, rebuild it first: `python3 navin/skills/theme-factory/scripts/build_showcase.py`.
2. **Ask which theme**, unless the user already named one or the brand is fixed (then use `navin-studio` and say so). Wait for an explicit pick; do not restyle a deck on a guess.
3. **Read the theme file** `themes/<id>.md` and extract the five hex values and two fonts. `python3 navin/skills/theme-factory/scripts/build_showcase.py --json` prints every theme as structured data when the values are needed in code.
4. **Apply consistently** across the whole artifact: ground, surface, text, one accent, one secondary. Heading font on titles and section heads, body font on everything else. Check the contrast of Text on Background and of the pill or button text on Accent; swap to the theme's dark variant when the artifact is a screen rather than a page.
5. **Render and check.** Screenshot a slide or page and look for stray default colors (pure black, pure white, theme-less blue links) before handing it over.

## The themes

| File | Name | Mood | Mode |
|---|---|---|---|
| `themes/navin-studio.md` | Navin Studio | ink on paper, one lime accent; the default | light |
| `themes/ink-and-lime.md` | Ink and Lime | the house palette on dark; for screens and talks | dark |
| `themes/harbor.md` | Harbor | cold water and steel; trustworthy | light |
| `themes/terracotta.md` | Terracotta | baked clay and warm plaster | light |
| `themes/moss.md` | Moss | forest floor, soft greens | light |
| `themes/slate.md` | Slate | grayscale with a violet pulse | light |
| `themes/aurora.md` | Aurora | violet and sky over near-black | dark |
| `themes/dune.md` | Dune | amber light on sand | light |
| `themes/orchard.md` | Orchard | leaf and lime on cream, friendly | light |
| `themes/midnight.md` | Midnight | deep navy and cold starlight | dark |

## Applying a theme

**Slides (python-pptx).** Background fill on every slide layout from `Background`; title runs in the heading font, `Text` color; body runs in the body font. One shape per slide may use `Accent` (a rule under the title, a highlighted number, a button); charts use `Accent` then `Secondary` then `Text` at 60 percent. Cards and callouts sit on `Surface`. Sizes: title 40 pt, body 20 pt, caption 12 pt.

**Documents (python-docx, reportlab).** Page stays `Background` only when it is light; for dark themes keep the page white or Paper and use the theme's light variant from the Mode line, because dark pages print badly. Headings in the heading font and `Text`; a 2 pt rule in `Accent` under the H1; table header rows filled with `Surface`; links in `Secondary`.

**HTML.** Declare the five colors as CSS variables (`--bg`, `--surface`, `--fg`, `--accent`, `--secondary`) and the two fonts as `--font-heading`, `--font-body`; set `data-theme="<id>"` on `<html>` so the choice is visible in the source. Load fonts by name from the system or with `@font-face` pointing at files fetched by the canvas-design skill's `fetch_fonts.py`; never hotlink a font service.

**Charts (matplotlib).** `figure.facecolor` and `axes.facecolor` from `Background`, spines and ticks from `Text` at 40 percent, series cycle `[Accent, Secondary, Text]`, title in the heading font.

The theme fixes colors and fonts; it does not fix layout. Keep generous margins, left-aligned text, hairline tables, no gradients, no drop shadows.

## Creating a theme

When none fits, write a new `themes/<id>.md` in the exact same structure (copy `themes/navin-studio.md` and change the values). Rules of thumb: one light ground and one slightly darker surface (or the reverse for dark themes), text with at least 7:1 contrast on the ground, an accent that is clearly a different hue from the secondary, and a serif-sans or sans-mono pairing from the curated list in the canvas-design skill. Give it a two-word mood and a one-word or two-word name that evokes a place or material. Then run `python3 navin/skills/theme-factory/scripts/build_showcase.py` so the showcase includes it, show the card to the user for approval, and apply it as above.

## Files

- `themes/*.md` - the ten theme definitions (parsed by the script; keep the format).
- `scripts/build_showcase.py` - parses `themes/*.md`, writes `assets/theme-showcase.html`; `--json` dumps the themes, `--check` verifies the committed page is current.
- `assets/theme-showcase.html` - the generated, self-contained showcase to present to the user.
