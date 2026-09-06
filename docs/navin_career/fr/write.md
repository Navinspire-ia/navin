# Qualite de redaction par mission

Le CV master est la seule source. `prepare` (aliases `write` / `cv` / `tailor`) livre un **pack pret a relire pour cette offre** : texte reordonne, lettre, notes ATS, fichier Word. Vous etudiez, vous corrigez le master si un trou est vrai, puis vous postulez sur la **page employeur**. Rien n'est invente. Ce n'est pas Easy Apply.

Action : `career action=prepare id=job-...`. La loop et le heartbeat **ne redigent jamais** et **ne postulent jamais**.

## Ce que prepare livre

| Bloc | Source | Passe modele ? |
| --- | --- | --- |
| CV texte | Master + experiences + stack + formation | Seulement si `ai_assist` est **true** |
| Competences alignees | Intersection offre / dossier | Non |
| Autres competences | Stack au dossier hors mots-cles | Non |
| Experiences | Fiches profil, reordonnees | Non (ordre seulement) |
| Lettre / message | Titre + societe + faits deja au CV | Seulement si `ai_assist` est **true** |
| Notes ATS | Matches + termes absents (non ajoutes) | Non |
| Pack Word | `python-docx` : CV + lettre + notes | Non |
| Apply | Ouvre l'URL officielle | Non |

Langue : **FR** si la premiere langue du profil commence par `fr`, ou pays FR / BE / LU / MC / CH. Sinon **EN**.

Sans master, experiences et stack, le pack dit `Master CV absent du dossier`. `apply` refuse tant que `cv_text` est vide.

Telechargement : `career action=download id=job-...` (fichier sous `~/.navin/career/files/`).

## Ce que prepare ne fait pas

- Pas d'envoi employeur. Vous deposez sur leur page.
- Pas d'Easy Apply, pas de scrape LinkedIn.
- Pas de stack ou certificat absent du master.
- Pas de redaction depuis la loop ou le heartbeat.

Le polish IA est **opt-in** (`ai_assist: true`). Defaut : template deterministe. Couper : `NAVIN_CAREER_AI=off`.

Details et checklist : [docs/md/career-write.md](../../md/career-write.md).
