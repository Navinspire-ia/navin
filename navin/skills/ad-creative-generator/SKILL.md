---
name: ad-creative-generator
description: Produce complete ad creative sets - concepts, hooks, copy, AI-generated images and videos declined per platform and format. Use for Meta, Google, LinkedIn, TikTok campaigns and product launches.
metadata: {"navin":{"emoji":"🎬","category":"marketing"}}
---

# Ad Creative Generator

Turn a product or offer into a complete, ready-to-launch creative set: concepts, copy, and AI-generated visuals/videos for every placement. Handoff media buying (budgets, audiences, bidding) to `paid-ads-manager`.

## When to use

- Paid or organic ad creative packages
- Product launch visual systems

## When not to use

- Long-form landing pages only (`copywriting-agent`)
- Campaign calendars without creatives (`campaign-manager`)

## Tooling

- **`generate_image`** - key visuals, packshots, lifestyle, banners; reference images for consistency
- **`generate_video`** - 6-10s ads (text-to-video or image-to-video)
- **`visual_qa`** - required still-image delivery gate for dimensions, ratio,
  sharpness, contrast, alpha, safe zones and vision-based product/logo/text/color fidelity
- **`exec` + ffmpeg/Pillow** - resize, overlays, concat, subtitles
- If tools missing: deliver brief + exact prompts; tell user which Settings to enable

## Workflow

### 1. Creative brief (never skip)

Product + key benefit, audience/ICP, platform(s), objective (awareness/traffic/conversion), brand constraints, offer/CTA, language(s). Align with `campaign-manager` message house.

### 2. Concepts before pixels

Propose 3 distinct concepts (angle + visual + hook). Angles: problem/solution, before/after, social proof, demo, lifestyle, pattern-interrupt. User picks, or ship the strongest if async.

### 3. Copy per placement

For the chosen concept, per platform:

- Hook / headline (3 variants)
- Primary text (short + long)
- CTA matching objective
- Hard limits: Google RSA 30/90, Meta ~125 visible, LinkedIn ~150

### 4. Visual generation

Build one **master key visual**, validate, then decline with `reference_images`:

| Placement | Ratio | Notes |
|-----------|-------|-------|
| Feed Meta/LinkedIn | 1:1 | safe margins for text |
| Stories/Reels/TikTok | 9:16 | subject in middle 60% |
| YouTube/display | 16:9 | logo + CTA visible |
| Display sizes | 300x250, 728x90 | Pillow resize if needed |

Leave negative space; quote any baked-in text in the prompt.

### 5. Video ads

6-10s: hook → benefit → CTA. Prefer image-to-video from the master for product consistency. Variants 9:16, 1:1, 16:9. Subtitles via ffmpeg (`video-generation` skill).

### 6. Delivery

- Run `visual_qa` on every final still with placement requirements and all
  authoritative references. Rework WARN and BLOCK results. Never auto-deliver
  a BLOCK asset, and never claim fidelity without a reference.
- Save under `marketing/creatives/<campaign>/`
- Summary table: concept, per-placement copy, asset paths + ratios
- Name for A/B: `concept-A-hook1-9x16`
- Attach media via `message` when appropriate

## Rules

- One concept, many declinations - never mix concepts in one ad set.
- Brand tokens (`brand-voice-manager`) override generic style.
- Never invent claims, prices, or stats; ask or omit.
- Legal: no competitor logos, no fake endorsements; respect platform policies.
- Iterate masters with `reference_images` instead of regenerating from scratch.

## Anti-patterns

- Shipping pixels with no hooks/CTA
- Unreadable text baked into images
- Claiming expected CPA without account data
