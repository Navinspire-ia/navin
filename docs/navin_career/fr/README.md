# Navin Career - Vue d'ensemble

Le module **Career** (sidebar → **Career**, route `#/career`) est un **desk emplois et missions** : recherche live, boards officiels, CV depuis votre master, pipeline de candidatures. Accroche : *Cherchez. Adaptez un CV Word par mission. Postulez sur la page officielle.*

Le livre live est le desk Studio et l'outil `career` (meme store que Tauri sur Linux / Windows / macOS, `navin career`, et `python -m navin.career.desk_cli`). N'inventez aucune offre absente du store.

## Comment ca marche

1. Ouvrez **Career** (`#/career`).
2. Renseignez le profil (role, pays, stack, CV master).
3. Cherchez les sources live : APIs et RSS publics (Remotive, Jobicy, Remote OK, Himalayas, We Work Remotely, Arbeitnow, Hacker News Who is hiring), JSON ATS officiels (Greenhouse, Lever, Ashby, Workable), JSON-LD JobPosting des pages carrieres, portails pays, extraits web.
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

Ordre fixe : API officielle / RSS → JSON-LD JobPosting → JSON board ATS → portail officiel → extrait web en dernier. Un moteur, quatre connecteurs generiques, pas de ferme de scrapers.

- Flux publics (sans cle) : Remotive, Jobicy (API v2 : geo, industrie, tag, count ; salaire min/max, devise et periode ; niveau), Remote OK (credite, lien vers l'annonce d'origine conserve), Himalayas, RSS We Work Remotely, Arbeitnow, Hacker News Who is hiring (Algolia). Reponses en cache une heure, 16 requetes max par run, titres filtres sur le profil.
- Boards ATS : Greenhouse, Lever, Ashby, Workable (premier board reussi par slug).
- JSON-LD JobPosting : pages carrieres et offres collees exposent `baseSalary`, `employmentType`, `jobLocationType`, `experienceRequirements`, `jobStartDate`, `validThrough`. Lu comme une donnee, jamais comme un scrape.
- APIs a cle (seulement si une cle est enregistree) : Adzuna, Jooble, USAJOBS, Job Opportunities API (`JOBOPPORTUNITIES_API_KEY`, filtre pays, champs salaire et remote).
- Free-Work (FR et UK) : pages de recherche publiques lues en direct, sans login, une requete par seconde, plafond par run. Chaque mission porte TJM, duree, mode de teletravail, competences et description complete. Le scrape generique ne touche jamais l'hote.
- Portails officiels : Jadarat, Dubai Careers, boards remote UE et hotes publics similaires.
- LinkedIn : page officielle + import par collage. Pas de scrape. Pas de bot Easy Apply.
- Une liste vide est honnete si une API est down.

## Fiche normalisee

Chaque offre porte les memes champs quelle que soit la source : types de contrat (freelance, CDI, CDD, temps partiel, interim, stage, alternance), mode de teletravail, niveau et annees d'experience, demarrage, duree, plage de TJM, plage de salaire annuel et devise. La devise suit le montant publie, sinon le marche du pays recherche (FR/BE/DE → EUR, CH → CHF, GB → GBP, US → USD, AE → AED, SA → SAR, MA → MAD...). Le score convertit avec des taux indicatifs vers la devise du profil ; la fiche affiche toujours la devise publiee (`400-600 €/j`, `40k-45k €/an`, `550 £/day`). Filtres de liste : contrat, experience, mode de travail, publication, duree, TJM minimum, salaire minimum.

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
