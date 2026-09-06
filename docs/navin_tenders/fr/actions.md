# Actions Tenders

Studio `#/tenders`, Tauri, `navin tenders`, `python -m navin.tenders.desk_cli` et l'outil `tenders` partagent le meme store.

## Lecture

| Action | Livre |
| --- | --- |
| `status` / `snapshot` | Livre complet : profil, pipeline, loop, book. A appeler en premier. |
| `get` | Un avis `id=tn-...`. |
| `search` / `list` | Filtre texte, pays, stage, GO. |
| `index` / `file` / `read-file` | Index local et extraits (`dossier.md`, `knowledge.md`, fichiers). |

## Dossier societe

| Action | Livre |
| --- | --- |
| `profile` | Wizard : nom, pays, devise, specialite, metiers, sources, seuils. |
| `knowledge` | Equipe, bordereau, methodologie, clauses. |
| `upload` | `kind=word_template` / `ppt_template` / `reuse_slide` / `reference`. |
| `add-reference` | Fiche reference sans fichier. |
| `remove-file` | Retire un modele ou une reference. |
| `custom-source` / `secret` / `notify` | Source HTML/API, cle SAM, canaux d'alerte. |

Details d'usage pour write : [Qualite de redaction](./write.md).

## Pipeline

| Action | Livre |
| --- | --- |
| `collect` | APIs officielles puis search+scrape sur hotes publics. Un portail en 404 n'arrete pas les autres. |
| `qualify` / `score` / `gonogo` | Score 0-100 + GO/NO-GO. `id` requis. |
| `write` / `draft` | Dossier pret : lettre, resume, architecture, planning, matrice, pack Word/PPT. |
| `revise` / `review` | Applique tes remarques. Stage `validating`. |
| `download` / `export` | Telecharge le `.docx` ou `.pptx` genere. |
| `stage` | `discovered` → … → `go` / `no-go` → `drafting` → `submitted` → `won` / `lost`. |
| `mail` | Brouillon `clarification`, `ack` ou `relance`. N'envoie pas. |
| `send` | Envoi canal desk, seulement si `approved=true` (defaut approval). |
| `follow` / `watch` | Digest GO / delais. Heartbeat autorise uniquement ces lectures + follow. |
| `crm-sync` | Pousse les GO vers le CRM projet. |
| `rescore` | Recalcule le livre apres un changement de profil. |

## Loop (pas de cron de chat)

| Action | Livre |
| --- | --- |
| `start` | Arme la loop. Exige le wizard complet. `run_now` lance un cycle. |
| `stop` / `pause` | Pause. Gagne toujours, meme pendant un collect. |
| `schedule` | Change l'horaire (daily, weekdays, weekend, weekly, monthly). |
| `tick` | Cycle force (collect + watch). Jamais depuis le heartbeat. |

```
/tenders write the dossier for tn-... using only references in the profile
navin tenders start --kind weekdays --hour 8 --minute 30
navin tenders pause
```

Le depot public se fait sur le portail acheteur. La loop n'envoie jamais un mail acheteur.
