# Module Ads - Vue d'ensemble

Le module **Ads** (barre latérale → **Autres modules → Ads**, route `#/ads`) est un **bureau media payant senior** : Google Ads, Microsoft Ads, Meta Ads, TikTok Ads, Reddit Ads et LinkedIn Ads. Chaque chiffre vient du moteur `ads` intégré (vos exports Ads Manager ou les lignes de rapport MCP), jamais du modèle. La production créative (vidéos, visuels, brand kits) reste dans le studio **Marketing** (`#/marketing`).

## Fonctionnement

1. Ouvrez **Ads** sous Autres modules.
2. Cliquez une carte : **Audit depuis les exports** (joignez vos CSV/XLSX), **Gaspillage & mots-clés négatifs**, **Approuver & appliquer les changements**, ou une carte plateforme Google / Microsoft / Meta / TikTok / Reddit / LinkedIn (vue, structure ou optimisation) - voir [Actions](./actions.md) et [Skills](./skills.md).
3. Le chat s'ouvre avec `/ads` et le prompt. Renseignez compte / KPI, puis envoyez.
4. L'agent lance le moteur `ads` (et les outils MCP connectés si disponibles), enregistre sous `ads/` + `ads-report-*.html`, et met les changements en attente dans `ads/changes.jsonl` jusqu'à votre accord.

Usage direct :

```
/ads vue Google Ads pour le customer 1234567890
/ads optimisation Meta hebdo - compte act_…
```

## La commande `/ads`

| | |
| --- | --- |
| Commande | `/ads [plateforme\|compte\|brief]` |
| Cycle de vie | Workflow agent |
| Skills préchargés | `studio-expert-contract`, `critic-reviewer`, `paid-ads-manager`, `marketing-analytics`, `conversion-rate-optimization`, `copywriting-agent`, `ad-creative-generator` |
| Board | Run tracké |
| Sortie | Fichiers sous `ads/` + `ads-report-*.html` + gate expert |

## Le moteur `ads` (analyse réelle)

L'outil `ads` est déterministe et disponible uniquement dans ce module :

| Action | Ce qu'il fait |
| --- | --- |
| `ingest` | Lit les exports Google, Microsoft, Meta, LinkedIn, TikTok ou Reddit (CSV / TSV / XLSX, téléchargements Google en UTF-16, nombres au format français, préambules de rapport) et les lignes JSON MCP ; détecte la plateforme et mappe les colonnes. |
| `analyze` / `pipeline` | CTR, CPC, CPM, CPA, CVR, ROAS et part de dépense par campagne, groupe d'annonces, annonce, mot-clé et terme de recherche ; période et projection mensuelle. |
| constats | `zero_conversion_spend`, `high_cpa`, `search_term_waste`, `low_ctr`, `low_quality_score`, `creative_fatigue`, `budget_pacing` (avec `monthly_budget`), `impression_share_limited`, `spend_concentration`, `scale_winner`, `tracking_missing`. Chacun embarque les chiffres exportés comme preuve. |
| `score` | Score de santé 0-100 : pénalités par sévérité plus un point par pourcent de dépense sans conversion. |
| `report` | Rapport déterministe JSON / Markdown / HTML (KPI, campagnes, constats, changements proposés, trous de données). |
| `changes` | File d'approbation : liste, `status=approved`, `rejected`, `applied`. Les changements approuvés viennent avec un plan d'exécution MCP (opération + relecture de vérification). |
| `export_changes` | `csv` (toutes actions), `google_editor` (négatifs + pauses pour Google Ads Editor), `microsoft_bulk` (feuille bulk Microsoft Advertising). |

Règles du desk : chiffres uniquement depuis les exports ou les lignes MCP ; colonnes manquantes signalées en `data_gaps` ; rien n'est mis en pause, rebudgété ou exclu sans un id de changement approuvé. Les seuils sont dans `tools.ads.thresholds` de la config.

## Connecter les MCP plateformes

Ouvrez **Réglages → MCP**, puis installez les presets nécessaires (catégorie **ads**) :

### Google Ads (`google-ads`)

- `pipx` + MCP officiel google-ads-mcp
- Env : `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` (MCC optionnel : `GOOGLE_ADS_LOGIN_CUSTOMER_ID`)

### Microsoft Ads (`microsoft-ads`)

- `npx -y @cesteral/msads-mcp` (API Microsoft Advertising v13)
- Env : `MSADS_ACCESS_TOKEN`, `MSADS_DEVELOPER_TOKEN`, `MSADS_CUSTOMER_ID`, `MSADS_ACCOUNT_ID`
- Docs : https://www.npmjs.com/package/@cesteral/msads-mcp

### Meta Ads (`meta-ads`)

- MCP distant : `https://mcp.facebook.com/ads`
- Champ : en-tête `Authorization` = `Bearer <token>`
- Doc : [Get started Meta Ads MCP](https://developers.facebook.com/documentation/ads-commerce/ads-ai-connectors/ads-mcp-server/ads-mcp-server-get-started)

### TikTok Ads (`tiktok-ads`)

- Serveur communauté via `uvx tiktok-ads-mcp` (le MCP officiel [Agentic Hub](https://ads.tiktok.com/help/article/about-tiktok-for-business-agentic-hub-and-mcp-server?lang=en) n'est pas encore un endpoint public self-serve)
- Env : `TIKTOK_APP_ID`, `TIKTOK_SECRET`, `TIKTOK_ACCESS_TOKEN`

### Reddit Ads (`reddit-ads`)

- `npx -y mcp-server-reddit-ads`
- Env : `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_REFRESH_TOKEN` (écriture `read` par défaut)

### LinkedIn Ads (`linkedin-ads`)

- Preset `linkedin-ads` (`npx -y @cesteral/linkedin-mcp`) + jeton Marketing API
- Env : `LINKEDIN_ACCESS_TOKEN` (`LINKEDIN_API_VERSION` optionnel)
- Doc : https://www.npmjs.com/package/@cesteral/linkedin-mcp

Les jobs cron/loop réutilisent le même env gateway.

## Astuces

- Connectez le MCP avant de demander des métriques live.
- Préférez le lecture seule ; n'approuvez pas les mutations de spend à la légère.
- Enchaînez Marketing (créas) puis Ads (structure / optimisation).
