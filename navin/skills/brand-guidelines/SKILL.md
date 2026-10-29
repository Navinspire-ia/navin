---
name: brand-guidelines
description: Defines the Navin brand (ink-on-paper palette with one lime accent, Inter and IBM Plex typography, spacing scale, wordmark rules, tone of voice) and shows how to apply it in HTML/CSS, python-pptx, python-docx, reportlab and matplotlib. Use when the user asks to "use the Navin colors", "make it on-brand", "apply our brand style", "style this deck like Navin" or when any document, slide, chart or interface needs a consistent house look. In French "applique la charte graphique Navin", "utilise les couleurs et la typographie Navin" or "mets ce document aux couleurs de la marque".
metadata: {"navin":{"emoji":"🎨","category":"design"}}
---

# Navin Brand Guidelines

Apply this identity to every artifact the agent produces for the house: slides, documents, PDF reports, charts, HTML pages, generated images, UI mockups. The look is ink on paper: near-black surfaces or warm off-white pages, generous whitespace, calm sans-serif type, and exactly one lime accent that marks what matters. No gradients by default, no decorative shadows, no second accent competing with lime.

Tokens live in `assets/navin-tokens.css` (CSS custom properties, light and dark) and `assets/navin-tokens.json` (the same values for Python and templating). Read from those files rather than retyping hex codes.

## Workflow

1. Decide the mode: **light** (Paper page, Ink text) for documents, reports, printed decks; **dark** (Ink page, Paper text) for screens, viewers, dashboards, hero slides.
2. Load tokens. In HTML, link or inline `assets/navin-tokens.css` and set `data-theme="dark"` on `<html>` when needed. In Python, `json.load(open("navin/skills/brand-guidelines/assets/navin-tokens.json"))` and read `colors` and `fonts`.
3. Lay out with the spacing scale and the type scale below. Leave more room than feels necessary; the brand breathes.
4. Use Lime once per view: the key number, the active state, the single call to action, the headline underline. Everything else is Ink, Paper and greys.
5. Reserve Leaf, Amber and Coral for semantic meaning only (success, warning, error). Teal, Sky and Violet are data-series colors for charts and diagrams.
6. Check contrast with the pairs table before shipping. Check the wordmark rules if the name appears as a logo.
7. Render and look at it (browser screenshot, present_artifact) before handing it over.

## Palette and roles

| Token | Hex | Role |
|---|---|---|
| Ink | `#17171a` | Primary text on light; primary surface in dark mode |
| Graphite | `#121214` | Deeper dark surface (sidebars, code blocks, cards on Ink) |
| Paper | `#f4f3ee` | Primary light surface; primary text on dark |
| Cream | `#faf9f6` | Raised light surface (cards, inputs on Paper) |
| Lime | `#c6e88a` | Signature accent: one highlight per view, buttons, active states, key figures |
| Teal | `#0e7490` | Links on light, data series 1 |
| Sky | `#0ea5e9` | Links on dark, data series 2, informational states |
| Leaf | `#6fbf6a` | Success |
| Amber | `#febc2e` | Warning |
| Coral | `#ff5f57` | Error, destructive actions |
| Violet | `#7c3aed` | Data series 3, rare emphasis in diagrams |

Greys are derived, never invented: text-muted is Ink at 60 percent on Paper (`#5f5f63`), Paper at 56 percent on Ink (`#9a9a96`); hairlines are Ink at 12 percent (`#d9d8d3`) or Paper at 12 percent (`#2a2a2e`).

### Light and dark pairs

| Purpose | Light | Dark |
|---|---|---|
| Page | Paper | Ink |
| Card / input | Cream | Graphite |
| Text | Ink | Paper |
| Muted text | `#5f5f63` | `#9a9a96` |
| Hairline | `#d9d8d3` | `#2a2a2e` |
| Accent fill | Lime with Ink text | Lime with Ink text |
| Link | Teal | Sky |

### Contrast (WCAG)

- Ink on Paper 16.1:1, Paper on Ink 16.1:1 - AAA for all text.
- Ink on Lime 13.0:1 - AAA. Always put Ink text on Lime fills; never Paper on Lime (1.2:1, fails).
- Teal on Paper 4.8:1 - AA for body text. Sky on Ink 6.5:1 - AA.
- Muted `#5f5f63` on Paper 5.7:1 and `#9a9a96` on Ink 6.3:1 - AA.
- Coral on Paper 2.7:1, Amber on Paper 1.5:1 and Leaf on Paper 2.0:1 do not pass for text; use them as fills with Ink text, or as 2 px borders and icons next to Ink labels.
- Lime as text on Ink 13.0:1 passes; use it only for short labels or figures, never paragraphs.

## Typography

- UI and body: **Inter**, fallback **IBM Plex Sans**, then `system-ui, sans-serif`.
- Code, figures, tables of numbers: **IBM Plex Mono** or **JetBrains Mono**, fallback `monospace`. Use tabular figures (`font-variant-numeric: tabular-nums`).
- Long-form editorial, quotes, cover titles when a warmer voice is wanted: **Lora** or **Crimson Pro**, fallback `Georgia, serif`.

