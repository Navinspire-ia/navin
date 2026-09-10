# Navin Career - Overview

The **Career** module (sidebar → **Career**, route `#/career`) is a **job and mission desk**: live search, official boards, CV from your master file, application pipeline. Tagline: *Search. Tailor a Word CV per mission. Apply on the official page.*

The live book is the Studio desk and the `career` tool (same store as Tauri on Linux / Windows / macOS, `navin career`, and `python -m navin.career.desk_cli`). Do not invent a job that is not in that store.

## How it works

1. Open **Career** in the sidebar (`#/career`).
2. Set the profile (role, countries, stack, master CV).
3. Search live sources: public job APIs and RSS (Remotive, Jobicy, Remote OK, Himalayas, We Work Remotely, Arbeitnow, Hacker News Who is hiring), official ATS JSON (Greenhouse, Lever, Ashby, Workable), JSON-LD JobPosting on career pages, country portals, web snippets.
4. LinkedIn is an **official tab**. Paste an offer you already opened. Navin never scrapes LinkedIn and never clicks Easy Apply.
5. `prepare` tailors the Master CV to **that** mission (reorder, keywords, cover, Word). Review it, then apply on the **employer page**. Details: [Write quality](./write.md).
6. Start the **desk loop** for recurring hunt (`collect` then `watch`) while the gateway is up. Heartbeat only watches new matches and follow-ups. The loop never writes or applies.

```
/career
career action=start
career action=prepare id=job-...
career action=watch
```

## The `/career` command

| | |
| --- | --- |
| Command | `/career` |
| Skills | `career-search`, `career-cv`, `career-apply` |
| Output | Offers in the local store + tailored drafts + pipeline |

## Sources (open, no scrape farm)

Order is fixed: official API / RSS → JSON-LD JobPosting → ATS board JSON → official portal → web snippet last. One engine, four generic connectors, no scraper farm.

- Public feeds (no key): Remotive, Jobicy (API v2: geo, industry, tag, count; salary min/max, currency and period; level), Remote OK (credited, link to the original listing kept), Himalayas, We Work Remotely RSS, Arbeitnow, Hacker News Who is hiring (Algolia). Responses are cached one hour, 16 requests max per run, titles filtered on the profile.
- ATS boards: Greenhouse, Lever, Ashby, Workable (first successful board per slug).
- JSON-LD JobPosting: company careers pages and pasted offers expose `baseSalary`, `employmentType`, `jobLocationType`, `experienceRequirements`, `jobStartDate`, `validThrough`. Read as data, never as a scrape.
- Keyed APIs (only with a key on file): Adzuna, Jooble, USAJOBS, Job Opportunities API (`JOBOPPORTUNITIES_API_KEY`, country filter, salary and remote fields).
- Free-Work (FR and UK): public search pages read live, no login, one request per second, capped per run. Each mission carries TJM, duration, remote mode, skills and the full description. Generic scrape never touches the host.
- Official portals: Jadarat, Dubai Careers, EU remote boards and similar public hosts.
- LinkedIn: open official page + paste import. No scrape. No Easy Apply bot.
- Empty results are honest when an API is down.

## Normalized card

Every offer carries the same fields whatever the source: contract kinds (freelance, permanent, fixed-term, part-time, temporary, internship, apprenticeship), remote mode, experience level and years, start date, duration, day rate range, yearly salary range and currency. The currency follows the posted amount, otherwise the market of the searched country (FR/BE/DE → EUR, CH → CHF, GB → GBP, US → USD, AE → AED, SA → SAR, MA → MAD...). Match score converts with indicative rates to your profile currency; the card always prints the posted currency (`400-600 €/j`, `40k-45k €/an`, `550 £/day`). List filters: contract, experience, work mode, posted within, duration, min day rate, min salary.

## Desk loop vs heartbeat

| Clock | Work |
| --- | --- |
| Desk loop | Collect then watch on the saved wall-clock calendar |
| Heartbeat | `watch` only. Never search, collect, start, tick, prepare or apply. |

Do not create a chat cron that searches or ticks. Details: [Career loop](./loop.md) · [shared contract](../../studio/desk-loop.md).

## Guardrails

- Never invent a job title or company.
- Never scrape LinkedIn.
- Never auto-submit Easy Apply or any employer form.
- Default send mode is approval. You click apply.
- Stop during a hunt always wins. A force tick while paused stays paused.

## Related

- Product: `/career` on navin.live
- Desktop (Tauri): [desktop.md](./desktop.md)
- Write quality: [write.md](./write.md)
- Guides: `/blog/agent-recherche-emploi-ia-navin-career`
- Compare: `/compare/indeed`, `/compare/linkedin-easy-apply`, `/compare/teal`
- Trading uses the same two-clock contract: [Trading](../navin_trading/en/README.md)
