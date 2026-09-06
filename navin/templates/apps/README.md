# App templates : ou ca vit, comment on les prend, comment l'agent les lance

Meme idee que les skills. Le catalogue est dans Navin. Le package s'installe dans le workspace. L'utilisateur peut forker l'overlay sans retoucher tout le repo amont.

## Ou on les met

| Couche | Chemin | Versionne ? | Role |
|--------|--------|-------------|------|
| Catalogue | `navin/templates/apps/catalog.py` | oui, code Navin | Index : slug, licence, GitHub, agents |
| Cache etude | `templates_apps/<slug>/` | non (gitignore) | Clones amont pour normaliser |
| Package signe | S3 `templates/v1/<slug>.tar.gz` | oui, releases | Archive Navin-ifiee (futur) |
| Install workspace | `<projet>/.navin/apps/<slug>/` | oui, avec le projet | Plugin : manifeste + agents + overrides user |
| Code applicatif | `<projet>/` (racine) | oui | L'app elle-meme (CRM, chat, POS, ...) |

Chaque template a un playbook `packages/<slug>/install.json` (env, Docker, DB, launch, modify).
Il est copie dans `.navin/apps/<slug>/install.json` a l'install. L'agent le lit avant toute modif.
Regenerer : `python -m navin.templates.apps.study`.

Le user ne "lance" pas le clone gitignore. Il installe un **plugin Navin** dans son projet.

```text
mon-crm/
  app/                      # code du produit (normalise depuis Atomic CRM)
  docker-compose.yml
  .env.example
  THIRD_PARTY_NOTICES.md
  .navin/
    project.json
    apps/
      crm/
        navin.json          # manifeste installe
        overlay.json        # spec user (gagne sur navin.json)
        agents/
          sales-agent/
            agent.yaml
            system.md
            tools.json
          lead-qualification-agent/
          ...
```

## Comment on les recupere

1. L'UI ou l'agent lit le catalogue (`list_app_templates` / Marketplace Apps).
2. L'utilisateur choisit `crm` (ou dit "installe le CRM").
3. Navin telecharge `https://navinagent.s3.eu-north-1.amazonaws.com/templates/v1/<slug>.tar.gz` (catalogue `templates/v1/catalog.json`). Le dossier local `templates_apps/` n'est qu'un fallback. Publier / ecraser : `make aws-upload-temp`.
4. Extraction dans le dossier cible + ecriture de `.navin/apps/crm/`.
5. Seed + `.env.example` + notices de licence.

Commandes :

```text
navin app list
navin app info crm
navin app create crm ./mon-crm
navin app install crm          # dans le workspace courant
navin app publish crm          # refuse tant que audit != passed
```

Dans la WebUI : galerie au centre de Code (`#/code?panel=templates`), bouton Templates dans la barre du workbench, cartes + bouton **Use**.

AWS : `make aws-upload-temp` pousse et ecrase `templates/v1/<slug>.tar.gz` + `catalog.json`.
L'icone Templates dans Code lit ce catalogue S3. `navin app publish` reste refuse tant que l'audit n'est pas `passed`.

## Comment l'agent les reperer et les lancer

Meme pattern que l'outil `skill` (noms dans le prompt, details a la demande).

Outil `app_template` :

| action | Effet |
|--------|--------|
| `list` | Templates du catalogue + ceux deja installes dans `.navin/apps/` |
| `find` | Recherche "relance leads" → crm / sales-agent |
| `read` | Charge `navin.json` + overlay user |
| `install` | Pose le package dans le workspace |
| `launch` | Lit `launch` (ex. `docker compose up` / `pnpm dev`) et demarre |
| `run_agent` | Spawn un agent du template (sales-agent, ...) avec ses tools |

Exemple user :

> Trouve mes 20 leads les plus chauds et cree les taches de relance pour lundi.

L'agent fait :

1. `app_template find query="leads relance"`
2. voit `crm` installe, agent `follow-up-agent` + tools `read_leads`, `create_tasks`
3. `app_template run_agent name=follow-up-agent`
4. l'agent metier appelle les tools du produit, pas un chat hors-sol

Decouverte auto au boot : si `.navin/apps/*/navin.json` existe, Navin les indexe comme les skills workspace.

## Comment on les rend exploitables comme des plugins

On ne revend pas le repo GitHub. On ajoute une **couche Navin** par-dessus.

Avant publication d'un template :

1. Garder le code amont MIT/Apache + `THIRD_PARTY_NOTICES.md`
2. Ecrire `navin.json` (launch, permissions, RAG, agents)
3. Ecrire `agents/<id>/system.md` + `tools.json` (tools qui parlent au vrai produit)
4. Seed demo + Docker + `.env.example`
5. Signer et pousser le tarball

Un agent metier ressemble a un skill, mais avec des tools produit :

```yaml
id: sales-agent
name: Sales Agent
permissions:
  - leads.read
  - leads.write
  - tasks.create
tools:
  - search_leads
  - update_lead
  - create_task
  - create_deal
rag:
  collections: [sales-playbook, products, pricing]
```

## Comment le user specifie et modifie

Il ne fork pas Medusa ou Atomic CRM pour changer un prompt. Il edite l'overlay.

Fichier `<projet>/.navin/apps/crm/overlay.json` :

```json
{
  "schema": "navin-app-overlay.v1",
  "slug": "crm",
  "disabled_agents": ["sales-forecast-agent"],
  "agent_overrides": {
    "follow-up-agent": {
      "system": "Relance uniquement en francais, tutoiement, jamais le dimanche.",
      "model": "configurable",
      "tools": ["search_leads", "create_task", "generate_email"]
    }
  },
  "extra_agents": [
    {
      "id": "whatsapp-closer",
      "name": "WhatsApp Closer",
      "description": "Relance les deals chauds sur WhatsApp",
      "tools": ["search_leads", "send_whatsapp"],
      "enabled": true
    }
  ],
  "env": { "CRM_PIPELINE": "enterprise" },
  "notes": "On ignore le forecast, on pousse WhatsApp."
}
```

Regle de merge : **overlay user > navin.json package > catalogue**.

Le user peut aussi :

- desactiver un agent dans l'UI
- changer le system prompt d'un agent
- ajouter / retirer un tool
- ajouter un agent perso (comme un skill custom dans `.navin/skills/`)
- garder ses overrides quand on met a jour le package amont

C'est le meme contrat que les skills : built-in dans Navin, custom dans `.navin/`, le workspace gagne.
