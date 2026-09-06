# Audit CRM (Atomic CRM)

Statut: **in_review**. Pas `passed`. AWS reste bloque.

Source: https://github.com/marmelab/atomic-crm  
Licence: MIT (fichier `LICENSE.md` present, copyright Marmelab / Francois Zaninotto).

## OK

- Licence MIT reelle (pas seulement un README).
- Stack React + Vite + shadcn + Supabase, demo fakerest (`npm run dev:demo`).
- Produit complet: contacts, companies, deals, tasks, notes, import/export.
- MCP produit deja la: `get_schema`, `query`, `mutate`, `display_task_list`, `complete_task`.
- RLS documente sur query/mutate.

## Bloquants avant AWS

- `supabase/signing_keys.json` contient une cle privee JWT locale (`d`). Exclu du copy (`signing_keys.json`).
- `supabase/functions/.env` contient des secrets de demo (Postmark, publishable key). Exclu (`.env`).
- Theme amont: Inter + gris shadcn generique. Restyle Navin applique (navy / CTA bleu, Plus Jakarta Sans).
- Dashboard amont: `return null` pendant le loading (ecran blanc). Remplace par un skeleton.
- `framer-motion` manquant. Ajoute sur le dashboard.
- Agents Navin absents amont. Ajoutes dans ce package.

## Happy path local (sans Docker) - OK 2026-08-15

```text
cd templates_apps/crm
npm install
npm run dev:demo
```

Demo fakerest sur le port 5174. Verifie:

- Dashboard: titre Navin CRM, Hot Contacts, Latest Activity, Upcoming Tasks (pas d'ecran blanc)
- Nav Contacts: liste de contacts mock
- Nav Deals: pipeline

Reset au reload. AWS toujours refuse (`in_review` != `passed`).

Full stack (Supabase + Docker): `make install && make start` puis http://localhost:5173/

## Pas encore fait

- Tests e2e Playwright du package Navin
- Seed demo signe + tarball S3
- Tools produit hors MCP SQL (wrappers `search_leads` / `create_deal` en HTTP local)
