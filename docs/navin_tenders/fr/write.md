# Qualite de redaction par avis

Source de verite du contrat : [docs/md/tenders-write.md](../../md/tenders-write.md).

Si le dossier societe contient des **references**, des **slides types** et des **exemples** (modeles Word/PPT), `write` livre une **reponse deja prete a relire** : texte + pack Word/PPT + matrice + architecture + planning. Tu etudies, tu laisses des remarques si besoin, tu valides. Ce n'est toujours pas un depot portail automatique. Rien n'est invente.

Action : `tenders action=write id=tn-...` (Studio, Tauri, CLI, chat). La loop et le heartbeat **ne redigent jamais**.

## Ce que write livre

| Bloc | Source | Passe modele ? |
| --- | --- | --- |
| Page de garde + sommaire | Societe + avis | Non |
| Lettre de candidature | Profil + avis + extraits de modeles | Oui (route `write` → role Settings **docs**) |
| Resume executif | Profil + avis + lecture des criteres | Oui (meme appel) |
| Societe, besoin, demarche, vision | Profil + avis | Non |
| Reponse fonctionnelle / technique | Exigences + metiers + methode | Technique : oui (2e passe) |
| Methodologie | Champ `methodology` + extraits Word/PPT/slides | Oui (2e passe) |
| Suivi / KPI, RACI, gouvernance | Equipe + risques lus | Non |
| Planning | Date limite + effort + equipe | Oui (2e passe) |
| References | Fiches et/ou extraits des fichiers `reference` | Non (liste + extraits) |
| Matrice de conformite | Une ligne par exigence extraite | Non (mapping dossier) |
| Budget | Budget publie + `price_book` | Non |
| Pack Word / PPT | Modele rempli ou dossier genere (page de garde, sommaire, chapitres) | Non |
| Revue | `revise` + remarques, stage `validating` | Oui si route docs |

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

### Fichiers uploades (wizard Studio)

| Kind | Formats | Role |
| --- | --- | --- |
| `word_template` | `.docx` | Modele de reponse. Le **texte** est extrait et reutilise. |
| `ppt_template` | `.pptx` | Idem pour un modele diapos. |
| `reuse_slide` | `.docx`, `.pptx`, `.pdf` | Slides / extraits types a recoller. |
| `reference` | `.docx`, `.pptx`, `.pdf` | Memoire technique, attestations, etudes de cas. |

Actions : `tenders action=upload kind=...` ou `add-reference` (fiche sans fichier).

Limites d'extraction :

- 8 Mo par fichier
- 40 pages PDF ou 40 slides PPT
- extrait conserve jusqu'a **12 000** caracteres dans le profil
- le polish IA voit jusqu'a **1 500** caracteres par fichier
- un scan sans couche texte donne un extrait vide (le write le dit, il n'invente pas la page)

## Comment le modele travaille

1. `build_response` assemble un brouillon **deterministe** (profil + avis + extraits). Les trous restent `non renseigne` / `not on file`.
2. Si l'IA est active (`ai_assist` pas a false, `NAVIN_TENDERS_AI` pas a `off`) **et** qu'un preset est route pour le role **docs**, `polish_response` reformule lettre + resume, puis architecture / planning / methode.
3. Garde-fou : tout chiffre du texte reformule doit deja etre dans le materiau. Les marqueurs de trou doivent rester visibles. Sinon le desk **garde le template**, sans invention.
4. Un pack Word/PPT est ecrit. `revise` applique tes remarques. Tu etudies un dossier deja pret.

Le verdict GO/NO-GO n'est pas un avis du modele : les regles de score decident. Le role **deep** (`qualify`) explique seulement le verdict.

## Ce que write ne fait pas

- Pas d'envoi acheteur. `send` reste sous approval. Tu deposes sur le portail.
- Pas de chiffre, client, certificat ou date inventes.
- Un avis title-only est enrichi depuis `source_url`. Si la page est vide, le dossier reste structure avec les trous visibles.

Le livrable est **pret a relire** : tu etudies, tu laisses des remarques si besoin, tu valides.

## Pour que ce soit bon

1. Wizard societe termine (specialite + metiers + pays).
2. Au moins 3-8 references precises (client, annee, montant si public) **et** un PDF/DOCX extractible par reference forte.
3. Un modele Word et 2-5 slides types (methodo, organisation, references types).
4. Equipe + methodologie + bordereau si tu veux ces blocs remplis.
5. Settings → Models → Task routing : role **docs** pour write/mail, **deep** pour qualify.
6. `tenders action=status` puis `knowledge` puis `write id=tn-...`. Verifier `used_files` et `from_file` sur le brouillon.
7. Toi tu completes prix, planning, pieces du reglement, puis tu deposes sur le portail.

Plus les extraits sont lisibles, plus la lettre est **specifique a cet AO**. Un pack de scans vides = template honnete et sec.

## Agent dans le chat

```
/tenders
tenders action=status
tenders action=knowledge
tenders action=get id=tn-...
tenders action=write id=tn-...
```

Le skill `tender-agent` impose : si des modeles ou references sont au dossier, write **doit** reutiliser leurs extraits. `rfp-writer` peut allonger la reponse dans le chat ; il n'a pas le droit d'ajouter un client ou une certif absents.

Heartbeat : refus 403 sur start / stop / schedule / tick / collect / write / send.

## Desactiver l'IA (template seul)

- Profil : `ai_assist: false`
- Env : `NAVIN_TENDERS_AI=off`

Le write reste utilisable. La lettre n'est plus reformulee.
