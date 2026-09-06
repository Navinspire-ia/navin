# Loop, heartbeat et write des desks

Documentation du contrat d'autonomie des desks **Tenders** (`#/tenders`), **Career** (`#/career`), **Trading** (`#/trading`) et **Marketing** (`#/marketing`). Deux horloges, un store, jamais une troisieme cron de chat.

| Page | Contenu |
| --- | --- |
| [Contrat](./contrat-loop-heartbeat.md) | Deux horloges, allow-lists, Stop, tick force, interdits |
| [Tenders](./tenders.md) | Chasse collect + watch, write, surfaces |
| [Tenders write](./tenders-write.md) | References, slides types, exemples, qualite par avis |
| [Career](./career.md) | Hunt collect + watch, surfaces, interdits LinkedIn |
| [Career write](./career-write.md) | CV adapte par mission, Word, notes ATS, opt-in IA |
| [Trading](./trading.md) | Cycle scan / debat / risk / journal, paper only |
| [Marketing](./marketing.md) | Mesure / learn / improve, briefs, jamais de publish |
| [Auto-reparation](./auto-reparation.md) | Recovery, timeouts, backoff, non-blocage |
| [CLI et surfaces](./cli-et-surfaces.md) | Studio, Tauri, `navin`, outil agent, HTTP |
| [Tests](./tests.md) | Suites a lancer, ce qui n'est pas un test live |

Doc produit Marketing (panes, actions, IA, FR + EN) : [navin_marketing](../navin_marketing/README.md).

Pages EN publiques (navin.live) : [Career](../navin_career/en/README.md) · [Career write](../navin_career/en/write.md) · [Trading](../navin_trading/en/README.md) · [deux horloges](../studio/desk-loop.md).

Relance `navin gateway` apres un deploiement pour charger ce process. Un hunt ou un cycle deja en cours n'est pas tue.
