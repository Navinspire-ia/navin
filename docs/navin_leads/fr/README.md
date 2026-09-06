# Module Leads & Ventes - Vue d'ensemble

Le module **Leads** (barre latérale → **Leads**, route `#/leads`) est un **bureau SDR senior** : chasse publique + outils scrape/search, signaux d'achat, scoring ICP/BANT-F, outreach préparé (envoi humain), export CRM. Chaque donnée est sourcée ou `unverified`. Les emails `verified` et le sync CRM live dépendent des connecteurs.

## Connecteurs pour de vrais leads

| Besoin | Config |
| --- | --- |
| Emails vérifiés | `HUNTER_API_KEY` et/ou `APOLLO_API_KEY` (env machine / shell Navin) puis `enrich_leads.py` |
| Recherche web profonde | MCP **Exa** et/ou **Firecrawl** (Réglages → MCP) |
| CRM live | MCP **HubSpot** et/ou **Salesforce** (skill `crm-update-agent` préchargé) |
| Corpus sites | outils `scrape` / `browser` ; `/scrape` autorisé depuis le module Leads |

Sans clés Hunter/Apollo, l'agent produit des listes sourcées avec patterns d'emails `unverified` - jamais des faux "verified".

## Fonctionnement

1. Ouvrez **Leads** dans la barre latérale (`#/leads`). Même store que Tauri, `navin leads` et l'outil `leads`.
2. Définissez l'ICP, puis **Start loop** (jour / semaine / week-end / mois + heure). Pause ou changement d'horaire à tout moment. Le gateway chasse puis surveille sur ce calendrier tant que Navin tourne.
3. Le heartbeat n'alerte que (tier A, signaux d'achat, relances dues). Il ne chasse jamais et n'envoie jamais une séquence.
4. Chasse ponctuelle, enrich, séquence et outreach restent sur le desk ou dans le chat. Ne créez pas une cron de chat qui hunt ou tick.

```
/leads trouve 30 SaaS RH en France, 50-200 salariés, avec leurs DRH
/leads enrichis sales/prospects-*.csv puis pousse les A dans HubSpot
```

## La commande `/leads`

| | |
| --- | --- |
| Commande | `/leads [icp\|entreprise\|brief]` |
| Skills préchargés | contrat expert, critic, DQ, prospector, signals, génération, qualification, account/entity research, outreach, persona, pipeline, **lead-enrichment**, **crm-update-agent**, **deep-web-research**, **web-extractor** |
| Board | Run tracké (`project-board`) |
| Sortie | `sales/prospects-*.csv` (+ enriched/scored) + `leads-report-*.html` + gate expert |

## Scripts

```bash
python navin/skills/lead-enrichment/scripts/enrich_leads.py --keys-check
python navin/skills/lead-enrichment/scripts/enrich_leads.py sales/prospects.csv -o sales/prospects-enriched.csv --verify-existing
python navin/skills/lead-qualification/scripts/score_leads.py sales/prospects-enriched.csv
```

## Règles de données

- Chaque donnée : URL source ou `unverified`.
- `email_status=verified` uniquement via API (Hunter/Apollo).
- Sources publiques uniquement - pas de LinkedIn connecté / login walls.
- Dédup domaine ; qualité > volume.

## Astuces

- ICP clair + 5 meilleurs clients = lookalikes plus justes.
- Activez Exa/Firecrawl + Hunter avant une chasse volume.
- Enchaînez : ICP → chasse → enrich → score → CRM.

## Reference du desk

| Page | Contenu |
| --- | --- |
| [Bureau](./desk.md) | Store unique, waterfall, BANT-F 80/55 |
| [Start loop](./loop.md) | Start / stop / horaire, superviseur 20 s |
| [Heartbeat](./heartbeat.md) | Watch seulement, grace 90 s, deadline 20 s |
| [Desktop (Tauri)](./desktop.md) | Linux, Windows, macOS, build `tsc` |
| [CLI et API](./cli-api.md) | `navin leads`, `/api/leads`, outil agent |
| [Actions](./actions.md) | 17 cartes Studio |
| [Skills](./skills.md) | Skills + MCP HubSpot / Salesforce |
