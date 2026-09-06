# Tenders - qualite de redaction par avis

Si le dossier societe contient des **references**, des **slides types** et des **exemples** (modeles Word/PPT), `write` livre une **reponse deja prete a relire** : texte + pack Word/PPT + matrice + architecture + planning. Tu etudies, tu laisses des remarques si besoin, tu valides. Ce n'est toujours pas un depot portail automatique. Rien n'est invente.

Action : `tenders action=write id=tn-...` (Studio `#/tenders`, Tauri, `navin tenders`, outil `tenders`).

La loop et le heartbeat **ne redigent jamais**.

Vue desk : [tenders.md](./tenders.md). Module bilingue : [docs/navin_tenders](../navin_tenders/README.md).

## Ce que write livre

| Bloc | Source | Passe modele ? |
| --- | --- | --- |
| Lettre de candidature | Profil + avis + extraits de modeles | Oui (route `write` → role Settings **docs**) |
| Resume executif | Profil + avis + lecture des criteres | Oui (meme appel) |
| References | Fiches et/ou extraits des fichiers `reference` | Non (liste + extraits) |
| Methodologie | Champ `methodology` + extraits Word/PPT/slides | Oui (2e passe) |
| Staffing | Equipe au dossier | Non |
| Architecture | Metiers + methode + extraits (aucun stack invente) | Oui (meme route, 2e passe) |
| Planning | Date limite + effort + equipe + phases au dossier | Oui (meme passe) |
| Matrice de conformite | Une ligne par exigence extraite (avis, CDC, pieces, delai) | Non (mapping dossier) |
| Pack Word / PPT | Modele societe rempli, sinon dossier propre genere | Non (python-docx / python-pptx) |
| Revue | `revise` applique tes remarques, stage `validating` | Oui si route docs |
| Bordereau | Phrase si `price_book` est renseigne, sinon `non renseigne` | Non |

Langue : **celle de l'avis** (titre + description + CDC), sinon le pays, sinon la locale du profil.

## Ce que tu dois mettre au dossier

### Config / wizard / `knowledge`

| Champ | Role dans write |
| --- | --- |
| Nom, specialite, points forts | Phrase d'ouverture |
| Metiers (`crafts`), types d'AO, types de projets | Perimetre et matrice |
| Methodologie | Corps technique si present |
| Equipe | Staffing |
| Bordereau (`price_book`) | Mention financiere (pas de prix inventes) |
| Certifications | Score / gaps, jamais inventees dans la lettre |
| References tapees (titre, client, annee, pays, montant) | Liste references meme sans fichier |

### Fichiers uploades

| Kind | Formats | Role |
| --- | --- | --- |
| `word_template` | `.docx` | Modele de reponse. Texte extrait **et** fichier rempli (placeholders + dossier append). |
| `ppt_template` | `.pptx` | Idem pour un deck. |
| `reuse_slide` | `.docx`, `.pptx`, `.pdf` | Slides / extraits types a recoller. |
| `reference` | `.docx`, `.pptx`, `.pdf` | Memoire technique, attestations, etudes de cas. |

Actions : `tenders action=upload kind=...` ou `add-reference` (fiche sans fichier).

Limites :

- 8 Mo par fichier
- 40 pages PDF ou 40 slides PPT
- extrait stocke jusqu'a **12 000** caracteres
- le polish IA voit jusqu'a **1 500** caracteres par fichier
- un scan sans couche texte = extrait vide (write le dit, n'invente pas la page)

## Comment le modele travaille

1. `build_response` (`navin/tenders/writer.py`) assemble un brouillon **deterministe**. Les trous restent `non renseigne` / `not on file`.
2. Si l'IA est active (`ai_assist` pas a false, `NAVIN_TENDERS_AI` pas a `off`) **et** qu'un preset est route pour le role **docs**, `polish_response` reformule **lettre + resume**, puis architecture / planning / methode.
3. Garde-fou `keeps_only_known_facts` : tout chiffre du texte reformule doit deja etre dans le materiau. Les marqueurs de trou doivent rester visibles. Sinon le desk **garde le template**.
4. `attach_exports` ecrit un `.docx` et un `.pptx` (ouvre tes modeles s'ils sont valides, sinon un dossier propre). Telechargement : `tenders action=download id=... kind=docx|pptx`.
5. Toi tu etudies le dossier. `revise` + tes remarques reecrit les blocs demandes. Stage `validating`.

Le verdict GO/NO-GO n'est pas un avis du modele. Le role **deep** (`qualify`) explique seulement le verdict.

## Ce que write ne fait pas

- Pas d'envoi acheteur (`send` sous approval). Tu deposes sur le portail.
- Pas de chiffre, client ou certificat absent du dossier.
- Pas de dates inventees hors de la date limite de l'avis.
- Un avis title-only est d'abord **enrichi** depuis `source_url` (page officielle, timeout 8 s). Si la page est vide, le dossier reste structure (architecture, planning, matrice) avec les trous visibles.

Le dossier est **pret a relire**. Tu n'as pas a tout reecrire : tu etudies, tu laisses des remarques si besoin, tu valides.

## Pour que ce soit bon

1. Wizard societe termine (specialite + metiers + pays).
2. 3-8 references precises **et** un PDF/DOCX extractible par reference forte.
3. Un modele Word et 2-5 slides types.
4. Equipe + methodologie + bordereau si tu veux ces blocs remplis.
5. Settings → Models → Task routing : **docs** pour write/mail, **deep** pour qualify.
6. `tenders action=status` puis `knowledge` puis `write id=tn-...`. Verifier `used_files` et `from_file`.
7. Studio : ecran dossier → etudier → remarques optionnelles → Valider ce brouillon. Telecharge Word / PPT.
8. `tenders action=revise id=tn-... remarks=...` puis `download`.

Plus les extraits sont lisibles, plus la lettre est specifique a **cet** AO.

## Agent

```
/tenders
tenders action=status
tenders action=knowledge
tenders action=get id=tn-...
tenders action=write id=tn-...
tenders action=revise id=tn-... remarks="..."
tenders action=download id=tn-... kind=docx
```

`tender-agent` : si des modeles ou references sont au dossier, write **doit** reutiliser leurs extraits. `rfp-writer` peut allonger dans le chat sans ajouter un client ou une certif absents.

Desactiver l'IA (template seul) : profil `ai_assist: false` ou env `NAVIN_TENDERS_AI=off`.
