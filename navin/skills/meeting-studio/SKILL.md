---
name: meeting-studio
description: Run the Navin Meeting studio desk - capture notes/transcripts, custom summary templates, speaker labels, calendar-linked meetings, chat-with-meeting Q&A, exports, and local audit-aware reports. Use when the user opens #/meeting or runs /meeting.
metadata: {"navin":{"emoji":"🎙️","category":"meeting"}}
---

# Meeting studio

Privacy-first meeting desk inside Navin Studio (`#/meeting`, command `/meeting`).
Transcription uses the user's configured Navin STT provider (prefer the highest-accuracy
managed/local model available). Summaries use their chat models and optional **custom
templates** from the Meeting workbench. Nothing is sent to a third-party meeting brand.

## Inputs

- Live or pasted transcript from the Meeting workbench
- Speaker map (Speaker 1… or real names only when explicit in transcript)
- Selected summary template instructions
- Optional calendar event metadata (ICS import is local-only)
- Chat-with-meeting questions (answer only from this meeting's evidence)
- Raw notes / CRM context

## Outputs (workspace)

```text
meetings/<slug>/
  transcript.md
  speakers.md
  summary.md
  minutes.md
  follow-up.md
  actions.md
  discovery.md
  chat.md
  audit.md
  export.docx   # when /studio document export is requested
meeting-report-YYYYMMDD-HHMMSS.html
```

## Workflow

1. Confirm language, template instructions, attendees/speakers if known.
2. High-accuracy cleanup of ASR when asked - never invent words or speakers.
3. If speaker identification is requested: label turns; use Speaker N when names are unknown.
4. Extract: decisions ≠ discussion ≠ actions (owner + deadline required).
5. Apply the selected template for summary/minutes.
6. For chat-with-meeting: answer only from transcript/notes; append Q&A to `chat.md`.
7. Draft follow-up email (human sends) and log CRM activity with the `crm` tool (`log_activity`) plus `crm-update-agent`.
8. Ship `meeting-report-*.html` (PDF via File Preview export), open preview, critic-review gate.
9. When DOCX is requested, use document studio tools / `/studio` patterns under `meetings/<slug>/`.

## Rules

- Never invent quotes, attendees, speaker identities, or commitments.
- Ambiguous actions become clarifying questions in the follow-up draft.
- Prefer facts from the transcript; mark hypotheses explicitly.
- Keep external email shorter and warmer than internal minutes.
- Respect privacy: do not upload meeting content outside configured Navin providers.
- Reuse `meeting-followup` and `discovery-call-assistant` when relevant.
