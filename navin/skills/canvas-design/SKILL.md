---
name: canvas-design
description: Designs posters, covers, title cards and single-page visual canvases as one PNG or PDF - a short visual philosophy first, then a composition rendered with HTML+CSS in the headless browser or with Pillow from a JSON spec, using open-licensed fonts and the Navin palette. Use when the user asks to "design a poster", "make a cover", "create a title card", "produce a visual for this event" or wants a design-forward static piece with minimal text. In French "conçois une affiche", "crée une couverture", "fais un visuel pour cet événement" or "produis un poster en PNG".
metadata: {"navin":{"emoji":"🖼️","category":"design"}}
---

# Canvas Design

Make art objects, not documents with decoration. A canvas from this skill is a single page (poster, cover, title card, print) where form, space and color carry the meaning and text is a rare, deliberate gesture. Two outputs: a short visual philosophy in markdown and one PNG or PDF. Never copy an existing poster or a named designer's work; build an original composition from the brief.

## Workflow

1. **Read the brief for its quiet thread.** What is the subject, the occasion, the format (A3 portrait, 1920x1080 cover, 1080x1350 social)? Pull out one conceptual reference that an insider would feel without a caption: a proportion, a rhythm, a material, a direction.
2. **Write the visual philosophy** (3-5 paragraphs, saved as `<name>-philosophy.md`). Name the movement in two words. Describe how it shows in space and form, color and material, scale and rhythm, hierarchy. Say each thing once. Keep it generic enough to be reused; the subject enters through execution, not through the text.
3. **Pick the renderer.**
   - HTML+CSS rendered by the browser tool: best for typographic posters, grids, CSS shapes, SVG patterns, web fonts. Write the file with write_file, `navigate` to `file:///...`, `screenshot` with `full_page=true` and `scale=2`, or `save_as_pdf` for print.
   - Pillow through `scripts/make_canvas.py`: best for pixel-exact prints, repeated marks, generative texture, and anything that must not depend on a browser. Write a JSON spec and run `python3 navin/skills/canvas-design/scripts/make_canvas.py spec.json out.png`.
4. **Get the fonts.** Choose a pairing from `references/fonts.md`. Run `python3 navin/skills/canvas-design/scripts/fetch_fonts.py --list` to see the manifest, then `python3 navin/skills/canvas-design/scripts/fetch_fonts.py "Crimson Pro" "IBM Plex Mono"` to download them into `~/.cache/navin/fonts` (idempotent; this is the only step that needs network). Reference the TTF paths in the spec or in `@font-face` rules.
5. **Compose.** Build the whole piece from three or four moves: a ground, one large form or field of repeated marks, a typographic anchor, one accent. Check the rules below.
6. **Render and inspect at full size.** Open the PNG with read_file or show it with present_artifact. Look for clipped text, uneven margins, colors that fight, anything that reads as default or template.
7. **Second pass: refine, do not add.** Adjust spacing, weight, alignment, tracking, opacity. If the instinct is to add another shape, stop and ask how the existing elements can be more exact.
8. **Deliver.** PNG at 2x for screens, PDF or 300 dpi PNG for print. Present with present_artifact and keep the philosophy file beside it.

## Composition rules

- **Margins.** Nothing touches the edge unless the bleed is the idea. Use a margin of 6-8 percent of the short side and keep it consistent on all four sides, or deliberately asymmetric (wide bottom for posters).
- **Grid.** Twelve columns or a modular square grid; align every element to it. Optical alignment beats mathematical alignment for type.
- **Hierarchy.** One element dominates; at most three size levels. If everything is large nothing is.
- **Repetition.** Fields of identical marks (lines, dots, arcs, glyphs) with one controlled variation (spacing, weight, rotation) give the sense of patient observation. Let the pattern breathe; density is not richness.
- **Color.** Two or three colors plus a ground. Start from the Navin palette: Paper `#f4f3ee` or Cream `#faf9f6` ground with Ink `#17171a` marks, or Ink ground with Paper marks; Lime `#c6e88a` as the one accent; Teal `#0e7490`, Violet `#7c3aed`, Coral `#ff5f57`, Amber `#febc2e` as the secondary tone when the brief calls for warmth or tension. Flat color, no gradients, no drop shadows.
- **Texture.** Fine grain, hairline rules, slight misregistration of a second color, or paper-like noise at low opacity give the hand-made feel. Keep it subtle enough to disappear at thumbnail size.
- **Text.** Few words. A title, maybe a date and a place, a small reference mark (an index, a coordinate, a catalogue number) set in mono. Thin to regular weights; wide tracking on small caps; tight tracking on display. Text sits inside the composition as a shape; it never floats as a caption.
- **Nothing overlaps by accident.** Overlaps are allowed only as a visible decision (type over a color block with clear contrast).

## Typography pairings

| Mood | Display | Text / labels |
|---|---|---|
| Navin house | Inter 600, tight | IBM Plex Mono for marks |
| Editorial, warm | Crimson Pro or Lora italic | Inter or IBM Plex Sans |
| Brutalist | Big Shoulders Display or Bricolage Grotesque | JetBrains Mono |
| Quiet, scientific | Instrument Serif | IBM Plex Mono |
| Expressive | Boldonse or Erica One (one word only) | Work Sans light |
| Technical | Tektur or Jura | Red Hat Mono |

All faces in `references/fonts.md` are under the SIL Open Font License; the file lists each role, a pairing and the download URL pattern.

## Pillow spec in brief

`python3 navin/skills/canvas-design/scripts/make_canvas.py --spec-example` prints a complete example. Layers are drawn in order: `rect`, `line`, `ellipse`, `grid` (repeated lines), `dots` (repeated circles), `text` (with `font`, `size`, `tracking`, `align`, `anchor`, `color`, `max_width`). Positions are in pixels or in fractions of the canvas (`"x": 0.5`). When a font file is missing the script falls back to Pillow's default face and prints a warning; fetch the font and run again for the final render.

For PDF output pass a `.pdf` destination; the script writes the PNG at the requested dpi and wraps it with Pillow's PDF writer.

## HTML route in brief

Set the body to the exact canvas size (`width: 1240px; height: 1754px` for A4 at 150 dpi), `overflow: hidden`, ground color on `html`. Load fonts with `@font-face { src: url("file:///home/.../fonts/CrimsonPro-Regular.ttf") }`. Use CSS grid for the structure and inline SVG for repeated marks. Screenshot with the browser tool at `scale=2`, or `save_as_pdf` with the page size matching the canvas.

## Files

- `references/fonts.md` - curated open-licensed fonts with roles, pairings, license notes and the raw download URL pattern.
- `scripts/fetch_fonts.py` - downloads selected fonts into `~/.cache/navin/fonts` (or `--dest`), `--list` prints the manifest, `--all` fetches everything, re-runs skip existing files.
- `scripts/make_canvas.py` - Pillow renderer from a JSON spec; `--spec-example` prints a Navin-palette example; writes PNG or PDF.
