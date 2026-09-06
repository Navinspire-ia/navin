# Module Tenders - Vue d'ensemble

Le module **Tenders** (barre laterale → **Tenders**, route `#/tenders`) est un **bureau d'appels d'offres publics** : collecte officielle, score 0-100, Go/No-Go, brouillon de dossier, pipeline jusqu'a Won/Lost.

Le livre live est le store local (`~/.navin/tenders/`). Studio, Tauri (Linux / Windows / macOS), `navin tenders`, `python -m navin.tenders.desk_cli` et l'outil `tenders` lisent le **meme** store. N'inventez jamais un avis qui n'y figure pas.

## Fonctionnement

1. Ouvrez **Tenders** (`#/tenders`). Terminez le wizard societe (nom, pays, devise, specialite, pays cibles, metiers, sources).
2. Deposez references, modeles Word/PPT et slides types (voir [Qualite de redaction](./write.md)).
3. **Start loop** (quotidien / semaine / week-end / mois + heure) ou Collect ponctuel. La loop chasse puis surveille sur ce calendrier tant que le gateway tourne. Elle n'envoie jamais un mail acheteur.
4. Qualifiez (`qualify`). Sur GO, `write` livre un dossier pret (texte + Word/PPT + matrice). Vous etudiez, vous remarquez si besoin, vous validez. Le depot se fait sur le **portail acheteur**, pas dans Navin.
5. Heartbeat : follow / watch seulement. Silencieux si rien de nouveau. Jamais collect, write, start ou send.

```
/tenders
tenders action=status
tenders action=write id=tn-...
```

## Loop et heartbeat (contrat Career)

| Piece | Role |
| --- | --- |
| Une loop | Superviseur gateway `navin-tenders-loop` (toutes les 20 s). Pas de cron de chat `tenders-loop`. |
| Stop gagne | `action=start` / `stop` / `schedule`. Un Pause ecrit tout de suite (intent) et s'applique a la fin du cycle. |
| Heartbeat silencieux | `tick_watch` avant le tour LLM. Digest injecte seulement si `count > 0`. |

La loop se repare seule : chasse perimee, JSON casse, retry apres erreur, timeout si un portail bloque. Details dans le code `navin/tenders/loop.py` et `navin/tenders/heartbeat.py`.

Ne creez **pas** une cron de chat qui collect ou tick.

## La commande `/tenders`

| | |
| --- | --- |
| Commande | `/tenders` |
| Outil | `tenders` (meme store que Studio) |
| Skills | `tender-agent`, `rfp-writer`, `tender-monitor`, proposal / contract, scrape, `archify` |
| Sortie | Avis dans le store + brouillons + journal |

## Sources (ouvertes, pas d'agregateur payant)

Ordre fixe : API officielle → open data → HTML structure → `web_search` + `scrape` sur hotes officiels. Un mur login / captcha / Cloudflare n'est pas contourne.

SAM.gov : API v2 avec `SAM_API_KEY` / `SAM_GOV_API_KEY`. Sans cle, aucun avis federal US invente.

## Regles

- Citer `source_url`. Jamais inventer un titre ou un avis.
- Jamais poster une offre publique sauf `send_mode=autonomous` **et** confirmation. Defaut : **approval**.
- `write` n'invente aucun chiffre, client ou certificat absent du dossier.
- Pas de tiret long Unicode (U+2014 / U+2013) dans les sorties.

## Suite

- [Qualite de redaction](./write.md) - references, slides types, exemples
- [Actions](./actions.md)
- [Skills](./skills.md)
- [Desktop (Tauri)](./desktop.md) - Linux, Windows, macOS
