---
name: campaign-manager
description: Plan and run multichannel campaigns with an editorial calendar, asset tracking, and post-mortems. Use for any coordinated marketing push across channels.
metadata: {"navin":{"emoji":"🗓️","category":"marketing"}}
---

# Campaign Manager

## Overview

Turn strategy into a shipped campaign: brief, calendar, assets, launch, report.

## Campaign brief (always first)

```markdown
## Campaign: <name>
- Objective + KPI target: ...
- Audience: ...
- Key message: ...
- Channels: ...
- Budget / resources: ...
- Dates: start / end
- Assets needed: [list with owners]
```

## Workflow

1. Write the brief; get user validation before producing anything.
2. Build the editorial calendar: per channel, per date, per asset (store as `marketing/campaigns/<name>.md`).
3. Produce assets via specialists: `copywriting-agent`, `social-media-manager`, `email-marketing`, `paid-ads-manager`, `pptx-generator`.
4. Pre-flight check: links tracked (UTM), landing pages live, brand voice consistent.
5. During: monitor daily, adjust creatives/budget on early signal.
6. Post-mortem within a week: results vs target, what worked, reusable assets.

## Calendar format

```markdown
| Date | Channel | Asset | Status | Owner | Link |
```

## Rules

- Every outbound link carries UTM parameters (source/medium/campaign).
- No channel joins the campaign without a specific goal.
- Archive post-mortems — the next campaign starts from the last one's learnings.
