# Navin Career - Overview

The **Career** module (sidebar → **Career**, route `#/career`) is a **job and mission desk**: live search, official boards, CV from your master file, application pipeline. Tagline: *Search. Tailor a Word CV per mission. Apply on the official page.*

The live book is the Studio desk and the `career` tool (same store as Tauri on Linux / Windows / macOS, `navin career`, and `python -m navin.career.desk_cli`). Do not invent a job that is not in that store.

## How it works

1. Open **Career** in the sidebar (`#/career`).
2. Set the profile (role, countries, stack, master CV).
3. Search live sources: Remotive, official ATS JSON (Greenhouse, Lever, Ashby), country portals, web snippets.
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

Order is fixed: official API → ATS board JSON → official portal → web snippet last.

- Live ingest: Remotive, Greenhouse, Lever, Ashby (first successful board per slug).
- Official portals: Jadarat, Dubai Careers, EU remote boards and similar public hosts.
- LinkedIn: open official page + paste import. No scrape. No Easy Apply bot.
- Empty results are honest when an API is down.

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
