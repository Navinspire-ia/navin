# Slide recipes and Navin theme rules

## Theme

| Element | Value |
|---------|-------|
| Format | 16:9, 13.333 x 7.5 in |
| Margins | 0.7 in left and right, 0.6 in top, 0.5 in bottom |
| Surfaces | Content slides on Paper `#f4f3ee`; title, section and closing slides on Ink `#17171a` when `dark_sections` is true |
| Text | Ink `#17171a` on Paper; Paper `#f4f3ee` on Ink; secondary text Graphite `#121214` at 70 percent or `#6b6b70` |
| Accent | One per deck: Lime `#c6e88a` (default), Teal `#0e7490`, Sky `#0ea5e9`, Violet `#7c3aed`, Coral `#ff5f57`, Amber `#febc2e` |
| Semantic | Positive Leaf `#6fbf6a`, warning Amber `#febc2e`, negative Coral `#ff5f57` |
| Fonts | Inter or IBM Plex Sans for everything; IBM Plex Mono for code or identifiers |
| Type scale | Display 44 pt, title 32 pt, subtitle 20 pt, body 18 pt, secondary 14 pt, footer 10 pt |
| Rhythm | Title block at the top, 0.3 in gap, content below; a 4 pt accent bar under the title on content slides |

Rules: no gradients, no drop shadows, no clip art, no more than two font sizes in a body block, left-aligned text (centered only on title, section, big number and quote slides).

## Slide types

### title

Fields: `title`, `subtitle`, `footer` (date, event or author). Dark surface, display size title bottom-left, subtitle below in the accent color, footer small at the bottom. One slide per deck.

### agenda

Fields: `title` (default "Agenda"), `items` (3 to 7). Numbered list with accent numerals; keep each item under 6 words.

### section

Fields: `title`, `number` ("01", "02"). Dark surface, large numeral in the accent color, section title in display size. Use when the deck has more than 8 content slides.

### bullets

Fields: `title`, `bullets` (strings; a nested list makes a second level), `notes`. Maximum 6 first-level bullets, 2 levels deep. Bullets are statements, not fragments followed by a colon. When more than 6 points are needed, split into two slides or use `two_column`.

### two_column

Fields: `title`, `left_title`, `left` (list), `right_title`, `right` (list). Before/after, worked/did not, pros/cons. Equal columns, titles in accent-colored small caps.

### comparison

Fields: `title`, `headers` (first cell empty, then option names), `rows` (first cell criterion, then one cell per option). Rendered as a table with the header filled Ink and Paper text, zebra rows. Up to 4 options and 6 criteria.

### big_number

Fields: `title`, `value` ("118%", "3.4 M"), `label`, `trend` ("+6 pts vs last year"). One metric per slide, value in display size and accent color, trend colored Leaf when positive and Coral when negative.

### quote

Fields: `quote`, `author`. Serif or large body text, centered, opening quotation mark in accent. Keep under 30 words.

### image

Fields: `title`, `image` (path, must exist), `caption`. The picture is fitted inside the content area with its aspect ratio kept; caption in secondary size. Prefer PNG or JPEG above 1600 px wide. Generate missing visuals with `generate_image` before building.

### table

Fields: `title`, `headers`, `rows`. Header Ink with Paper text, body 14 pt, right-aligned numbers, zebra rows in Cream `#faf9f6`. Up to 8 rows and 6 columns; beyond that, split or summarise.

### chart

Fields: `title`, `chart` (`column`, `bar`, `line`, `pie`, `doughnut`), `categories`, `series` (name to list of numbers), `number_format` ("0.0", "#,##0", "0%"), `legend` (true or false). Native python-pptx charts, series colored from the palette in order (accent, Teal, Violet, Amber, Leaf). One message per chart; put it in the title ("Revenue grew 18 percent"), not "Revenue by quarter".

### closing

Fields: `title` ("Thank you", "Next steps"), `subtitle`, `contact`. Dark surface, same composition as the title slide.

## Composition checks before delivering

1. Every slide answers one question stated in its title.
2. No text below 14 pt, no more than 40 words of body per slide.
3. Numbers carry a unit and a period ("Q3 2026", "EUR k").
4. The accent color appears on every slide at least once and never for more than one role.
5. Images are not stretched; charts have a readable legend or direct labels.
6. Speaker notes hold the detail the slide does not show.
7. Thumbnail sheet reviewed, or outline reviewed when rendering is unavailable.

## Writing the spec quickly

Start from `pptx_build.py --spec-example`, delete the types that are not needed, keep the order title, agenda, sections with 2 to 5 content slides each, closing. Translate every string into the deck language before building; the `language` field is recorded in the file properties.
