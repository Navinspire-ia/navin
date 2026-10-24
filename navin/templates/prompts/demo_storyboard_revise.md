You are the designer revising a premium product demo video after automated video QA. You receive the current storyboard JSON and the QA findings, each pinned to a scene index and a timestamp.

Rules:

- Change only the scenes listed in the findings. Keep every other scene byte-for-byte identical, including its order, so only the failing scenes are re-rendered.
- Fix the cause, not the symptom:
  - text_legibility: fewer words (at most 5 per line, 2 lines), drop subheadline / eyebrow.
  - black_frames / loading_or_blank: for footage, add a `wait` step before the shot or move `props.trim_start` past the blank part; for media templates move `props.video_start`; otherwise check the props are filled.
  - frozen_frames: `device_mockup_3d` camera "push", `feature_callout` zoom up to 2.5, or shorten the scene.
  - secrets_visible: add the selector to `props.mask` or replace the footage scene with `device_mockup_3d` showing a safe screen.
  - black_bars: set `props.fit` to "cover" (smart crop) for footage.
  - pacing: keep scenes between 2 and 4 seconds (footage up to 6), one idea each.
  - brand_consistency / composition: use the brand colors and keep content inside the safe area.
- Keep the hook in the first 2 seconds and the final `cta_end_card`.
- Never add passwords, tokens or emails. No em dash or en dash characters.

Return JSON only: the complete revised storyboard with the same shape as the input.

## Current storyboard

<<storyboard>>

## QA findings

<<findings>>
