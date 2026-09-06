# Navin Career - Vue d'ensemble

Le module **Career** (sidebar → **Career**, route `#/career`) est un **desk emplois et missions** : recherche live, boards officiels, CV depuis votre master, pipeline de candidatures. Accroche : *Cherchez. Adaptez un CV Word par mission. Postulez sur la page officielle.*

Le livre live est le desk Studio et l'outil `career` (meme store que Tauri sur Linux / Windows / macOS, `navin career`, et `python -m navin.career.desk_cli`). N'inventez aucune offre absente du store.

## Comment ca marche

1. Ouvrez **Career** (`#/career`).
2. Renseignez le profil (role, pays, stack, CV master).
3. Cherchez les sources live : Remotive, JSON ATS officiels (Greenhouse, Lever, Ashby), portails pays, extraits web.
4. LinkedIn est un **onglet officiel**. Collez une offre deja ouverte. Navin ne scrape jamais LinkedIn et ne clique jamais Easy Apply.
5. `prepare` adapte le Master CV a **cette** mission (reordre, mots-cles, lettre, Word). Relisez, puis postulez sur la **page employeur**.
6. Demarrez la **loop desk** pour la chasse recurrente (`collect` puis `watch`) tant que le gateway tourne. Heartbeat ne fait que watch les matchs et follow-ups.

```
/career
career action=start
career action=prepare id=job-...
career action=watch
```

Qualite de redaction : [write.md](./write.md). Loop : [loop.md](../en/loop.md) · [contrat](../../md/career.md).

## La commande `/career`

| | |
| --- | --- |
| Commande | `/career` |
| Skills | `career-search`, `career-cv`, `career-apply` |
| Sortie | Offres dans le store local + packs adaptes + pipeline |

## Sources (ouvertes, pas une ferme de scrape)

Ordre fixe : API officielle → JSON board ATS → portail officiel → extrait web en dernier.

- Ingest live : Remotive, Greenhouse, Lever, Ashby (premier board reussi par slug).
- Portails officiels : Jadarat, Dubai Careers, boards remote UE et hotes publics similaires.
- LinkedIn : page officielle + import par collage. Pas de scrape. Pas de bot Easy Apply.
- Une liste vide est honnete si une API est down.

## Loop desk vs heartbeat

| Horloge | Travail |
| --- | --- |
| Loop desk | Collect puis watch sur le calendrier disque |
| Heartbeat | `watch` seulement. Jamais search, collect, start, tick, prepare ou apply. |

Ne creez pas de cron de chat qui cherche ou tick. Details : [Career loop](../en/loop.md) · [contrat partage](../../studio/desk-loop.md).

## Garde-fous

- Jamais inventer un titre ou une societe.
- Jamais scraper LinkedIn.
- Jamais soumettre Easy Apply ou un formulaire employeur.
- Mode d'envoi par defaut : approval. Vous cliquez apply.
- Stop pendant un hunt gagne toujours.

## Liens

- Produit : `/career` sur navin.live
- Desktop (Tauri) : [desktop.md](./desktop.md)
- Write : [write.md](./write.md)
- Guides : `/blog/agent-recherche-emploi-ia-navin-career`
- Compare : `/compare/indeed`, `/compare/linkedin-easy-apply`, `/compare/teal`
