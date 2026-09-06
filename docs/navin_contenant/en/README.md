# Documents module - Overview

The **Documents** module (sidebar → **Documents**, route `#/content`) is a document studio: pick a template, add an optional brief, and the agent designs and generates a real, ready-to-share file - PowerPoint, Word, PDF, or Excel - in your workspace.

It aims to go beyond slide tools like Gamma: the output is an actual editable office file with researched content, a designed structure, and a consistent visual theme - produced by an agent that can also browse the web, read your project files, and iterate on feedback.

## How it works

1. Open **Documents** in the sidebar.
2. (Optional) Type a **brief** at the top: product, audience, goal, tone. It is attached to every action you click.
3. Filter by group if you like (Project, Investment, Marketing, Sales, HR & Training, Legal).
4. Click a template card. A **preparation dialog** opens: describe what you need, select the **output language** (mandatory, never inferred from the interface language) and pick a **visual template** from the matching category (PPT, Word, PDF, Excel) or keep "Auto".
5. Confirm. The chat panel opens and the `/studio` command is sent with the template's specification, your details, your brief and the attached visual template.
6. The agent clarifies or infers the missing details, designs the structure (sections, narrative arc, one idea per slide/page), generates the file with real content - never lorem ipsum - applies a visual theme, and reports the file path plus an outline.

You can also use the command directly in any chat:

```
/studio a 12-slide investor deck for a B2B solar startup, sober blue theme
```

## The `/studio` command

| | |
| --- | --- |
| Command | `/studio [format + brief]` |
| Lifecycle | Agent workflow (runs a full agent turn) |
| Skills preloaded | `pptx-generator`, `docx-generator`, `pdf-generator`, `spreadsheet-analyst`, `presentation-designer`, `professional-writer`, `document-templates`, `archify` |
| Output | A real file saved in the workspace (`.pptx`, `.docx`, `.pdf`, `.xlsx`, `.csv`) |

The agent uses `python-pptx`, `python-docx`, `openpyxl`, and PDF tooling to build the files programmatically, which means everything remains editable afterwards.

## Formats

| Badge | Format | Typical use |
| --- | --- | --- |
| PPTX | PowerPoint | Pitch decks, plans, training material, brand decks |
| DOCX | Word | Charters, business plans, proposals, handbooks |
| PDF | PDF | One-pagers, reports, battlecards |
| XLSX | Excel | Roadmaps, budgets, financial models, pipelines, calendars |
| CSV | Text data | UTF-8 tabular exports with the local delimiter announced |

The selected language is preserved end to end in content and metadata, fonts, dates, numbers and currencies. Arabic and other RTL languages use appropriate direction and layout with fonts that cover the required glyphs.

## Visual themes & custom templates

A **Theme** picker sits under the brief field with 8 built-in visual themes: Executive, Minimal, Tech, Bold, Warm, Nature, Elegant, Corporate. Each has an exact spec (palette, fonts, layout rules) defined in the `document-templates` skill; pick one and every generated document applies it consistently. **Auto** lets the agent choose the theme that fits the audience.

You can also use **your own template**: drop a `.pptx` / `.docx` / `.xlsx` file into the workspace (conventionally under `templates/`) and say "use templates/pitch.pptx as the base". The agent opens the file, reuses its masters and styles, fills the content, and saves the result as a new file - your template is never overwritten. When you give an explicit URL to a free template, the agent can download it into `templates/` and adapt it (it notes the license).

## Template categories

50 templates across 6 groups - see [Templates](./templates.md) for every card:

- **Project** - pitch deck, charter, roadmap, status report, budget
- **Investment** - investor deck, business plan, financial model, one-pager, due diligence pack
- **Marketing** - marketing plan, campaign brief, editorial calendar, market research, brand deck
- **Sales** - proposal, sales deck, pipeline & forecast, battlecard, pricing grid
- **HR & Training** - onboarding handbook, training deck, review grid, job description, HR report
- **Legal** - 25 contracts, from NDAs and partnerships through SaaS, DPA/GDPR, employment, shareholders and settlement agreements

Contracts use configurable governing law and dispute resolution. The agent retains the HTML source with the DOCX/PDF export, checks contract-wide consistency and always adds a warning for review by qualified local counsel before signature.

## Tips

- The richer the brief, the better the first version. Include audience, goal, and tone.
- Iterate in the same chat: "make slide 4 more visual", "add a competitors section", "switch the theme to dark green".
- Combine with web research: "research the actual market numbers before writing".
- Ask for a matching set: "now produce the one-pager PDF version of this deck".
