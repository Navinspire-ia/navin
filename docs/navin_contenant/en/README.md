# Documents module — Overview

The **Documents** module (sidebar → **Documents**, route `#/content`) is a document studio: pick a template, add an optional brief, and the agent designs and generates a real, ready-to-share file — PowerPoint, Word, PDF, or Excel — in your workspace.

It aims to go beyond slide tools like Gamma: the output is an actual editable office file with researched content, a designed structure, and a consistent visual theme — produced by an agent that can also browse the web, read your project files, and iterate on feedback.

## How it works

1. Open **Documents** in the sidebar.
2. (Optional) Type a **brief** at the top: product, audience, goal, tone. It is attached to every action you click.
3. Filter by theme if you like (Project, Investment, Marketing, Sales, HR & Training).
4. Click a template card. The chat panel opens and the `/studio` command is sent automatically with the template's specification and your brief.
5. The agent clarifies or infers the missing details, designs the structure (sections, narrative arc, one idea per slide/page), generates the file with real content — never lorem ipsum — applies a visual theme, and reports the file path plus an outline.

You can also use the command directly in any chat:

```
/studio a 12-slide investor deck for a B2B solar startup, sober blue theme
```

## The `/studio` command

| | |
| --- | --- |
| Command | `/studio [format + brief]` |
| Lifecycle | Agent workflow (runs a full agent turn) |
| Skills preloaded | `pptx-generator`, `docx-generator`, `pdf-generator`, `spreadsheet-analyst`, `presentation-designer`, `professional-writer`, `document-templates` |
| Output | A real file saved in the workspace (`.pptx`, `.docx`, `.pdf`, `.xlsx`) |

The agent uses `python-pptx`, `python-docx`, `openpyxl`, and PDF tooling to build the files programmatically, which means everything remains editable afterwards.

## Formats

| Badge | Format | Typical use |
| --- | --- | --- |
| PPTX | PowerPoint | Pitch decks, plans, training material, brand decks |
| DOCX | Word | Charters, business plans, proposals, handbooks |
| PDF | PDF | One-pagers, reports, battlecards |
| XLSX | Excel | Roadmaps, budgets, financial models, pipelines, calendars |

## Visual themes & custom templates

A **Theme** picker sits under the brief field with 8 built-in visual themes: Executive, Minimal, Tech, Bold, Warm, Nature, Elegant, Corporate. Each has an exact spec (palette, fonts, layout rules) defined in the `document-templates` skill; pick one and every generated document applies it consistently. **Auto** lets the agent choose the theme that fits the audience.

You can also use **your own template**: drop a `.pptx` / `.docx` / `.xlsx` file into the workspace (conventionally under `templates/`) and say "use templates/pitch.pptx as the base". The agent opens the file, reuses its masters and styles, fills the content, and saves the result as a new file — your template is never overwritten. When you give an explicit URL to a free template, the agent can download it into `templates/` and adapt it (it notes the license).

## Template categories

25 templates across 5 categories — see [Templates](./templates.md) for every card:

- **Project** — pitch deck, charter, roadmap, status report, budget
- **Investment** — investor deck, business plan, financial model, one-pager, due diligence pack
- **Marketing** — marketing plan, campaign brief, editorial calendar, market research, brand deck
- **Sales** — proposal, sales deck, pipeline & forecast, battlecard, pricing grid
- **HR & Training** — onboarding handbook, training deck, review grid, job description, HR report

## Tips

- The richer the brief, the better the first version. Include audience, goal, and tone.
- Iterate in the same chat: "make slide 4 more visual", "add a competitors section", "switch the theme to dark green".
- Combine with web research: "research the actual market numbers before writing".
- Ask for a matching set: "now produce the one-pager PDF version of this deck".
