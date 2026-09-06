---
name: campaign-manager
description: Plan and run multichannel campaigns with an editorial calendar, asset tracking, and post-mortems. Use for any coordinated marketing push across channels.
metadata: {"navin":{"emoji":"🗓️","category":"marketing"}}
---

# Campaign Manager

Turn strategy into a shipped campaign: brief, message house, calendar, assets, launch checklist, report. Senior bar: measurable KPIs, UTM hygiene, no invented ROAS.

## AWS media templates + Montage tools

If the turn includes media references (runtime context `MEDIA TEMPLATE (AWS)` or `MEDIA REFERENCES (AWS)`, up to 6 per turn):

- Use the local files as the brand / product visual base (family + tag + format).
- With several references, COMBINE them into one output: each plays the role of its family (style = look, character = face, color = palette, element = product, location = set, structure = layout, camera = lens, effects = finish, stock = subject). Never produce one output per reference.
- Pass the downloaded files to `generate_image` (`reference_images`) / `generate_video` (`reference_image`), and keep the reference aspect ratio.
- The files are ALREADY materialized under `.navin/resources/media-templates/` (see the runtime context local paths). Never re-download the s3 urls yourself.
- If the AWS master is missing, the runtime auto-falls back to the official preview still (`<id>-preview.jpg`): use that still as the reference. NEVER fabricate placeholder media (ffmpeg lavfi, solid colors, drawtext).
- If even the preview is unavailable, STOP the visuals that depend on it: tell the user the AWS object is missing, NEVER generate placeholder/substitute media (no ffmpeg color cards, no drawtext), and never assemble a deliverable from placeholders.

Marketing produces the campaign (offer, ICP, CTA, channels). For pixels and motion, call Montage generation tools: `generate_image`, `generate_video`, `generate_music`, `generate_speech`, `montage(action=assemble|package|render|stock_search)`. Gate every final still with `visual_qa` before delivery. Never claim those tools are unavailable.

## When to use

- Coordinated launches or always-on campaigns across channels
- 360° studio requests that need a plan + assets

## When not to use

- Single ad creative with no plan (`ad-creative-generator`)
- Pure brand voice definition (`brand-voice-manager`)

## Campaign brief (always first)

```markdown
## Campaign: <name>
- Objective + KPI target (metric, baseline, target, window)
- Audience / persona
- Offer + CTA
- Key message / message house (promise, proof, CTA)
- Funnel role per channel (TOFU/MOFU/BOFU)
- Channels + why each exists
- Budget / resources (or "organic only")
- Dates: start / end
- Assets needed: [list with owners]
- Tracking: UTM plan + landing URL
```

Validate with:

```bash
python navin/skills/campaign-manager/scripts/check_campaign_brief.py marketing/campaigns/<name>-brief.md
```

## Message house

| Layer | Content |
|-------|---------|
| Promise | one-sentence value |
| Pillars | 3 proof themes |
| Proof | case, number, demo (real only) |
| CTA | single primary action |

## Workflow

1. Write the brief; run the checker; get user validation when stakes are high.
2. Build editorial calendar: channel × date × asset (`marketing/campaigns/<name>.md`).
3. Produce assets via specialists: `copywriting-agent`, `social-media-manager`, `ad-creative-generator`, `email-marketing`, image/video tools. Pages use the Marketing super render stack (`ui-ux-pro-max`: Motion + Lenis + Embla + Lucide + Three.js / R3F / drei, `--stack threejs`, designed scene). Decks use `presentation-designer` (no extra npm, no WebGL). Never GSAP, Locomotive, Spline, Lottie spam, particles, Three.js wallpaper, Theatre, Barba.
4. Pre-flight: UTMs, landing live, brand voice, legal claims, then `visual_qa`
   with placement sizes, ratio, safe zones and authoritative product/brand
   references. Rework WARN/BLOCK and never auto-deliver BLOCK assets.
5. During: note what to monitor (user supplies ad accounts/analytics); do not invent live metrics.
6. Post-mortem within a week of end: results vs target (from user data), what worked, reusable assets.

## Calendar format

```markdown
| Date | Channel | Asset | Funnel | Status | Owner | UTM / Link | KPI |
```

## Rules

- Every outbound link carries UTM (source/medium/campaign[/content]).
- No channel joins without a specific goal and KPI.
- Never invent CPC/ROAS/CVR; label estimates or require Ads/GA exports.
- Archive post-mortems for the next campaign.

## Anti-patterns

- Channel laundry lists with no message house
- Assets without CTA or landing
- "Awareness" with no measurable proxy
