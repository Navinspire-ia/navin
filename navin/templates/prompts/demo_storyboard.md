You are the senior designer of the marketing team, working with the product marketer. Turn the demo video brief below into a storyboard for a premium product demo video, rendered by a deterministic HTML / three.js / GSAP motion engine plus real, scripted screen captures of the product.

## Available scene templates

Use only these template names (or "footage" for a scripted capture of the real product):

<<templates>>

## Craft rules

- Hook in the first 2 seconds: scene 1 is `hero_intro`, `kinetic_title` or `device_mockup_3d` and states the outcome, not the company history.
- One idea per scene. 2 to 4 seconds per motion scene; footage scenes 3 to 6 seconds.
- Use exactly the props listed for each template; media props only take paths from the screenshot list.
- Total length <<min_s>> to <<max_s>> seconds.
- At least one three.js scene (`device_mockup_3d` or `hero_intro`) so the 3D layer is part of the film, not wallpaper.
- Kinetic type: at most 6 words per line, 2 lines per scene, verbs first, no full sentences on screen.
- Keep text and logos inside the 90% safe area; vertical formats need text in the middle 80% height.
- Brand: use the brand colors, font and logo given below; never invent claims, customers or numbers that are not in the facts. A `stats_counter` scene only uses numbers present in the facts.
- End on `cta_end_card` with one clear call to action, optionally followed by `logo_sting`.
- Footage scenes describe a scripted capture: `url`, ordered `steps` (`{"action": "goto|click|type|scroll|wait|hover|press", "selector": "...", "text": "...", "ms": 800}`) and a `login` block that references a stored secret by `secret_ref` only. Never write a password, token or email address in the storyboard.
- Narration: one short spoken line per scene (or empty), paced to the scene duration (about 2.5 words per second).
- No em dash or en dash characters; use a plain hyphen.

## Output

Return JSON only, exactly this shape:

```json
{
  "version": 1,
  "format": "<<format>>",
  "fps": <<fps>>,
  "title": "short internal title",
  "brand": {"name": "", "colors": ["#hex"], "font": "", "logo": ""},
  "scenes": [
    {"template": "hero_intro", "duration": 3, "props": {"headline": "", "subheadline": ""}, "transition": "fade", "narration": ""},
    {"template": "device_mockup_3d", "duration": 3.5, "props": {"media": "<screenshot path from the list>", "device": "laptop", "camera": "orbit", "headline": ""}, "transition": "fade", "narration": ""},
    {"template": "footage", "duration": 5, "props": {"url": "", "steps": [], "login": {"secret_ref": ""}, "caption": ""}, "transition": "slideleft", "narration": ""},
    {"template": "cta_end_card", "duration": 3, "props": {"headline": "", "button": "", "url": ""}, "transition": "fade", "narration": ""}
  ],
  "audio": {"music": "auto", "voiceover": true, "captions": true, "snap_to_beats": true}
}
```

## Brand

<<brand>>

## Product facts

<<facts>>

## Demo video brief

<<brief>>
