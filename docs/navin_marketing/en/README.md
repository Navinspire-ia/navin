# Marketing module — Overview

The **Marketing** module (sidebar → **Marketing**, route `#/marketing`) is a full-stack marketing studio. From a single brief, the agent produces real deliverables end to end: ad copy, platform-native social posts, product visuals, advertising videos with scripts, email sequences, landing page copy — every asset saved as a file in your workspace.

## How it works

1. Open **Marketing** in the sidebar.
2. (Optional) Type a **brief** at the top: product, audience, goal, tone. It is attached to every action.
3. Pick an action card in one of the three groups — **Creative**, **Content**, **Strategy** (see [Actions](./actions.md)).
4. The chat opens and `/campaign` is sent automatically with the action's specification and your brief.
5. The agent defines the persona and key message, produces the deliverables, saves the assets, and ends with a summary table of files and paths.

Direct usage in any chat:

```
/campaign launch campaign for our new eco water bottle, target young urban athletes
```

## The `/campaign` command

| | |
| --- | --- |
| Command | `/campaign [brief]` |
| Lifecycle | Agent workflow (runs a full agent turn) |
| Skills preloaded | `campaign-manager`, `ad-creative-generator`, `social-media-manager`, `copywriting-agent`, `image-generation`, `video-generation`, `brand-voice-manager`, `customer-persona-builder` |
| Output | Copy, images, videos, plans — saved to the workspace with a deliverables summary |

## Media generation providers

Image and video generation use the providers configured in **Settings**:

- **Images** — the configured image-generation provider/model produces product packshots, lifestyle scenes, social visuals, and brand assets. Aspect-ratio variants (square, story, landscape) are generated per platform.
- **Videos** — when a video-generation provider is configured, the agent writes the script and storyboard, then generates the actual video. Without one, it still delivers the full script, scene breakdown, on-screen text, and voiceover lines ready for production.

The agent always keeps a consistent brand voice across assets (`brand-voice-manager`), and can research the market and competitors with web tools before creating.

## What you can produce

| Category | Deliverables |
| --- | --- |
| Creative | Product images, ad videos, social visuals per format, brand kits (logo directions, palette, typography, voice) |
| Content | Social posts per platform with hooks and hashtags, blog articles, 5-email sequences, landing page copy |
| Strategy | 360° campaigns, customer personas, 30-day content plans, competitor analyses |

## Tips

- Give the brief once at the top; every card reuses it.
- Chain actions in the same chat: persona first, then the 360° campaign inherits it.
- Ask for platform variants: "adapt the video script for TikTok (15s) and YouTube (30s)".
- Everything is a file: images, videos, calendars, and copies land in the workspace, ready to publish.
