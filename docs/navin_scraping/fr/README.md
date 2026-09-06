# Module Scraping - Vue d'ensemble

Le module **Scraping** (barre latérale → **Scraping**, juste après **Code**, route `#/scraping`) transforme des URL du web ouvert en jeux de données propres et structurés. L'agent crawl ou fetch en parallèle, retire le chrome, enrichit les métadonnées, et exporte en CSV, JSON, JSONL, XML, Excel, Markdown ou rapport HTML - avec une URL source sur chaque ligne.

## Fonctionnement

1. Ouvrez **Scraping** dans la barre latérale (après **Code**).
2. Cliquez une carte dans l'un des trois groupes - **Collecter**, **Nettoyer & enrichir**, **Exporter** (voir [Actions](./actions.md)).
3. Le chat s'ouvre avec `/scrape` et le prompt de la carte déjà dans le compositeur (taille adaptée au texte). Ajoutez les URLs / contraintes dans le chat, puis envoyez.
4. Les scrapers écrits par l'agent utilisent **Scrapling 0.4.14**. Les jobs ponctuels passent par l'outil `scrape` (Rust si `navin-core` est compilé). JS/formulaires : `browser`. Fichiers sous `scrape/`.

Usage direct dans n'importe quel chat :

```
/scrape crawl https://example.com/docs profondeur 2, export xlsx + rapport html
/scrape transforme cette liste d'URL en corpus markdown prêt pour le RAG
```

## La commande `/scrape`

| | |
| --- | --- |
| Commande | `/scrape [url\|site\|brief]` |
| Cycle de vie | Workflow agent (tour d'agent complet) |
| Skills préchargés | `scrapling`, `scrape-operator`, `web-extractor`, `data-quality-agent`, `playwright-browser`, `deep-web-research`, `report-generator`, `spreadsheet-analyst` |
| Sortie | Jeux de données et rapports dans l'espace de travail (`scrape/…`) |

## Architecture (Scrapling d'abord, puis Rust / Playwright)

| Couche | Rôle |
| --- | --- |
| Studio WebUI | Cartes qui remplissent le chat `/scrape` - jobs en un clic |
| **Scrapling 0.4.14** | Framework Python par défaut quand l'agent écrit un scraper / spider |
| Outil Python `scrape` | Repli : `fetch`, `crawl`, `extract`, `clean`, `export`, `pipeline` |
| `navin-core` (Rust) | Fetch parallèle, extract/clean HTML, export CSV/JSON/XML/XLSX/rapport |
| Fallback httpx | Même API `scrape` sans `make native` (plus lent) |
| Outil `browser` | Pages JS, formulaires, scroll (Playwright / Chromium) |

Installer le framework : `pip install "scrapling[fetchers]==0.4.14"` puis `scrapling install`, ou `pip install -e ".[scraping]"`. Accélérateur Rust : `make native`.

## Ce que l'agent sait collecter

| Job | Approche |
| --- | --- |
| Une / quelques pages | `scrape action=fetch` |
| Site same-domain | `scrape action=crawl` ou `pipeline` avec `max_depth` / `max_pages` |
| Liste d'URL / sitemap | `fetch` parallèle, puis `export` |
| Pages JS vides | Escalade vers `browser` (`action=content` ou network + `response_body`) |
| Corpus RAG | Un Markdown par page + `manifest.json` |

## Formats d'export

| Format | Usage typique |
| --- | --- |
| `csv` / `xlsx` | Tableurs, import CRM / BI |
| `json` / `jsonl` | Pipelines, APIs, chargeurs RAG |
| `xml` | Ingest legacy / entreprise |
| `md` | Corpus lisible |
| `report` | Vue d'ensemble HTML (ok / erreurs / liens) |

## Règles de données

- **Sourcer chaque ligne** : garder l'URL (et le status) sur chaque enregistrement.
- **Ne rien inventer** : pages vides ou bloquées restent vides/erreur ; passer au browser si besoin.
- **Pas de bypass** : captcha / Cloudflare / paywall / login → pause et demande pour que tu résolves ou te connectes dans la session navigateur, puis reprise.
- **Fichiers, pas le chat** : les corpus vont sous `scrape/` dans l'espace de travail.
- **Conformité** : demander quand robots/CGU comptent.
- **SSRF-safe** : les URL sont validées avant fetch (mêmes gardes que `web_fetch`).

## Astuces

- Tester 1-3 URL avec `fetch` avant un gros crawl.
- Préférer `pipeline` quand le format d'export est déjà connu.
- Enchaîner Collecter → Nettoyer → Exporter dans un même chat.
- Combiner avec `/studio` pour un deck ou un Word de synthèse.

Voir aussi la [référence de l'outil scrape](../scrape-tool.md) (EN).
