---
name: video-generation
description: Generate short videos with the generate_video tool (Veo, Sora, Hailuo) - scripts, storyboards, scene-by-scene AI generation, and ffmpeg assembly. Use for social clips, product demos, and ads.
metadata: {"navin":{"emoji":"🎥","category":"marketing"}}
---

# Video Generation

## Overview

Four layers of video work: the script (always), AI generation with `generate_video`, image-to-video from product shots, and assembly (ffmpeg).

## Tooling

- **`generate_video`** - text-to-video or image-to-video through the configured provider (Google Veo via Gemini, OpenAI Sora, MiniMax Hailuo). Returns a persistent artifact (`vid_...`) with a local path. Generation takes minutes; tell the user before launching several clips.
- **`generate_image`** - produce a key visual or first frame, then animate it by passing the artifact path as `reference_image` to `generate_video`.
- If `generate_video` is not in the tool list, video generation is not enabled: deliver script + storyboard + ready-to-use prompts instead, and point the user to Settings → Video to enable a provider.

## 1. Script & storyboard (the real value)

30s vertical clip structure:
- **0-3s hook** - question, striking number, or surprising visual (decides everything)
- **3-20s body** - one point, illustrated
- **20-30s payoff + CTA**

Storyboard format: `| Sec | Visual | VO/text overlay | generate_video prompt |` - validate before generating anything.

## 2. AI generation with `generate_video`

Generate scene by scene from the storyboard. Each prompt needs:

- Subject and action ("a barista pours latte art in slow motion")
- Camera movement ("slow dolly-in", "orbit", "static tripod shot")
- Style and lighting ("warm morning light, shallow depth of field, cinematic")
- On-screen text quoted exactly, if any

```text
generate_video(
  prompt="Close-up of a matte-black wireless earbud rotating on a marble pedestal, studio lighting, soft reflections, slow 360 orbit, premium product commercial style",
  aspect_ratio="9:16",
  duration_seconds=8
)
```

Image-to-video for product consistency: generate or reuse a packshot first (see `product-visuals`), then animate it:

```text
generate_video(
  prompt="Animate this product shot: gentle camera push-in while soft particles of steam rise, keep the product and label unchanged",
  reference_image="/path/to/media/generated/2026-07-21/img_ab12cd34ef56.png",
  aspect_ratio="16:9"
)
```

Aspect ratios per platform: 9:16 TikTok/Reels/Shorts, 16:9 YouTube/web, 1:1 feed.

## 3. Assembly (ffmpeg via `exec`)

```bash
# concat clips
ffmpeg -f concat -safe 0 -i list.txt -c copy out.mp4
# vertical crop for social (9:16)
ffmpeg -i in.mp4 -vf "crop=ih*9/16:ih" -c:a copy vertical.mp4
# burn subtitles + add audio track
ffmpeg -i in.mp4 -vf subtitles=subs.srt -i voix.mp3 -map 0:v -map 1:a out.mp4
```

Subtitles: always (most social video plays muted) - generate the .srt from the script.

## Workflow

1. Clarify: platform (ratio! duration!), goal, brand constraints.
2. Script + storyboard with one `generate_video` prompt per scene → user validation.
3. Generate clips scene by scene; check each artifact before the next.
4. Assemble with ffmpeg; subtitle; deliver per-platform exports via the `message` tool with artifact paths in `media`.

## Rules

- Hook first: write 5 hooks, keep 1 (`copywriting-agent` craft).
- Brand voice and visual tokens apply (`brand-voice-manager`, `template-manager`).
- Rights: only use assets (music, footage) the user owns or that are licensed.
- Keep raw artifact paths internal; deliver via the `message` tool `media` parameter.
- For full ad campaigns (concepts, declinations, copy + visuals + video), use `ad-creative-generator`.
