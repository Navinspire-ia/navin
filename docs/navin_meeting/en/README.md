# Meeting module - Overview

The **Meeting** module (sidebar → **Meeting**, route `#/meeting`) is a privacy-first meeting desk that aims past typical "PRO meeting note" checklists: capture, high-accuracy STT via **your** Navin providers, **custom summary templates**, speaker labeling, ICS calendar link + local start alerts, chat-with-meeting, Markdown/DOCX/PDF exports, and a **local audit trail**.

## How it works

1. Open **Meeting** in the sidebar.
2. Create a meeting, pick a **template**, then **Record**, **Import audio**, or paste a transcript.
3. Optionally import a calendar `.ics`, link an upcoming event, label speakers.
4. Run actions (Minutes, Speakers, High-accuracy pass, Ask…) - chat opens with `/meeting`.
5. Export Markdown instantly, or seed DOCX/PDF via document/report tools.
6. Review the local audit trail (browser-only).

```
/meeting produce minutes with the selected template
/meeting identify speakers then summarize
/meeting answer: what did we decide about pricing?
```

## Desk layout

The desk is one toolbar, one meeting list, and one tabbed workspace, so capture,
actions, exports, and calendar never fight for the same screen.

- **Toolbar**: meeting title, STT status pill (click it when it is amber to open Settings -> Voice), **Record** with timer, **Import audio**, full-width toggle, **New meeting**.
- **Full width**: the arrows button expands the desk over the whole shell and hides the chat column. Running an action or asking a question brings the chat back automatically, since that is where the answer lands.
- **Meeting list** (left): search, then one row per meeting. The chevron opens a **brief**: excerpt, template, speaker count, and delete.
- **Tabs**: `Transcript` (capture + exports), `Report` (generate and read minutes without the chat), `Notes` (notes + speakers + audit trail), `Actions` (ask box + Run with your models), `Calendar`, `Templates`.

## Capability map

| Capability | Status in Navin |
| --- | --- |
| High-accuracy transcription | Uses your best configured STT provider/model + "High-accuracy pass" cleanup |
| Custom summary templates | Built-in + user-defined templates in the desk |
| Export MD / DOCX / PDF | Instant Markdown; DOCX via `/studio` tools; PDF via `meeting-report-*.html` File Preview |
| Auto-detect meetings | Local ICS import + browser notification ~5 min before start (no silent Zoom/Meet join) |
| Speaker identification | Dedicated action + speaker list editor (no invented names) |
| Chat with meetings | Ask box grounded on the active meeting transcript/notes |
| Calendar integration | ICS import/link (Google/Outlook/Apple export) |
| Self-hosted / privacy | Local-first desk + your providers; audit trail stays in the browser |
| GDPR-oriented audit | Local append-only action log exportable as Markdown |
| Visual recap | Action generating a recap image + decision diagram inside the HTML report |

## Audio pipeline

The desk reads the live transcription settings (Settings -> Voice) and shows the
active provider, model, and segment limit above the transcript. Record and
Import stay disabled while transcription is off or the provider has no
credentials.

- **Live recording** streams chunks (25 s, or less when your duration cap is lower) so the transcript grows while the meeting runs.
- **Import** decodes the file locally, downmixes it to 16 kHz mono, and re-cuts it into segments under the configured duration cap, so a one hour recording no longer hits the `duration` / `size` limits. Progress is shown as `Transcribing 4/24`.
- Providers that reject the browser `webm/opus` container (for example Xiaomi MiMo) receive WAV, converted in the browser.
- **Listen** reads the summary (or notes / transcript) aloud with your TTS provider. It opens a speak-only realtime voice session without touching the microphone, so it needs a Pro, Ultra, or Team plan plus a configured TTS provider; the button is hidden otherwise.

## Joining meetings

Imported ICS events expose their conference link (`URL`, `X-GOOGLE-CONFERENCE`,
or the first Zoom / Meet / Teams / Webex style link found in the location or
description).

- Every upcoming event gets a **Join** link.
- Clicking the start notification opens the conference link.
- The **Open the conference link automatically at start time** toggle joins without a click. It stays off by default because browsers block programmatic tab opening when the app is not focused.

## The `/meeting` command

| | |
| --- | --- |
| Command | `/meeting [transcript\|notes\|brief]` |
| Skills | meeting-studio, meeting-followup, discovery-call-assistant, expert contract, critic, crm-update-agent |
| Output | `meetings/<slug>/*` + `meeting-report-*.html` (+ DOCX when requested) |

## Related docs

- [Quickstart](./quickstart.md)
- [Transcription](./transcription.md)
- [Templates and exports](./exports.md)
- [Calendar](./calendar.md)
- [Privacy and audit trail](./privacy.md)
- [Troubleshooting](./troubleshooting.md)
- [Actions](./actions.md)
- [Skills](./skills.md)
- Voice / STT: Settings -> Voice
