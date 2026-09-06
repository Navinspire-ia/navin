# Module Ads - Vue d'ensemble

Le module **Ads** (barre latérale → **Autres modules → Ads**, route `#/ads`) est un **bureau media payant senior** : Google Ads, Meta Ads, TikTok Ads, Reddit Ads et LinkedIn Ads. Préférez les lectures MCP live aux métriques inventées. La production créative (vidéos, visuels, brand kits) reste dans le studio **Marketing** (`#/marketing`).

## Fonctionnement

1. Ouvrez **Ads** sous Autres modules.
2. Cliquez une carte Google / Meta / TikTok / Reddit / LinkedIn (vue, structure ou optimisation) - voir [Actions](./actions.md) et [Skills](./skills.md).
3. Le chat s'ouvre avec `/ads` et le prompt. Renseignez compte / KPI, puis envoyez.
4. L'agent utilise les outils MCP connectés, enregistre sous `ads/` + `ads-report-*.html`.

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

## Connecter les MCP plateformes

Ouvrez **Réglages → MCP**, puis installez les presets nécessaires (catégorie **ads**) :

### Google Ads (`google-ads`)

- `pipx` + MCP officiel google-ads-mcp
- Env : `GOOGLE_ADS_DEVELOPER_TOKEN`, `GOOGLE_PROJECT_ID`, `GOOGLE_APPLICATION_CREDENTIALS` (MCC optionnel : `GOOGLE_ADS_LOGIN_CUSTOMER_ID`)

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
