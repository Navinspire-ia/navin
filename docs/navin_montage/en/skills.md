# Montage skills and tools

## Skills

| Skill | Role |
| --- | --- |
| `montage-studio` | Primary playbook: doctor → demo → package → kit/calendar → creatives → HyperFrames |
| `playwright-browser` | Live Agent browser, including `record_start` / `record_stop` |
| `studio-html-report` | Closing HTML report pattern (`montage-report-*.html`) |

Manage skills under **Settings → Skills**. In Montage mode these tools are registered - the agent must call them, not claim they are missing.

## `montage` tool actions

| Action | Purpose |
| --- | --- |
| `detect` | Host toolchain snapshot |
| `doctor` | Checks with status + fix strings |
| `setup` | Install `core` \| `ffmpeg` \| `hyperframes` \| `remotion` \| `stock-*` |
| `stock_search` | Pexels / Unsplash / Pixabay query |
| `analyze` | Write `marketing/montage/project-kit.md` |
| `calendar` | Propose calendar (`days` 7 / 14 / 30) |
| `screenshot` | Register UI stills (`path` or `paths`) |
| `demo_register` | Import browser recording into `demos/` |
| `package` | Platform exports (`profiles`, optional `srt`, `title`) |
| `render` | HyperFrames HTML → MP4 (`composition`, `profile` or width/height/fps) |
| `profiles` | List built-in render profiles |

### Setup packages

`package=` for `setup`:

- `core` - detect only (no heavy npm)
- `ffmpeg` - OS manager or user-local binary
- `hyperframes` - lazy npm under `~/.navin/montage`
- `remotion` - optional lazy npm under `~/.navin/montage/remotion`
- `stock-pexels` / `stock-unsplash` / `stock-pixabay` - status / keys only

## Browser recording

```text
browser(action=record_start)
# … drive the product UI …
browser(action=record_stop)
montage(action=demo_register, path=<recording>)
```

Prefer `open_preview` for local apps. Stop before starting a second recording.

## Render profiles

| Profile id | Label | Resolution | Aspect |
| --- | --- | --- | --- |
| `youtube_landscape` | YouTube Landscape | 1920×1080 | 16:9 |
| `youtube_4k` | YouTube 4K | 3840×2160 | 16:9 |
| `youtube_shorts` | YouTube Shorts | 1080×1920 | 9:16 |
| `instagram_reels` | Instagram Reels | 1080×1920 | 9:16 |
| `instagram_feed` | Instagram Feed | 1080×1080 | 1:1 |
| `tiktok` | TikTok | 1080×1920 | 9:16 |
| `linkedin` | LinkedIn | 1920×1080 | 16:9 |
| `cinematic` | Cinematic | 2560×1080 | 21:9 |

- Default `package` = social set (excludes `youtube_4k` and `cinematic`).
- `profiles=all` or a comma-separated list for opt-in sizes.
- `render` with `profile=youtube_shorts` sets width/height from the table.

## Related media tools

When configured: `generate_image`, `generate_video`, `generate_music`. Confirm budget before batches. Hand off paid media mutations to `/ads` only with explicit approval.
