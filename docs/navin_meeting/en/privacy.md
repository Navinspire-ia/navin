# Meeting privacy and audit trail

What leaves your machine when you use the Meeting desk (`#/meeting`), what stays, and how to prove it.

## What leaves the machine

| Data | Leaves? | To whom |
| --- | --- | --- |
| Audio chunks and imported files | Yes, when you record or import | Only the STT provider configured in Settings -> Voice |
| Transcript, notes, summary | Yes, when you run an action | Only the chat model you selected for that action |
| Summary text for read-aloud | Yes, when you press Listen | Only the configured TTS provider |
| Meeting list, titles, speakers, custom templates | No | Stored under the local Navin data directory |
| Calendar events and conference links | No | Parsed locally from the file you imported |
| Audit trail | No | Local until you export it yourself |

There is no hosted Navin meeting archive and no automatic upload. Optional Google or Microsoft calendar connections transmit calendar data only when configured and explicitly used. If you never record, import, run an action, or sync a calendar, nothing about the meeting is transmitted.

## Where local data is stored

The gateway stores records, templates, calendar data, audit JSONL, audio segments and bot status under the local Navin data directory in `meetings/`. Writes are atomic and locked across processes. A versioned, idempotent migration imports legacy browser `localStorage` data. The emergency ZIP export remains readable without Navin.

Deleting a meeting removes its record and audio segments. Desktop users should back up the Navin data directory, not only the WebView profile.

## Audit trail

Every meaningful action is appended to a local, append-only log: meeting created and deleted, recording started and stopped, audio imported and transcribed, transcript appended with its size, template selected or created, action sent to the agent, question asked, export produced, calendar imported, event linked or joined, auto-join toggled, summary read aloud.

Each entry carries a timestamp, the meeting identifier, the action, and a short detail. The `Notes` tab shows the log for the active meeting and downloads it as Markdown, which is the artefact to attach to a compliance review.

The trail is designed to answer the question an auditor actually asks: what was captured, when, with which processor, and who asked for the export. It is not a security boundary, since it lives in the same profile as the data it describes.

## Practices for regulated environments

1. Choose the STT and chat providers deliberately. In the Meeting desk, the processor is whoever you configured, so your data processing record should name them.
2. Record only what you need. A meeting with notes and no audio still produces minutes, since actions accept notes alone.
3. Announce recording to participants. The meeting bot appears as a participant but does not provide legal consent or an audible announcement.
4. Export and archive at the end, then delete the meeting from the desk. The browser profile is a workspace, not an archive.
5. Keep the audit trail with the export when the meeting has a legal or contractual weight.

## Related docs

- [Overview](./README.md)
- [Calendar](./calendar.md)
- [Templates and exports](./exports.md)