Scale (px, line-height): display 56/1.05, h1 40/1.1, h2 28/1.2, h3 20/1.3, body 16/1.55, small 14/1.5, caption 12/1.4, code 13/1.6. Weights: 400 body, 500 labels, 600 headings. Letter-spacing: -0.02em on display and h1, 0 on body, +0.08em on uppercase captions. Never use bold 700 or above; it fights the ink-on-paper calm.

Slides (16:9, 1920x1080): title 64, section header 44, body 28, caption 18, in points for python-pptx divide by roughly 1.33.

## Spacing and shape

Base unit 4 px. Scale: 4, 8, 12, 16, 24, 32, 48, 64, 96. Page margins: 64 px on screens, 24 mm on A4, 0.75 in on slides. Component padding 12/16, card padding 24, section gap 48. Radii: 6 px for controls, 8 px for cards, 12 px for large panels; no pill buttons. Borders are 1 px hairlines; drop shadows are off by default, and when depth is unavoidable use `0 1px 2px rgba(23,23,26,.08)`.

## Wordmark and logo

- The wordmark is the word **Navin** set in Inter 600, letter-spacing -0.02em, Ink on light surfaces and Paper on dark. A Lime period after the word (`Navin.`) is the only permitted decoration, and only in display sizes.
- Clear space: at least the cap height of the N on every side. Minimum size 20 px on screen, 6 mm in print.
- Never recolor the wordmark into Teal, Coral or a gradient. Never outline it, rotate it, add a glow, or set it in a serif.
- Do not place the wordmark on Lime; place it on Paper or Ink.
- When a mark is required and no logo file is available, use the wordmark alone. Do not invent a symbol.

## Tone of voice

Plain, precise, confident, unhurried. Short sentences. Concrete nouns and verbs. Numbers over adjectives. Say what the thing does, not how exciting it is. Address the reader directly in interfaces ("Save", "Open the report") and in the third person in product copy ("Navin runs the suite"). No exclamation marks, no emoji in product surfaces, no marketing superlatives.

## Do and don't

Do:
- One Lime element per screen or slide.
- Big quiet margins; let one headline carry a slide.
- Tables with hairlines only, no zebra fills.
- Charts with Teal, Sky, Violet series on Paper, labels in Ink, grid in hairline grey.
- Dark mode for viewers and dashboards, light mode for anything printed.

Don't:
- Gradients, glassmorphism, neon glows, drop shadows under text.
- Lime as a background for paragraphs or as the page color.
- Coral or Amber as decoration; they mean error and warning.
- Mixed accent colors on buttons; secondary buttons are outlined Ink or Paper.
- Centered body text or justified paragraphs.

## Applying the brand

**HTML / CSS.** Inline `assets/navin-tokens.css`, then:

```css
body { background: var(--navin-bg); color: var(--navin-fg); font-family: var(--navin-font-sans); }
.cta { background: var(--navin-accent); color: var(--navin-ink); border-radius: var(--navin-radius-control); }
```

**python-pptx.** Read the JSON and set colors via `RGBColor.from_string(hex[1:])`:

```python
tokens = json.load(open("navin/skills/brand-guidelines/assets/navin-tokens.json"))
c = tokens["colors"]
slide.background.fill.solid(); slide.background.fill.fore_color.rgb = RGBColor.from_string(c["paper"][1:])
run.font.name = "Inter"; run.font.size = Pt(28); run.font.color.rgb = RGBColor.from_string(c["ink"][1:])
```

**python-docx.** `run.font.name = "Inter"`, `run.font.color.rgb = RGBColor(0x17, 0x17, 0x1a)`, heading styles 600 weight via `run.font.bold = False` plus a semibold font variant when installed; margins `section.left_margin = Mm(24)`.

**reportlab.** Register Inter if the TTF is present (`pdfmetrics.registerFont(TTFont("Inter", path))`), else Helvetica. `canvas.setFillColor(HexColor(c["ink"]))`, page background `HexColor(c["paper"])`, accent rule `HexColor(c["lime"])` 2 pt wide under the title.

**matplotlib.**

```python
plt.rcParams.update({"font.family": "Inter", "axes.facecolor": c["paper"], "figure.facecolor": c["paper"],
                     "axes.edgecolor": "#d9d8d3", "axes.prop_cycle": plt.cycler(color=[c["teal"], c["sky"], c["violet"], c["lime"]])})
```

Hide the top and right spines, keep the grid at `#d9d8d3` alpha 0.6, and highlight the one series that matters in Lime.

**generate_image prompts.** Describe the palette in words: "near-black ink background, warm off-white paper shapes, a single pale lime accent, flat, no gradients, generous negative space".

## Files

- `assets/navin-tokens.css` - custom properties for light and dark, fonts, spacing, radii.
- `assets/navin-tokens.json` - the same tokens as data for Python and templating.
