---
name: career-advisor
description: Recommend career moves, skill investments, and trajectory options based on profile, market demand, and goals. Use for career decisions and development plans.
metadata: {"navin":{"emoji":"🧗","category":"careers"}}
---

# Career Advisor

## Overview

Career advice grounded in the person's actual profile and real market data — not generic listicles.

## Analysis framework

### 1. Position audit
- Current: role, skills (rated), achievements, salary, energy map (what energizes vs drains)
- Trajectory so far: pattern of moves, what each added

### 2. Market reality check
`web_search` current demand: which roles are growing, what skills appear in target job postings, salary ranges per market (FR/DZ/Gulf/remote).

### 3. Options on the table
For each path (stay & grow / move up / move sideways / specialize / independent):

```markdown
| Option | 2-year upside | Risk | Skill gap | First step |
```

### 4. Skill investment plan
Gap between current profile and target role, ranked by market value ÷ effort. Concrete: which certification, which project to build, which visibility action (`linkedin-optimizer`).

## Workflow

1. Interview the user: history, constraints (geography, family, finances), definition of success at 5 years.
2. Run the market check on their 2–3 candidate directions.
3. Present the options table with a clear recommendation and reasoning.
4. Build the 90-day plan: skills, visibility, network actions, with `cron` check-ins if wanted.

## Rules

- Respect constraints as given — don't advise "just move to Dubai" casually.
- Market claims come with sources and dates.
- Ambition calibrated to evidence: encourage stretch, flag fantasy.
