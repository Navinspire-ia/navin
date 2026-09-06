# Montage studio - Overview

**Point fort :** develop your **web and mobile** app in Navin (Code / Mobile), then let Montage create **project-linked** videos and images ready for social networks - real UI footage first, AI creatives when you need B-roll, stills, music, or voiceover.

**Montage** turns a linked project into a propose-only marketing video desk: live browser demo → social exports → optional AI creatives and HyperFrames compositions. Nothing is published automatically.

| | |
| --- | --- |
| Route | `#/montage` (Studio → Montage) |
| Composer mode | **Montage** (prefixes free text with `/montage`) |
| Command | `/montage [brief]` |
| Workspace output | `marketing/montage/` (+ `montage-report-*.html`) |
| Model routing | role `docs` |

## When to use Montage

- You have a **runnable product UI** (local preview or staging URL) and want real footage, not stock-only ads.
- You need **platform crops** (YouTube, Shorts, Reels, Feed, TikTok, LinkedIn) from one master demo.
- You want a **14-day calendar** and kit before spending on AI video batches.
- You need HTML/GSAP motion (HyperFrames) or optional React scenes (Remotion) without bundling those stacks into the Navin install.

For paid ads accounts (Google / Meta / TikTok / Reddit), use the **Ads** studio (`#/ads`) after you approve spend. Marketing copy/campaigns without live demo packaging stay under `#/marketing` (`/campaign`).

## Studio tabs

| Tab | Purpose |
| --- | --- |
| Templates | Cutroom media library: frames and cuts (grade, camera, sets, finishes) plus commerce universes (startups & apps, AI agents, fashion, beauty). Pick up to 6 references per turn; files download from AWS for the agent |
| System | Toolchain doctor + Install for missing packages (FFmpeg, HyperFrames, Remotion) |
| AI model | Pick managed Image / Video / Music / STT / TTS models when Plus or BYOK is ready |
| Actions | One-click briefs (translate & dub, translated subtitles, doctor, live demo, package, pipeline, music, HyperFrames) |
| Gallery | Assets under `marketing/montage/` |
| Profiles | Built-in export sizes |

## Video translation & dubbing

Montage localizes any clip end to end with local tools only: ffmpeg extracts and splits the audio on silences, the configured STT writes a timed `source.srt`, the agent translates the cues (timing untouched), TTS generates the new voice, and `montage(action=dub)` re-injects it - optionally keeping the original audio as a quiet bed and burning the translated subtitles.

- **Translate & dub a video** - an English clip comes back speaking French (or any target language), audio re-injected.
- **Translated subtitles** - keep the original voice, burn clean translated subtitles; the `.srt` is also delivered for platform closed captions.

Outputs land under `marketing/montage/localization/<clip>/`. If STT or TTS is not configured, the agent says exactly which one and stops - it never fakes a transcript.

Link a project with the project selector before recording. Montage targets the **linked workspace only** - it must not invent another product.

## Access and spend

1. **Navin Plus or higher (recommended)** - managed key for Image / Video / Music (Lyria) / STT / TTS.
2. **BYOK** - add provider keys under Settings → Providers, then set each media section.
3. **FFmpeg-only** - once a demo file exists, `package` can crop/export without AI spend.
4. Free plan alone cannot run managed media generations.

Explain Plus vs BYOK before expensive `generate_video` / `generate_music` batches. Default music = **Lyria Clip 30s**; Lyria Pro only when the user asks for a full track.

## Happy path (live demo → exports)

1. Open Studio → Montage, link the project.
2. System tab: Install FFmpeg if missing (see [Packages](./packages.md)).
3. Actions → **Product demo (browser)** → fill URL + happy-path steps (or record yourself in the Agent browser).
4. Agent: `record_start` → drive steps → `record_stop` → `demo_register` → files under `marketing/montage/demos/`.
5. **Package social exports** → `marketing/montage/exports/` (default profiles; `profiles=all` adds 4K + cinematic).
6. Optional: calendar, AI B-roll, HyperFrames render, report HTML.

## Delivery layout

```text
marketing/montage/
  demos/           # registered recordings
  exports/         # platform MP4s + briefs
  captures/        # UI stills
  compositions/    # HyperFrames HTML
  project-kit.md
  calendar-*.md
montage-report-*.html
```

## Related docs

- [Actions](./actions.md) · [Packages & toolchain](./packages.md) · [Media models](./media-models.md) · [Skills & tools](./skills.md)
- Composer modes (incl. Montage): [Modes](../../navin_dev/en/modes.md)
- Mobile run/preview: [Mobile](../../navin_dev/en/mobile.md)
- Marketing campaigns: [navin_marketing](../../navin_marketing/en/README.md)
- Ads MCP: [navin_ads](../../navin_ads/en/README.md)
