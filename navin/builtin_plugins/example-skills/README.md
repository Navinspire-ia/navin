# Example Skills

A pack of twelve skills that show what a skill can be: creative tooling, writing workflows, builder toolkits and the skill creation loop itself. They are original Navin skills built on the Navin palette and tool set.

## What is inside

| Skill | What it does |
|-------|--------------|
| `algorithmic-art` | Generative art with p5.js: a seeded generator template, a self-contained viewer with parameter sliders, PNG export |
| `brand-guidelines` | The Navin brand for documents, slides, images and interfaces: palette roles, type scale, logo rules, CSS and JSON tokens |
| `canvas-design` | Posters, covers and single-image canvases: visual philosophy first, then a Pillow or browser render; curated open fonts |
| `theme-factory` | Ten ready-made themes (colors + font pairing) with a generated showcase page, for slides, docs and HTML |
| `slack-gif-creator` | Animated GIFs sized for Slack emoji and messages: frame composer, easing, palette reduction, validators |
| `doc-coauthoring` | Three-stage co-authoring: context questions, outline and section drafts reviewed one by one, reader simulation |
| `internal-comms` | Status updates, newsletters, FAQ answers and incident notes with an audience and tone matrix |
| `mcp-builder` | Build an MCP server in Python or TypeScript, test it with a client, write an evaluation set, wire it into Navin |
| `webapp-testing` | Test a local web app: reconnaissance, Playwright flows, console and network capture, server lifecycle helper |
| `web-artifacts-builder` | Scaffold a React + MUI + framer-motion artifact and bundle it into one self-contained HTML file |
| `frontend-design` | A deliberate visual direction for a new interface inside the locked design system |
| `skill-creator` | Create, package and validate skills, then improve them with an evaluation loop and benchmark reports |

## How it works

Each skill is a folder with a `SKILL.md` the agent loads on demand, plus scripts, templates and references next to it. The agent reads the skill when the request matches its trigger phrases, follows its workflow, and uses the bundled scripts through `exec`.

Colors default to the Navin palette (Ink `#17171a`, Paper `#f4f3ee`, Lime `#c6e88a`, Teal `#0e7490`) and fonts to Inter / IBM Plex Sans, IBM Plex Mono and Lora.

## Examples

```
"Generate a flow-field artwork with a seed I can tweak"
"Make me a poster for the launch, museum style, one accent color"
"Give me a dark theme for this deck"
"A bouncing 'ship it' GIF for Slack, emoji size"
"Help me write the Q4 platform proposal, section by section"
"Write the incident update for the outage this morning"
"Build an MCP server for our ticketing API and test it"
"Check the signup flow of the local app and capture console errors"
"Turn this dashboard into a single HTML artifact"
"Create a skill for our release checklist and evaluate it"
```
