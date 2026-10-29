---
name: pptx
description: Works on PowerPoint decks that already exist or must follow a strict JSON plan. Inventories slides and shapes, replaces text in place while keeping formatting, deletes and reorders slides, rebuilds a deck from a JSON spec with the Navin theme (twelve slide types from title to closing, native tables and charts), renders a contact sheet of thumbnails, repacks and validates the package. Use when the user asks to "update the numbers in this deck", "list the slides of the pptx", "rebuild the presentation from this outline", "show me thumbnails of the slides" or "fix this corrupted pptx". In French "remplace les textes de cette présentation", "liste les slides du pptx", "génère les vignettes des slides" or "répare ce PowerPoint corrompu". Story and slide plan from a brief belong to presentation-designer and pptx-generator.
metadata: {"navin":{"emoji":"🗂️","category":"documents"}}
---

# PPTX Toolkit

## Overview

This skill operates on decks: the ones the user sends (inventory, surgical edits, thumbnails) and the ones built from a structured spec (JSON to .pptx with the Navin theme). It keeps masters, layouts and formatting intact and changes only what was asked. Scripts run with `exec` on the bundled python-pptx, lxml and PyMuPDF libraries.

Pick the sibling skill when the job is different:

- `presentation-designer`: turn a brief into a story and a slide plan.
- `pptx-generator`: free-form python-pptx code for a new deck with custom layouts, charts and brand templates.
- `pdf`: the input is a PDF export of a deck.

## Workflow

### 1. Inventory before editing

```
python3 navin/skills/pptx/scripts/pptx_inventory.py deck.pptx --markdown
```

Per slide: layout name, every shape with its id, name, type, position, placeholder type, text and whether it holds a picture. Use `--slide 3` to focus and drop `--markdown` for JSON. Shape ids and names are what the edit script targets, so quote them from this output rather than guessing.

When the deck needs a visual review, render it: `python3 navin/skills/pptx/scripts/pptx_thumbnails.py deck.pptx --out deck-thumbs.png --cols 4` builds a contact sheet with slide numbers (LibreOffice to PDF, PyMuPDF to PNG); `--per-slide` also writes one PNG per slide. Look at the PNG with `read_file`. Without LibreOffice the script exits with code 3; use `--outline` to get a text outline instead and say that no rendering was possible.

### 2. Edit in place

Write the edits as a JSON list and apply them, always to a new file unless the user wants the original overwritten:

```
python3 navin/skills/pptx/scripts/pptx_replace.py deck.pptx --edits edits.json --out deck-v2.pptx
```

Edit forms:

- `{"slide": 2, "shape_id": 5, "text": "New title"}` replaces the whole text, keeping the formatting of the first run.
- `{"slide": 2, "shape_name": "Content Placeholder 2", "paragraphs": ["first bullet", "second bullet"]}` rewrites the bullet list, reusing the first paragraph's level and style.
- `{"slide": 3, "shape_name": "Title 1", "find": "2025", "replace": "2026"}` replaces a substring inside the runs.

`--delete-slide 7` removes a slide (1-based) and `--reorder 3,1,2` applies a new order; both can be combined with `--edits`. Re-run the inventory on the output to confirm the change.

### 3. Build from a spec

For a deck that must follow a precise plan (a report, a recurring review, a translated copy), write a JSON spec and build it:

```
python3 navin/skills/pptx/scripts/pptx_build.py --spec-example > spec.json   # full example to adapt
python3 navin/skills/pptx/scripts/pptx_build.py --spec spec.json --out deck.pptx --json
```

The spec holds `title`, `language`, `author`, a `theme` block (`accent`: lime, teal, sky, violet, coral, amber; `dark_sections`: true or false) and a `slides` list. Slide types: `title`, `agenda`, `section`, `bullets` (nested lists allowed), `two_column`, `comparison`, `big_number`, `quote`, `image`, `table`, `chart` (column, bar, line, pie, doughnut, with `categories` and `series`), `closing`. Each slide accepts `notes` for speaker notes. `--template base.pptx` reuses the masters of an existing deck when the user has a corporate template.

The theme is the Navin look: 16:9, Ink `#17171a` sections and title, Paper `#f4f3ee` content slides, one accent color, Inter or IBM Plex Sans, generous margins. Composition rules and the content each type expects are in `references/slide-recipes.md`; read it before writing a spec.

### 4. Package-level fixes

When python-pptx has no API (broken relationships, orphan media, custom XML, a layout to rename):

1. `python3 navin/skills/pptx/scripts/office_pkg.py unpack deck.pptx work/` (pretty XML).
2. Edit `work/ppt/slides/slideN.xml`, `work/ppt/presentation.xml` (slide order lives in `p:sldIdLst`) or the `.rels` files with `edit_file`.
3. `office_pkg.py pack work/ fixed.pptx` then `office_pkg.py validate fixed.pptx` (zip, content types, relationships, XML, python-pptx round trip).

### 5. Convert and deliver

`python3 navin/skills/pptx/scripts/office_convert.py deck.pptx --to pdf --out-dir out/` produces the PDF through headless LibreOffice and says clearly when `soffice` is missing. Deliver the .pptx (and the PDF when asked), a thumbnail sheet or outline, and a short list of what changed per slide.

## Scripts

| Script | Command |
|--------|---------|
| `scripts/pptx_inventory.py` | `FILE [--slide N] [--markdown]` |
| `scripts/pptx_replace.py` | `FILE [--edits edits.json] [--delete-slide N] [--reorder 3,1,2] [--out F]` |
| `scripts/pptx_build.py` | `--spec spec.json --out deck.pptx [--template base.pptx] [--json]`; `--spec-example` |
| `scripts/pptx_thumbnails.py` | `FILE [--out sheet.png] [--cols N] [--dpi N] [--per-slide] [--outline] [--json] [--timeout S]` (exit 3 without LibreOffice unless `--outline`) |
| `scripts/office_pkg.py` | `unpack ARCHIVE DIR`; `pack DIR ARCHIVE`; `validate ARCHIVE [--json]` |
| `scripts/office_convert.py` | `SOURCE --to pdf\|pptx [--out-dir D] [--timeout S] [--json]` |

## Rules

- Language gate: when slides are created or text is written, ask for the deck language if the user did not state it, and keep one language per deck.
- Never write em dashes (U+2014) or en dashes (U+2013) on slides or in notes. Use "-" or rephrase.
- One idea per slide, at most six bullets, no paragraph walls; move detail to speaker notes.
- Keep decks editable: real text boxes, native tables and charts, never a slide pasted as an image.
- In-place edits preserve the original formatting; do not restyle a user's deck unless asked.
- Images referenced in a spec must exist; the build script fails on a missing path rather than leaving an empty frame. Generate missing visuals with `generate_image` first.
- Validate the package after any unpack/pack cycle and review the thumbnails (or the outline) before delivering.

## References

- `references/slide-recipes.md`: the twelve slide types, what each expects, Navin theme rules (16:9 grid, margins, type scale, color roles) and composition checks.
