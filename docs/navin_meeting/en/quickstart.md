# Meeting quickstart

From an empty desk to a shareable set of minutes, in five minutes. Route `#/meeting`, command `/meeting`.

## Before you start

| Requirement | Where | Needed for |
| --- | --- | --- |
| Transcription enabled with a configured provider | Settings -> Voice | Record and Import audio |
| A chat model | Settings -> Models | Minutes, summaries, action lists |
| TTS provider and a Pro, Ultra, or Team plan | Settings -> Voice | The optional Listen button |

The status pill in the toolbar tells you where you stand. Green means transcription is ready and shows the active provider. Amber means it is not, and clicking it opens Settings -> Voice. While it is amber, Record and Import stay disabled on purpose rather than failing halfway through a meeting.

## Five steps

1. **Create the meeting.** Click the plus button in the toolbar. Rename it in the title field, since the title becomes the export folder name (`meetings/<slug>/`).
2. **Pick a template** next to the title: Standard minutes, Executive brief, Sales discovery, Stand-up / sync, or Interview notes. The template drives every summary action, so choose it before running them.
3. **Capture.** Press **Record** to stream the microphone, or **Import audio** for a file you already have. The transcript fills the `Transcript` tab while the meeting runs.
4. **Generate the report.** Open the `Report` tab and click **Generate report**. One model call turns the transcript into template-driven minutes, rendered right there next to the metadata, the detected decisions and actions, and the formatted transcript. Nothing goes through the chat.
5. **Export.** At the bottom of `Transcript` (or `Notes`), Markdown and HTML download instantly and **PDF** opens the print dialog. DOCX is the one format still written by the agent.

The `Actions` tab remains for the jobs that genuinely need the agent: DOCX packs, follow-up emails saved to disk, high-accuracy cleanup passes, visual recaps.

## Full width and the chat

The double arrow button in the toolbar expands the desk over the whole window and hides the chat column. Use it while you take notes during a call. As soon as you run an action or ask a question, the chat comes back on its own, because that is where the agent answers.

## Where the files land

```text
meetings/<slug>/
  transcript.md
  minutes.md
  actions.md
  follow-up.md
  chat.md
  meeting-report-<timestamp>.html
```

## Next steps

- Label who spoke in the `Notes` tab, then run **Identify speakers** for a labeled transcript.
- Import your calendar in the `Calendar` tab so upcoming meetings show a join link.
- Write your own template in the `Templates` tab when the built-in five do not match your report format.

## Related docs

- [Overview](./README.md)
- [Transcription](./transcription.md)
- [Templates and exports](./exports.md)
- [Troubleshooting](./troubleshooting.md)
