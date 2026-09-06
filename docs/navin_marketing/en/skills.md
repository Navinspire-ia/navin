# Marketing skills

## Preloaded on the Marketing module

These load with `product_module=marketing` (Studio `#/marketing`) even without a slash command.

| Skill | Purpose |
| --- | --- |
| `marketing-strategist` | Wired to the `marketing` tool: status, brand, understand, loop start/stop/schedule. Source of truth is the desk store. |
| `growth-marketing` | Experiments on the desk (`improve` / metrics). Weekly review = desk loop, never a chat cron. |
| `digital-marketing` | Funnel and channel mix. Super render bar for pages vs PPT. |
| `email-marketing` | Sequences and deliverability. Persist copy with `marketing action=content`. |
| `marketing-analytics` | Measurement honesty. Write numbers with `marketing action=metrics`. |

## Preloaded by `/campaign` / `/montage`

| Skill | Purpose |
| --- | --- |
| `studio-expert-contract` | Senior desk contract: evidence, deliverables, PASS/WARN/BLOCK. Also on `/marketing`. |
| `critic-reviewer` | Critical review before delivery. |
| `campaign-manager` | Brief, message house, calendar, UTMs (`check_campaign_brief.py`). Persist with `plan`. |
| `ui-ux-pro-max` | Page stack: Motion, Lenis, Embla, Lucide, three + R3F + drei. Designed 3D. |
| `presentation-designer` | Decks: poster type, photos, native charts. No WebGL on a slide. |
| `pptx-generator` | Editable PPTX. A screenshot slide is rejected. |
| `ad-creative-generator` | Ad concepts and placements. Then `generate_image` / `generate_video` + Visual QA. |
| `social-media-manager` | Platform-native formats and cadence. |
| `copywriting-agent` | Headlines, body, CTAs. |
| `image-generation` / `video-generation` | Configured providers. |
| `product-visuals` | Packshots, lifestyle, 360. No fake 3D inside PowerPoint. |
| `brand-voice-manager` | Voice consistency. |
| `customer-persona-builder` | Data-backed personas. |
| `montage-studio` | Live demo, exports under `marketing/montage/`. Never auto-publish. |

## Complementary

| Skill | Purpose |
| --- | --- |
| `market-research` / `competitor-intelligence` | Then `marketing action=competitor` or `research`. |
| `paid-ads-manager` | Prefer the **Ads** studio `/ads` + MCP presets. |
| `go-to-market-planner` | Launch plans. Pair with `marketing action=launch`. |
| `conversion-rate-optimization` | Landing and funnel. |
| `content-generation` / `content-recycler` / `blog-writer` | Scale and recycle. |
| `fact-checker` / `proofreader` | Quality pass. Still no auto-publish. |

Invoke a skill by name when you want a focused pass. Facts that the loop must see still go through the `marketing` tool.
