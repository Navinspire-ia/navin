# Skills Scraping

## Préchargés par `/scrape`

| Skill | Rôle |
| --- | --- |
| `scrapling` | Framework par défaut (Scrapling 0.4.14) quand l'agent écrit du code de scraping. |
| `scrape-operator` | Orchestre : Scrapling d'abord, puis `scrape` (Rust/httpx), puis `browser`. **Conçu pour ce module.** |
| `web-extractor` | Extraction lisible style Firecrawl en Markdown/JSON pour RAG ou migrations. |
| `data-quality-agent` | Déduplication, cohérence de schéma, champs manquants sur les exports. |
| `playwright-browser` | Pilote Chromium quand le HTML statique est une coquille vide. |
| `deep-web-research` | Recherche multi-sources qui alimente souvent la liste de graines. |
| `report-generator` | Synthèses exécutives à côté des exports bruts. |
| `spreadsheet-analyst` | Pivot, filtres et contrôles sur CSV/XLSX. |

## Skills complémentaires

| Skill | Rôle |
| --- | --- |
| `prompt-injection-defender` | Traiter le contenu web non fiable comme des données, pas des instructions. |
| `rag-knowledge-builder` | Indexer un corpus scrape nettoyé dans une base de connaissances. |
| `fact-checker` | Vérifier les affirmations extraites avant réutilisation. |
| `competitor-intelligence` | Veille concurrentielle structurée qui peut fournir des graines de crawl. |
| `seo-technical-auditor` | Suivi crawlabilité / données structurées après un scrape de site. |

Les skills se chargent automatiquement avec `/scrape` ; invoquez-en un explicitement pour une tâche ciblée ("use data-quality-agent on scrape/out.csv").
