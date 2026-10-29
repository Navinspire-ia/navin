---
name: algorithmic-art
description: Generates original algorithmic art with p5.js - seeded randomness, flow fields, particle systems, noise and parametric variation - packaged as a self-contained interactive HTML viewer with seed navigation, sliders and PNG export. Use when the user asks to "make generative art", "create a flow field", "build a particle system sketch", "write art with code" or wants a seeded, reproducible p5.js piece. In French "génère de l'art génératif", "crée un flow field avec p5.js" or "fais une oeuvre algorithmique reproductible".
metadata: {"navin":{"emoji":"🌀","category":"design"}}
---

# Algorithmic Art

Produce living algorithms, not decorated pictures. A piece made with this skill is a p5.js sketch whose every run is reproducible from a seed, whose character is controlled by a small parameter object, and whose beauty comes from the process (fields, forces, accumulation) rather than from a hand-placed composition. Deliver two things: a short algorithmic philosophy in markdown, and one self-contained HTML file built from `templates/viewer.html`.

Never imitate a named living artist or reproduce an existing work. Invent the system.

## Workflow

1. **Read the request for its seed idea.** Extract the mood, the subject, the constraints (colors, size, static or animated). Treat them as a foundation, not a cage. Identify one quiet conceptual thread that someone in the know would feel without it being spelled out (a rhythm, a ratio, a direction of flow).
2. **Write the algorithmic philosophy** (3-5 paragraphs, saved as `<name>-philosophy.md` with write_file). Give the movement a two-word name. Describe how it manifests through processes: which noise or field drives it, what entities exist, how they interact, what accumulates, when the system stops. Say each thing once. Leave implementation room.
3. **Choose the parameters.** Ask what the system needs to be tunable: counts, scales, speeds, probabilities, angles, thresholds, palette. Keep 4-8 numeric parameters plus the seed. Put them in one `params` object with `min`, `max`, `step` metadata so the viewer can build sliders automatically.
4. **Write the algorithm.** Open `templates/generator_template.js` and follow its structure: seeded PRNG, `params` object, `setup()` and `draw()`, entity classes, export helpers. Replace the flow-field example with the system the philosophy demands. Keep `randomSeed(seed)` and `noiseSeed(seed)` as the first calls after `createCanvas`.
5. **Assemble the viewer.** Copy `templates/viewer.html` to the output folder (for example `artifacts/<name>.html`) and paste the sketch into the marked `SKETCH` block. Do not restyle the chrome: Ink background, Paper text, one Lime accent. The chrome reads `PARAMS` and generates the sliders, so no manual control markup is needed.
6. **Render and inspect.** Open the file with the browser tool (`navigate` to `file:///...`, wait 1500 ms, `screenshot`). Check that the canvas is not empty, that colors hold together, that nothing is clipped. If p5 failed to load from the CDN, the viewer shows a fallback message; report it rather than hide it.
7. **Explore seeds.** Try at least three seeds (the Previous / Next buttons step the seed; the input jumps to one). Adjust ranges if a slider end produces noise or a blank frame. Tune until the default seed is strong on its own.
8. **Show the result.** Call present_artifact with `type=file` on the HTML (or `type=html` with the content) so the piece appears beside the chat; attach the screenshot as a PNG with present_artifact or open_preview when a quick look is enough. Hand over the philosophy file next to it.

## Technical rules

**Seeded randomness.** Same seed, same image, always. Use the p5 `random()` and `noise()` after seeding, or the `mulberry32` PRNG in the template when values must be stable across p5 versions. Never call `Math.random()` inside the algorithm.

**Parameter object.** One object, flat, numeric values with ranges:

```javascript
const PARAMS = {
  seed:       { value: 2024, min: 1, max: 99999, step: 1 },
  count:      { value: 1800, min: 100, max: 6000, step: 50 },
  noiseScale: { value: 0.0025, min: 0.0005, max: 0.01, step: 0.0005 },
  stepLength: { value: 2.2, min: 0.5, max: 6, step: 0.1 },
  steps:      { value: 260, min: 40, max: 800, step: 10 },
  lineAlpha:  { value: 28, min: 4, max: 120, step: 1 },
};
```

The viewer turns each entry into a labelled slider and calls `regenerate()` when one moves. Design parameters from the system's needs, not from a menu of pattern types.

**Canvas.** Square 1200x1200 by default, `pixelDensity(1)` for predictable export size. Static pieces call `noLoop()` after the last pass; animated pieces keep `draw()` running and may stop after N frames. Keep a frame under 50 ms so the sliders stay responsive, or compute in batches across frames.

**Palette.** Start from the Navin palette and bend it to the philosophy: Ink `#17171a` or Graphite `#121214` grounds, Paper `#f4f3ee` or Cream `#faf9f6` marks, Lime `#c6e88a` as the single highlight, Teal `#0e7490`, Sky `#0ea5e9`, Violet `#7c3aed`, Coral `#ff5f57`, Amber `#febc2e` as supporting tones used sparingly. Map color to a property of the system (velocity, age, density), never to raw random RGB. Two or three hues plus a ground is usually enough.

**Export.** `saveCanvas(name, 'png')` is wired to the Download PNG button. For SVG, render once with the p5.js-svg renderer or rebuild the same geometry as `<path>` strings with the helper in the template (`exportSVG(paths)`), which works for line-based systems. Name files `<movement>-seed<seed>.png`.

## Craft

- Balance density: enough marks to read as a field, not so many that it turns to mud. Alpha accumulation (low `lineAlpha`, many passes) gives depth cheaply.
- Keep a hierarchy even in chaos: a calm region, a dense region, a direction.
- Prefer organic constraints: particles born at edges and dying at boundaries, forces that decay, octaves of noise layered at different scales.
- Reproducibility is a feature; test that two loads of the same seed are pixel-identical.
- Performance: precompute the field on a grid, reuse vectors, avoid `sqrt` in inner loops when a squared distance suffices.

## Variations

When the user wants a series, do not create several files. Add preset buttons (seed 7, 42, 1337) to the viewer, or a gallery mode that draws six small canvases from consecutive seeds. For a print set, export the chosen seeds as PNG with `exec` running the viewer under playwright: `python3 -c` with `page.goto(file_url)`, `page.evaluate('setSeed(42); regenerate()')`, then `page.screenshot`.

## Files

- `templates/generator_template.js` - structure to copy: `mulberry32` PRNG, `PARAMS` with ranges, flow-field particles as the worked example, `exportPNG` and `exportSVG` helpers.
- `templates/viewer.html` - the chrome: p5 from one CDN with a visible fallback, Ink background, Lime controls, seed input with previous / next / random, slider generation from `PARAMS`, Regenerate, Reset and Download PNG. Replace only the `SKETCH` block.
