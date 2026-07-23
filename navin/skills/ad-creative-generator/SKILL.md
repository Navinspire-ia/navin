---
name: ad-creative-generator
description: Produce complete ad creative sets — concepts, hooks, copy, AI-generated images and videos declined per platform and format. Use for Meta, Google, LinkedIn, TikTok campaigns and product launches.
metadata: {"navin":{"emoji":"🎬","category":"marketing"}}
---

# Ad Creative Generator

Turn a product or offer into a complete, ready-to-launch creative set: concepts, copy, and AI-generated visuals/videos for every placement.

## Tooling

- **`generate_image`** — key visuals, packshots, lifestyle scenes, banner declinations. Supports reference images for iterative edits and product consistency.
- **`generate_video`** — 6–10s video ads (text-to-video, or image-to-video from a validated key visual).
- **`exec` + ffmpeg/Pillow** — resize, crop, add text overlays, concat, subtitle.
- If a tool is missing from the tool list, deliver the creative brief + exact prompts instead, and tell the user which setting to enable (Settings → Image / Settings → Video).

## Workflow

### 1. Creative brief (never skip)

Collect or infer: product + key benefit, audience/ICP, platform(s), objective (awareness, traffic, conversion), brand constraints (colors, fonts, tone, logo), offer/CTA, language(s).

### 2. Concepts before pixels

Propose 3 distinct creative concepts, one line each (angle + visual idea + hook). Examples of angles: problem/solution, before/after, social proof, demo, lifestyle aspiration, contrast/pattern-interrupt. Let the user pick one (or produce the strongest if async).

### 3. Copy per placement

For the chosen concept, write per platform:
- **Hook / headline** (3 variants — the best creative dies with a weak hook)
- **Primary text** (short + long variant)
- **CTA** matching the objective
- Respect hard limits: Google RSA 30/90 chars, Meta ~125 visible chars, LinkedIn 150.

### 4. Visual generation

Build one **master key visual** first, validate, then decline:

```text
generate_image(
  prompt="Product hero shot of [product] on [setting], [brand color] accent lighting, negative space top-left for headline text, premium advertising photography, ultra sharp",
  aspect_ratio="1:1", image_size="2K"
)
```

Decline the validated master with `reference_images` for consistency:

| Placement | Ratio | Notes |
|---|---|---|
| Feed Meta/LinkedIn | 1:1 | headline in the image, safe margins |
| Stories/Reels/TikTok | 9:16 | key element in the middle 60% |
| YouTube/display banner | 16:9 | logo + CTA visible |
| Google Display 300x250, 728x90 | explicit sizes | via image_size or Pillow resize |

Always leave negative space for text; quote in the prompt any text that must render in the image.

### 5. Video ads

6–10s per clip, structure hook → benefit → CTA. Prefer image-to-video from the validated key visual for product consistency:

```text
generate_video(
  prompt="Animate: slow push-in on the product while background lights shift from dusk to golden, keep product and label unchanged, add subtle floating particles",
  reference_image="<key visual artifact path>",
  aspect_ratio="9:16", duration_seconds=8
)
```

Generate per-ratio variants (9:16, 1:1, 16:9). Subtitles/text overlays via ffmpeg (`video-generation` skill has the commands).

### 6. Delivery

- Send all artifacts via the `message` tool `media` parameter.
- Summarize the set: concept, per-placement copy table, list of assets with ratios.
- Name variants clearly for A/B testing (concept-A-hook1-9x16, etc.).

## Rules

- One concept, many declinations — never mix concepts inside one ad set.
- Brand tokens (`brand-voice-manager`, `template-manager`) override generic style choices.
- Never invent claims, prices, or statistics; ask or omit.
- Legal: no competitor logos, no fake endorsements, respect platform ad policies (e.g. no before/after body claims on Meta health ads).
- Iterate on the master visual with `reference_images` instead of regenerating from scratch — keeps product identity stable.
- For paid campaign structure (budgets, audiences, bidding), hand over to `paid-ads-manager`.
