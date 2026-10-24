---
name: video-generation
description: Generate short videos with the generate_video tool (Veo, Sora, Hailuo) - scripts, storyboards, scene-by-scene AI generation, and assembly with the montage tool. Use for social clips, product demos, and ads.
metadata: {"navin":{"emoji":"🎥","category":"marketing"}}
---

# Video Generation

## Overview

Four layers of video work: the script (always), AI generation with `generate_video`, image-to-video from product shots, and assembly (`montage`).

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

## 3. Assembly (the `montage` tool, never raw ffmpeg)

Do not hand-write ffmpeg commands through `exec`: the `montage` tool already does the editing with progress, cancel, probing and platform canvases (see `montage-studio`).

- `montage(action=probe, path=...)` - real clip length before trimming or placing transitions.
- `montage(action=assemble, visuals=..., durations=..., trims=..., transition=fade, music=..., voice=..., srt=..., profile=...)` - one MP4 from clips, stills, music, narration and burned subtitles.
- `montage(action=timeline_save, ...)` when the user may adjust the cut in Montage Studio, then `timeline_render`.
- `montage(action=package, path=..., profiles=default)` - 16:9 / 9:16 / 1:1 platform exports.
- For a product demo, promo or launch film, follow the "Premium demo video" recipe in `montage-studio` and the `motion-design` playbook (storyboard, scripted capture, polish, motion scenes, premium assemble, video QA loop). AI clips from `generate_video` are B-roll inside that edit, not the deliverable on their own.

Subtitles: always (most social video plays muted) - generate the .srt from the script.

## Workflow

1. Clarify: platform (ratio! duration!), goal, brand constraints.
2. Script + storyboard with one `generate_video` prompt per scene → user validation.
3. Generate clips scene by scene; check each artifact before the next.
4. Assemble with `montage(action=assemble)` or a timeline; subtitle; package per-platform exports with `montage(action=package)`; deliver via the `message` tool with artifact paths in `media`.

## Rules

- Hook first: write 5 hooks, keep 1 (`copywriting-agent` craft).
- Brand voice and visual tokens apply (`brand-voice-manager`, `template-manager`).
- Rights: only use assets (music, footage) the user owns or that are licensed.
- Keep raw artifact paths internal; deliver via the `message` tool `media` parameter.
- For full ad campaigns (concepts, declinations, copy + visuals + video), use `ad-creative-generator`.
