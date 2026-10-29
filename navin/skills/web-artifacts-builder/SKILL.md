---
name: web-artifacts-builder
description: Builds multi-component web artifacts (React + TypeScript + Material UI + framer-motion, optional three.js) bundled into one self-contained HTML file shown beside the chat - scaffold, live preview, vite-plugin-singlefile bundle. Use when the user asks to "build an interactive dashboard artifact", "make a React app I can open as one HTML file", "create a multi-screen prototype" or "turn this data into an interactive explorer". In French "crée une page web autonome en un seul fichier", "fais un tableau de bord interactif" or "construis un prototype React".
metadata: {"navin":{"emoji":"🧱","category":"development"}}
---

# Web Artifacts Builder

An artifact is a single HTML file that Navin shows next to the conversation
with `present_artifact`. Small artifacts (a chart, a form, a one-screen
visual) are best written by hand as one HTML file with inline CSS and JS.
This skill is for the larger case: several components, shared state, routing
between views, a design system, animation or 3D. It scaffolds a real Vite +
React + TypeScript project, lets the agent develop it with a live preview,
then bundles everything (JS, CSS, fonts, icons) into ONE HTML file with no
external requests.

Stack, fixed: Vite, React 18, TypeScript, Google Material UI
(`@mui/material`, `@emotion/react`, `@emotion/styled`, `@mui/icons-material`),
`framer-motion` for motion, and optionally `three` + `@react-three/fiber` +
`@react-three/drei`. Never add Tailwind, shadcn or any other UI kit: the
bundle must stay predictable and the theme comes from MUI.

## When to use it

- Use this skill when the artifact needs 3 or more interacting components, a
  data table with filtering and dialogs, several views, charts plus controls,
  or 3D.
- Do not use it for a static report, a single chart or a form: write a plain
  HTML artifact instead, it is faster and lighter.
- Do not use it to build the user's production frontend. That is ordinary
  project work (see `frontend-design` for the visual direction and the
  project's own tooling for the code).

## Workflow

### 1. Scaffold

Run `bash scripts/init-artifact.sh <name> [--three] [--dry-run]` in a
scratch folder (for example `.navin/artifacts/`). The script writes the whole
template itself, no network needed until `npm install`:

- `package.json` with the pinned stack and `vite-plugin-singlefile`,
- `vite.config.ts` configured for a single inlined output,
- `tsconfig.json`, `index.html`, `src/main.tsx`,
- `src/theme.ts`: an MUI theme built from the Navin palette,
- `src/App.tsx`: a working sample with an AppBar, a card list fed by a local
  dataset, a filter `TextField` that really filters, and a detail `Dialog`.

`--three` adds the three.js dependencies and a `src/Scene.tsx` with a
rotating mesh in a `Canvas`. `--dry-run` writes the files and prints the plan
but skips `npm install`, which is useful to inspect the template or in tests.
Without `--dry-run` the script runs `npm install` at the end; Node 18 or
newer is required.

### 2. Develop with a live preview

Replace the sample data and components with the user's content. Start the
dev server with `open_preview` (or `start_app`) pointed at the project
folder: it picks a free port and shows the running app in the Preview tab.
Never bind to 5173-5176, 8765-8768 or 18790-18793; those ports belong to the
Navin runtime. When a port must be set by hand, pass `--port` to `vite` with
another value such as 4300.

While iterating:

- Keep everything in `src/`; one component per file once a component passes
  about 80 lines.
- Use the theme (`useTheme`, `sx`) for colours and spacing; no hard-coded hex
  outside `src/theme.ts`.
- Use `framer-motion` for entrance and state transitions only; keep durations
  under 300 ms and respect `prefers-reduced-motion`.
- Check the browser console with the `browser` tool (`navigate`, `console`)
  before bundling: an artifact that logs React warnings is not finished.

### 3. Bundle into one file

Run `bash scripts/bundle-artifact.sh <dir> [--skip-build]`. It runs
`npm run build` inside the project (unless `--skip-build`, for an existing
`dist/`), then verifies the result:

- `dist/index.html` exists and `dist/` contains nothing else (a favicon is
  tolerated),
- the HTML has no `<script src=` and no `<link rel="stylesheet" href=`
  pointing at a separate file: every asset must be inlined,
- the size is printed in KB.

On any failure it exits 1 with the exact reason. Typical causes: a dynamic
`import()` that Vite split into a chunk (set `build.rollupOptions.output.inlineDynamicImports`,
already done in the template, or remove the lazy import), an image referenced
by URL instead of imported, or a font loaded from a CDN. Fix the cause,
never hand-edit `dist/index.html`.

Material icons are tree-shaken per import (`import SaveIcon from
'@mui/icons-material/Save'`); never import the whole icon barrel or the
bundle grows by megabytes.

### 4. Present

Show `dist/index.html` with `present_artifact`. Tell the user what the
artifact does, what data it contains and what is interactive. Keep the
project folder so the next change is an edit plus a re-bundle, not a
rewrite.

## Design rules

The theme in `src/theme.ts` encodes the Navin palette; build on it rather
than around it:

- Ink `#17171a` for text and dark surfaces, Paper `#f4f3ee` for the light
  background, Lime `#c6e88a` as the accent for primary actions and active
  states, Teal `#0e7490` for secondary actions and links, Violet `#7c3aed`
  for highlights and data series that must stand apart.
- Typography: a readable sans for the UI, a mono face for numbers and code.
  No Inter by default; pick a face that fits the subject and declare it in
  the theme (`frontend-design` explains how to choose).
- Layout: left-aligned content with a clear hierarchy; avoid full-width
  centred stacks, uniform rounded corners on everything and gradient
  backgrounds. Shape radius 6 px, dense spacing (8 px grid).
- Real data or an honest empty state. If the user's data is not available,
  load a small realistic sample and say so in the UI ("Sample data"); never
  fake metrics that look authoritative.
- Every visible control is wired. A filter filters, a button opens the
  dialog or performs its action, a tab switches content. Remove anything
  decorative that does nothing.
- Dark mode only if the user asks; the default is the light Paper surface
  with Ink text.

## Checklist before presenting

- `bundle-artifact.sh` exited 0 and printed the size (under 2 MB is the
  comfort zone; above 5 MB, cut icons, fonts or data).
- The artifact opened from `file://` in the `browser` tool with no console
  errors.
- No external network request at runtime (check with the `browser` tool's
  `network` action; a bundled artifact should fetch nothing).
- The empty states, the loading states and one error state have been seen.
- Text has been proofread; no placeholder like "Lorem" or "TODO".

## Bundled files

- `scripts/init-artifact.sh`: write the template (and install dependencies).
- `scripts/bundle-artifact.sh`: build and verify the single-file output.
