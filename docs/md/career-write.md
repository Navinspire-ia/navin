# Career - CV adapte a chaque mission

Le Master CV reste la source unique. `prepare` (aliases `write` / `cv` / `tailor`) livre un **pack pret a relire pour cette offre** : texte reordonne, lettre, notes ATS, fichier Word. Tu etudies, tu corriges si besoin, tu postules sur la **page employeur**. Rien n'est invente. Ce n'est pas un Easy Apply.

Action : `career action=prepare id=job-...` (Studio `#/career`, Tauri, `navin career`, outil `career`).

La loop et le heartbeat **ne redigent jamais** et **ne postulent jamais**.

Vue desk : [career.md](./career.md). Module bilingue : [docs/navin_career](../navin_career/README.md).

## Ce que prepare livre

| Bloc | Source | Passe modele ? |
| --- | --- | --- |
| CV texte | Master CV + experiences + stack + formation | Seulement si `ai_assist` est **true** |
| Competences alignees | Intersection offre / dossier | Non |
| Autres competences | Stack au dossier, hors mots-cles de l'offre | Non |
| Experiences | Fiches profil, reordonnees vers l'annonce | Non (ordre seulement) |
| Lettre / message | Titre + societe + faits deja au CV | Seulement si `ai_assist` est **true** |
| Notes ATS | Mots-cles matches + termes absents (non ajoutes) | Non |
| Pack Word | `python-docx` : CV + lettre + notes | Non |
| Apply | Ouvre l'URL officielle ; toi tu soumets | Non |

Langue : **FR** si la premiere langue du profil commence par `fr`, ou si le pays de l'offre / residence est FR, BE, LU, MC, CH. Sinon **EN**.

## Ce que tu dois mettre au dossier

### Profil / wizard

| Champ | Role dans prepare |
| --- | --- |
| Nom, email, telephone | En-tete du CV |
| Titres, headline | Bandeau |
| Stack, points forts, faits | Alignement et phrases d'ouverture |
| Experiences (titre, societe, periode, faits) | Corps, reordonne par score mots-cles |
| Formation | Bloc education, jamais invente |
| Master CV (texte) | Paragraphes reordonnes vers l'annonce |
| Pays, langues | Locale du pack |

Sans master **et** sans experiences **et** sans stack, le pack dit `Master CV absent du dossier` / `Master CV is not on file`. `apply` refuse tant que le texte CV est vide.

### Fichiers

Le desk ecrit un `.docx` sous `~/.navin/career/files/`. Telechargement : `career action=download id=job-...`.

Pas de modele Word societe (contrairement a Tenders). Le fichier est genere proprement a chaque prepare.

## Comment le modele travaille

1. `build_pack` (`navin/career/writer.py`) assemble un brouillon **deterministe**. Les termes de l'offre absents du dossier restent dans `keywords_missing` et **ne sont pas ajoutes** au CV.
2. Le polish IA ne tourne que si `ai_assist` est **true** et que `NAVIN_CAREER_AI` / `NAVIN_TENDERS_AI` n'est pas `off`. Defaut : template seul.
3. Garde-fou `keeps_only_known_facts` : aucun employeur, date, outil ou diplome invente. Sinon le desk **garde le template**.
4. `attach_exports` ecrit le `.docx`. Stage `ready`. `next_action` : relire, puis ouvrir l'URL d'origine.
5. `apply` ouvre la page officielle. LinkedIn + mode autopilot est refuse. La loop et le heartbeat n'appellent jamais `prepare` ni `apply`.

## Ce que prepare ne fait pas

- Pas d'envoi employeur. Toi tu colles / uploades sur leur page.
- Pas d'Easy Apply, pas de scrape LinkedIn.
- Pas de stack, stage ou certification absents du Master CV.
- Pas de redaction depuis la loop ou le heartbeat.
- Pas de cron de chat qui prepare ou postule.

Le pack est **pret a relire pour cette mission**. Tu n'as pas a tout reecrire : tu etudies, tu ajustes le master si un trou est vrai, tu relances prepare, tu postules.

## Pour que ce soit bon

1. Wizard profil termine (titre + pays + stack).
2. Master CV lisible (vrai texte, pas de lorem) **et** 3-8 experiences datees.
3. Stack reelle, pas une wishlist.
4. Settings → Models → Task routing : role **docs** seulement si tu actives `ai_assist`.
5. `career action=status` puis `prepare id=job-...`. Verifier `keywords_matched`, `keywords_missing`, `pack_ready`.
6. Studio : carte offre → Adapter le CV a cette mission → relire le preview → telecharger Word → Apply (page employeur).
7. `career action=download id=job-...`.

Plus le master est precis, plus le CV de **cette** mission est specifique.

## Agent

```
/career
career action=status
career action=get id=job-...
career action=prepare id=job-...
career action=download id=job-...
career action=apply id=job-...
```

Skills `career-cv` / `cv-tailoring` : reordonner et echo l'annonce. Jamais ajouter un employeur ou un outil absent du master.

Activer l'IA (opt-in) : profil `ai_assist: true`. Couper : `ai_assist` absent ou false, ou env `NAVIN_CAREER_AI=off`.
