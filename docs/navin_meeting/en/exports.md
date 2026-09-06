# Meeting templates and exports

Templates decide what a summary contains. Exports decide how it leaves the desk (`#/meeting`).

## Built-in templates

Pick a template next to the meeting title, before running a summary action. Every summary action reads the selected template instructions.

| Template | Written for | Emphasis |
| --- | --- | --- |
| Standard minutes | Recurring project meetings | Decisions, owners, deadlines, open points |
| Executive brief | Stakeholders who were not there | Ten lines, outcome first, no transcript quotes |
| Sales discovery | Prospect calls | Pain, budget, timeline, decision path, objections |
| Stand-up / sync | Daily team syncs | Done, next, blockers, per person |
| Interview notes | Hiring and user interviews | Signals, verbatim quotes, evidence, no judgement |

## Custom templates

The `Templates` tab has a two-field editor: a name and the instructions themselves. Write the instructions as you would brief a careful assistant: the sections you want, in which order, the tone, the output language, and the fields that must never be missing.

```text
Name: Comite de pilotage
Instructions: Report in French. Sections: 1) Decisions with owner and date,
2) Budget impact, 3) Risks with severity, 4) Next steps. Quote the transcript
for every decision. Say "not discussed" instead of guessing.
```

Custom templates are stored locally in the browser profile, appear in the picker next to the built-in ones, and are marked `Custom`. Saving one selects it for the active meeting.

## Where the export buttons live

The export row (Markdown, HTML, PDF, DOCX) sits at the bottom of the `Transcript` tab and the `Notes` tab, under the model that produced the report. The local audit trail lives under `Notes`.

## Export formats

| Format | How it is produced | Best for |
| --- | --- | --- |
| Markdown | Built by the desk from the report, no model call | Archiving, wikis, git |
| HTML | Built by the desk, self-contained and styled for print | Sending one file that opens anywhere |
| PDF | The same HTML sent to the browser print dialog | Clean, final, read-only sharing |
| DOCX | The agent writes it from the meeting content | Documents to send and countersign |
| Audit trail | Downloaded immediately as Markdown | Compliance and internal reviews |
| Listen | TTS read-aloud, no file | Reviewing a summary while doing something else |

All three local formats render the same document as the `Report` tab, in this order: title, metadata table, summary, the decisions and actions table, open questions, notes, then the transcript split into readable turns. Empty sections are dropped rather than left as placeholders, and detected items quote the transcript verbatim.

If the print dialog is unavailable in your build, download the HTML and print it from your browser: the file carries its own print stylesheet, with page breaks kept out of tables and speaker turns.

## The HTML report

Most actions also refresh `meetings/<slug>/meeting-report-*.html`. That report is the artefact to share when you want one link rather than five files: it embeds the summary, the decisions, the action table, and, with the **Visual recap** action, a recap image plus a diagram of the decision flow.

## Related docs

- [Overview](./README.md)
- [Actions](./actions.md)
- [Quickstart](./quickstart.md)
