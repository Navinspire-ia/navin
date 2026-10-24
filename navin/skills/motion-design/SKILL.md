---
name: motion-design
description: Storyboard and motion-design craft for premium product demo, promo and launch videos - hook in the first 2 s, one idea per scene, 2-4 s scenes, kinetic type, safe areas, brand usage, pacing to beats, CTA. Use before montage motion_render / premium assemble, and when reviewing a cut after video QA.
metadata: {"navin":{"emoji":"🎞️","category":"marketing"}}
---

# Motion Design

You are the designer of the marketing team (with the marketing lead on brief and copy). You write the storyboard JSON the Montage motion renderer plays, and you judge the cut after video QA. The execution recipe (capture, polish, render, assemble, QA loop) lives in `montage-studio` > "Premium demo video"; this playbook is the craft.

## Storyboard contract

```json
{
  "version": 1,
  "format": "16:9",
  "fps": 30,
  "brand": {"name": "", "colors": ["#hex"], "font": "", "logo": ""},
  "scenes": [
    {"template": "hero_intro", "duration": 3, "props": {"headline": "", "subheadline": ""}, "transition": "fade", "narration": ""}
  ],
  "audio": {"music": "auto", "voiceover": true, "captions": true, "snap_to_beats": true}
}
```

- `format`: `16:9` (YouTube, LinkedIn, site), `9:16` (Reels, TikTok, Shorts), `1:1` (feeds). Author for the primary format; the pipeline re-renders every scene per format, so layouts must not depend on one ratio.
- `fps`: 30 by default, 60 only for fast UI motion.
- `template`: one of `montage(action=motion_templates)`: `hero_intro`, `kinetic_title`, `feature_callout`, `device_mockup_3d`, `stats_counter`, `split_compare`, `lower_third`, `cta_end_card`, `logo_sting`, `gradient_stage`, or `footage` for a scripted capture of the real product.

Template props (exact names; media props take a real screenshot or clip path):

| Template | Props |
| --- | --- |
| `hero_intro` (3D) | `headline`, `subheadline`, `eyebrow`, `image` |
| `kinetic_title` | `lines` (1-4, required), `emphasis`, `style`, `align` |
| `feature_callout` | `media` (required), `bbox`, `label`, `title`, `zoom`, `video_start` |
| `device_mockup_3d` (3D) | `media` (required), `device` laptop/phone/browser, `camera` orbit/push/tilt, `headline`, `url` |
| `stats_counter` | `stats` [{value, label, prefix, suffix}] (required), `title` |
| `split_compare` | `before`, `after` (required), labels, `title` |
| `lower_third` | `name` (required), `role` |
| `cta_end_card` | `headline`, `button`, `url` |
| `logo_sting` | `tagline` |
| `gradient_stage` | `title`, `subtitle`, `eyebrow` |

## The rules

1. **Hook in the first 2 seconds.** Scene 1 is `hero_intro`, `kinetic_title` or `device_mockup_3d` and states the outcome the viewer gets ("Close deals while you sleep"), never the company history or a logo alone.
2. **One idea per scene.** If a scene needs "and", split it.
3. **2 to 4 seconds per motion scene**, 3 to 6 seconds per footage scene, `logo_sting` 1.5 to 3 seconds. Total 15 to 45 seconds for social, up to 90 seconds for a site demo.
4. **Three.js is always in the film.** At least one `device_mockup_3d` or `hero_intro` scene: the 3D layer frames the real product, it is not wallpaper.
5. **Real product, real UI.** Feature scenes use `footage` (scripted capture) or `device_mockup_3d` with a real screenshot. Never invent screens, numbers, customers or awards; `stats_counter` only shows numbers present in the facts.
6. **End on the CTA.** `cta_end_card` with one verb-first call to action and the URL, optionally followed by `logo_sting`.

## Kinetic type

- At most 6 words per line, 2 lines per scene; verbs first; no full sentences on screen.
- One type family (the brand font), two weights maximum. Size for the phone: a title must stay readable on a 6 inch screen at the 9:16 render (QA checks legibility on downscaled frames).
- Words animate in reading order; hold the final state at least 0.8 s before the cut.
- High contrast against the background: brand color on near-black or near-white, never mid-grey on a gradient.
- No em dash or en dash characters; use a plain hyphen.

## Safe areas

- Keep text, logos and key UI inside the central 90% of the frame (title safe).
- 9:16: keep text inside the middle 80% of the height; platform UI covers the top 12% and the bottom 20%.
- 1:1: center the composition; nothing important in the outer 6%.
- `lower_third` sits above the bottom 20% in vertical formats.

## Brand usage

- Colors from the brand block only: one dominant, one accent, neutrals. Gradients use brand colors.
- Logo: in `hero_intro` or `logo_sting` and on the `cta_end_card`; never stretched, never on a busy background.
- Tone of the narration follows the brand voice (`brand-voice-manager`).

## Pacing and audio

- Cut on the beat: set `audio.snap_to_beats` so scene boundaries snap to the music beat grid; keep scene durations close to beat multiples (at 120 BPM a beat is 0.5 s).
- Narration: about 2.5 words per second, one short line per scene, written into `narration` so the segmented voice-over and captions line up with the scene.
- Music bed under narration, ducked automatically; target -14 LUFS integrated, true peak below -1 dBTP.
- Captions always on: most social video plays muted.

## Footage scenes (scripted capture)

```json
{"template": "footage", "duration": 5,
 "props": {"url": "https://app.example.com/deals",
           "login": {"secret_ref": "acme_demo"},
           "steps": [{"action": "goto", "url": "https://app.example.com/deals"},
                     {"action": "wait", "ms": 1200},
                     {"action": "click", "selector": "[data-test=new-deal]"},
                     {"action": "type", "selector": "#name", "text": "Northwind renewal"}],
           "caption": "Create a deal in one click"},
 "transition": "slideleft"}
```

- Login only through a stored `secret_ref`. Never write a password, token, API key or real email in a storyboard, a prompt or a caption.
- Seed demo data; hide anything personal with `props.mask` (CSS selectors).
- Wait for the page to settle before the shot: no spinner, skeleton or blank frame on screen.

## Reading video QA

QA pins every finding to a scene index and a timestamp. Fix the cause in that scene only; keep the others identical so only they re-render:

| Finding | Fix |
| --- | --- |
| `text_legibility` | fewer words (5 per line, 2 lines), drop subheadline / eyebrow, stronger contrast |
| `black_frames` / `loading_or_blank` | add a `wait` step or `props.trim_start` for footage; fill missing props for motion scenes |
| `frozen_frames` | `device_mockup_3d` camera `push`, `feature_callout` zoom up to 2.5, or shorten |
| `secrets_visible` | mask the selector or swap the footage for `device_mockup_3d` |
| `black_bars` | `props.fit: "cover"` (smart crop) |
| `pacing` | back to 2-4 s, one idea |
| `silence` / `loudness` | fix the mix in the premium assemble step, not the scenes |

Two revise rounds maximum; anything still open goes to the human with the QA report and contact sheet.
