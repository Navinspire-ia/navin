# Montage actions

Each card seeds or sends `/montage` with a concrete brief. Prefer **Edit in chat** when you need to adjust paths or profiles before running.

## Cards

| Action | What it does | Agent expectations |
| --- | --- | --- |
| Translate & dub a video | `transcribe` → translate `.srt` → TTS → `dub` | Timed STT per silence-split segment, cue timings untouched, new voice re-injected (`original_gain_db` keeps the original bed). Never fake a transcript. |
| Translated subtitles | `transcribe` → translate `.srt` → `assemble` burn | Keep the original voice, burn the translated cues, deliver the `.srt` too. |
| Check toolchain | `montage(action=doctor)` + `detect` | Report ready vs missing with exact fixes. No invented readiness. |
| Product demo (browser) | Opens a **brief form** (URL + steps + optional login) | Navigate URL → `browser(record_start)` → exact happy path → `record_stop` → `demo_register`. Save under `marketing/montage/demos/`. Do not invent another product. |
| Package social exports | `montage(action=package, path=…, profiles=default)` | Use latest demo. Write brief under `exports/`. Never auto-publish. |
| Full montage pipeline | Doctor → demo → package → analyze + 14-day calendar | Linked project only. Ask once for URL if UI is not runnable. |
| Music bed (Lyria Clip) | `generate_music` | Default 30s Clip. Lyria Pro only on explicit request. Confirm Plus/BYOK. |
| Compose & render | HyperFrames HTML → MP4 | `setup` if missing, author under `compositions/`, `render` with a profile. |

## Live demo brief (required fields)

When you click **Brief agent**, Montage asks for:

| Field | Required | Example |
| --- | --- | --- |
| Product URL | Yes | `https://app.example.com/login` or local preview URL |
| Happy path to film | Yes | Numbered steps (login → create → result screen) |
| Login / access | No | Demo account, magic link, or “ask me at login wall” |

You can also record yourself in the Agent browser without the form; register the file afterward with `demo_register`.

## Slash usage

```text
/montage Record the linked app at https://staging.example.com : login → create project → show dashboard. Save under marketing/montage/demos/, then package profiles=default.
```

```text
/montage Run doctor, then install ffmpeg if missing. Do not install Remotion unless I ask.
```

## Guardrails

- Greeting-only messages (“hi”) are conversation, not a mission - no kit files, no analyze.
- Never claim `record_start` / `montage` are unavailable in Montage mode.
- Never auto-publish to social networks or Ads MCP without an explicit later request.
