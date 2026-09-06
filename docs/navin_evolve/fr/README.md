# Evolve Engine

L'Evolve Engine est un daemon natif en Rust qui teste, casse, diagnostique, corrige et optimise vos propres projets, puis prouve chaque changement avec un certificat signé. Le tableau de bord le pilote depuis l'onglet Evolve de Navin Code dans la WebUI (`#/code?panel=evolve` ; l'ancien lien `#/evolve` y mène toujours) ; tout fonctionne aussi via la CLI `navin-engine`.

## Principe produit

Break -> Diagnose -> Fix -> Prove -> Evolve -> Certify. Le moteur ne modifie jamais votre arbre de travail directement : chaque expérience tourne dans un worktree Git isolé (`.navin/shadow/<run-id>`), et les changements acceptés arrivent sous forme de branche Git que vous fusionnez explicitement. Il ne fait pas non plus confiance au jugement du LLM : seules les mesures décident, et chaque acceptation est adossée à un certificat cryptographique.

## Démarrage rapide

1. Ouvrez le tableau de bord Evolve et sélectionnez un projet (ce doit être un dépôt Git avec du code commité).
2. Cliquez sur « Démarrer le daemon ».
3. Laissez les trois champs vides (le moteur détecte vos commandes), choisissez **Prove**, et lancez.
4. Lisez le score de robustesse (0-100) et les verdicts par panne dans la carte « Preuve de robustesse ». Ensuite, lancez **Optimize** ou **Evolve** pour laisser le moteur améliorer le code, et fusionnez la promotion si les chiffres vous plaisent.

## Le daemon

Chaque projet a son propre daemon : un processus d'arrière-plan qui écoute sur un port loopback éphémère, publié avec un jeton d'accès dans `.navin/evolve/endpoint.json`. Rien d'autre sur la machine ne peut le piloter sans lire ce fichier, et rien depuis l'extérieur ne peut l'atteindre. Le transport est le même sur Windows, macOS et Linux.

Un projet ouvert depuis Windows sur `\\wsl.localhost\<distro>\...` est un projet Linux : le daemon est démarré à l'intérieur de la distribution, via `wsl.exe`, pour que les preuves tournent avec la chaîne d'outils que le projet utilise réellement. Son fichier d'endpoint se lit directement à travers le partage Windows, et WSL 2 redirige son port loopback vers l'hôte : le tableau de bord le joint comme n'importe quel autre daemon. La distribution doit avoir `navin-engine` sur son propre PATH.

- **Démarrer** : cliquez sur « Démarrer le daemon » dans le tableau de bord, ou lancez `navin-engine daemon /chemin/du/projet`.
- **Arrêter** : cliquez sur l'icône d'alimentation à côté du badge « daemon en ligne », ou faites Ctrl+C dans son terminal.
- **Inspecter** : `navin-engine status /chemin/du/projet` affiche la file de jobs ; `.navin/evolve/daemon.log` contient la sortie complète.
- Au repos il consomme environ 5 Mo de RAM : le laisser tourner ne pose aucun problème. Les jobs s'exécutent un par un ; les nouveaux lancements se mettent en file derrière le job en cours.
- **Arrêter un job** : « Arrêter ce job » à côté d'un job en file ou en cours dans le tableau de bord. Un job en file est abandonné avant de démarrer ; un job en cours est interrompu à son étape suivante, son worktree shadow est détruit et l'app qu'il avait lancée est tuée avec tout son groupe de processus : rien ne continue de tourner ni d'occuper un port. Arrêter un job laisse le daemon en place, et le job suivant de la file démarre aussitôt.

## Les champs de lancement (les trois sont optionnels)

| Champ | Rôle | Si laissé vide |
|---|---|---|
| Commande de démarrage | Comment lancer votre app (par exemple `npm run dev`, `flask run`, `cargo run`) | Résolue depuis le projet, affichée « Auto : ... » dans le champ |
| URL de test | L'adresse HTTP locale où votre app répond une fois démarrée. Le moteur lui envoie des requêtes pour mesurer la latence et tester la robustesse | Observée : le moteur démarre l'app une fois dans un shadow et regarde quel port elle ouvre |
| Commande de test | Comment lancer votre suite de tests (par exemple `pytest`, `npm test`) | La commande détectée est utilisée ; sans commande, le gate de correction saute l'étape des tests projet |

Une valeur saisie gagne toujours sur la détection. L'URL doit être locale : le moteur refuse de bencher ou d'injecter des pannes ailleurs que sur localhost.

### Comment la commande de démarrage est résolue

Le moteur construit une liste de candidats, du plus crédible au moins crédible, et les essaie jusqu'à ce que l'un serve du trafic :

1. La ligne `web:` d'un `Procfile`, la déclaration la plus explicite qu'un projet puisse faire.
2. Chaque unité qui sait tourner, de la plus externe à la plus interne, via son script `start` puis son script `dev`. Une commande appartenant à un sous-dossier y est exécutée (`cd web && npm run dev`).
3. Une cible `Makefile` nommée `run`, `start`, `serve`, `dev` ou `up`.

Un candidat qui meurt aussitôt (sous-projet jamais installé, binaire absent) passe la main au suivant : un monorepo dont l'app la plus externe est inutilisable est quand même prouvé sur le service qui, lui, tourne. Quand vous passez `--start` vous-même, cette commande est la seule essayée. Si tous les candidats échouent, l'erreur les liste chacun avec sa raison et la fin du journal de la dernière tentative.

La détection par unité couvre Node (scripts `package.json`, puis `server.js` / `index.js` / `app.js`), Python (Django `manage.py`, points d'entrée FastAPI et Flask, avec le virtualenv du projet quand il existe), Rust, Go, Spring Boot, PHP (Laravel, Symfony, serveur intégré), Ruby (Rails, Rack) et .NET.

### Comment l'URL de test est trouvée

Aucune analyse statique ne peut savoir avec certitude quel port une app ouvre : le moteur mesure au lieu de deviner. Au premier lancement il démarre votre app dans un shadow jetable, regarde quels ports TCP le groupe de processus ouvre (le noyau dit quelles sockets lui appartiennent, un serveur voisin n'est donc jamais confondu avec le vôtre), préfère un port que le projet désignait, puis un port qui répond en HTTP, et arrête l'app. La réponse est mise en cache dans `.navin/evolve/probe-url.json` pour cette commande de démarrage exacte : les lancements suivants n'ont plus de démarrage supplémentaire. Supprimez ce fichier pour forcer une nouvelle découverte.

## Exemples de commandes

Ce que le moteur écrit pour vous, et ce qu'il faut saisir quand votre projet démarre d'une façon inhabituelle. Adaptez le port à votre app.

| Stack | Commande de démarrage | Commande de test | URL de test |
|---|---|---|---|
| Flask | `flask --app app run --port 5000` | `pytest` | `http://127.0.0.1:5000/` |
| Django | `python manage.py runserver 8000` | `python manage.py test` | `http://127.0.0.1:8000/` |
| FastAPI | `uvicorn main:app --port 8000` | `pytest` | `http://127.0.0.1:8000/` |
| Express / Node | `npm start` ou `node server.js` | `npm test` | `http://127.0.0.1:3000/` |
| Next.js | `npm run dev` | `npm test` | `http://127.0.0.1:3000/` |
| Vite (React, Vue...) | `npm run dev` | `npx vitest run` | `http://127.0.0.1:5173/` |
| Rust | `cargo run` | `cargo test` | le port que votre app ouvre |
| Go | `go run .` | `go test ./...` | le port que votre app ouvre |
| Spring Boot | `./mvnw spring-boot:run` | `./mvnw test` | `http://127.0.0.1:8080/` |
| Script maison | `sh run.sh` | `sh test.sh` | ce que le script sert |

Conseils :

- Les commandes s'exécutent dans le worktree shadow, qui ne contient que les fichiers commités. Un virtualenv à la racine du projet est utilisé via son chemin absolu ; les autres interpréteurs hors du dépôt en demandent un aussi (par exemple `/home/moi/venvs/app/bin/python -m pytest`).
- Les dépendances installées sont prêtées au shadow plutôt que réinstallées : chaque `node_modules`, virtualenv (`.venv`, `venv`, `env`), `vendor` Composer et `vendor/bundle` déjà présent dans votre espace de travail est lié symboliquement au bon endroit dans le shadow. Le shadow démarre donc en quelques secondes, et un paquet que votre espace de travail n'a jamais installé y manque toujours : installez-le une fois chez vous.
- C'est votre app qui doit ouvrir le port ; le moteur le guette mais ne peut pas l'ouvrir à sa place.
- Un script wrapper commité dans le dépôt (`run.sh`, `test.sh`) est l'option la plus fiable pour les démarrages en plusieurs étapes (migrations, seeds, variables d'environnement).

## Les opérations

- **Prove** : démarre votre app en isolation, mesure une baseline (latence, débit), puis injecte des pannes réelles et rapporte celles auxquelles l'app survit. Produit un score de robustesse (0-100) et un verdict (`pass`, `weak`, `fail`) sauvegardés sous `.navin/proofs/`.
- **Optimize (ASSE)** : demande au LLM choisi plusieurs variantes de code visant un objectif (`p95` de latence ou `throughput`), benche chacune sous la charge identique, et ne promeut que le gagnant mesuré qui franchit tous les gates ci-dessous. Rapport sous `.navin/optimize/`.
- **Evolve** : pipeline complet - prouver, diagnostiquer les points faibles depuis les logs, générer des corrections avec le LLM, vérifier chacune, et enregistrer une promotion pour chaque correction acceptée. Rapport sous `.navin/evolve-runs/`.

**Modèle** : la liste déroulante affiche les presets de modèles texte de votre config Navin, tous providers confondus. Il ne sert que pour Optimize et Evolve (Prove n'appelle jamais de LLM).

## Catalogue des pannes par profil

| Profil | Pannes injectées | Fenêtre de charge | Concurrence |
|---|---|---|---|
| `quick` | charge, kill + récupération | 5 s | 16 |
| `standard` | charge, requêtes malformées, flood de connexions (200 sockets), chaos réseau (latence, pertes, resets via proxy TCP), kill + récupération | 12 s | 48 |
| `deep` | comme standard, plus dur (flood 512 sockets) | 30 s | 128 |

Critères de réussite appliqués à chaque panne : taux d'erreur d'au plus 1 %, aucun crash, et récupération en moins de 15 secondes après un kill. La mémoire est bornée par le `max_memory_mb` de la politique (rlimit sur le processus enfant).

## Comment un candidat est jugé

Une variante ou une correction générée n'est promue que si **tout** ce qui suit est vrai, mesuré dans son propre shadow :

1. **Tests projet** : une suite verte doit rester verte (une suite déjà rouge avant le patch est une condition préexistante, pas un reproche fait au candidat).
2. **Invariants métier** : chaque invariant déclaré doit sortir en 0, avec la même règle de condition préexistante.
3. **Équivalence comportementale** : le verifier différentiel rejoue les mêmes vecteurs de requêtes contre la baseline et le candidat ; les réponses doivent correspondre (statut et hash du corps) sur chaque vecteur.
4. **Taux d'erreur** : au plus 1 point de pourcentage de plus que le benchmark baseline.
5. **Gain mesuré** : au moins `min_gain` pourcent sur l'objectif **et** statistiquement significatif (voir plus bas). Pour les corrections : le finding ciblé est résolu, le score de robustesse n'a pas baissé, aucun nouveau finding critical/high n'est apparu, le P95 n'a pas régressé au-delà de la tolérance.
6. **Preuve finale** : le gagnant passe une preuve quick fraîche avec un score au moins égal à celui de la baseline.

Chaque rejet est enregistré avec sa raison exacte dans le rapport et affiché dans le tableau de bord.

## Confiance statistique

Un benchmark isolé ne prouve rien : le même code varie de plusieurs pourcents d'un run à l'autre. Chaque mesure d'Optimize répète donc le benchmark sur plusieurs fenêtres (3 par défaut, `--repeats` en CLI, `repeats` dans les params du job) après un court warmup, et rapporte moyenne ± écart-type pour le P95 et le RPS.

Une variante ne gagne que si son gain dépasse le bruit combiné des deux distributions (critère de Welch à deux échantillons, confiance d'environ 95 %). Un "+3 %" noyé dans le bruit est marqué "dans le bruit de mesure" et n'est jamais promu : le moteur croit les mesures, pas la chance.

## Invariants métier

Les tests prouvent le code ; les invariants prouvent le domaine. Déclarez dans `.navin/evolve.toml` des commandes qui doivent sortir en 0 pour qu'un candidat soit promouvable - des totaux de commandes cohérents, aucun paiement dupliqué, intégrité référentielle :

```toml
[[invariants]]
name = "order_total_consistency"
command = "python verify_orders.py"

[[invariants]]
name = "no_duplicate_payments"
command = "python verify_payments.py"
timeout_secs = 60
```

Ils s'exécutent dans chaque shadow (baseline, chaque variante, chaque candidat de fix), après la suite de tests, avec le shadow comme répertoire courant. Les scripts référencés par chemin relatif doivent donc être **commités** dans le dépôt. Un candidat qui casse un invariant précédemment vert est rejeté, même s'il est plus rapide.

## Verifier différentiel

Avant le benchmark, le moteur explore l'app baseline pour collecter jusqu'à 24 vecteurs GET (`--diff-vectors`), prend l'empreinte de chaque réponse (code de statut + hash du corps), puis rejoue exactement les mêmes vecteurs contre chaque candidat. Toute divergence - un statut qui change, un corps différent, une route disparue - disqualifie le candidat, avec la liste précise des vecteurs divergents dans la note. Mettez `--diff-vectors 0` pour désactiver le contrôle.

## Auto-run après commit

Activez l'interrupteur « Auto après commit » dans la carte de lancement et le daemon surveille votre HEAD Git : chaque nouveau commit relance automatiquement la dernière opération lancée (même type, mêmes paramètres) en arrière-plan, pendant que vous continuez à travailler.

- Le watcher vérifie le HEAD toutes les 5 secondes en lisant deux fichiers sous `.git/` ; aucun processus n'est lancé et le coût est négligeable.
- Si un job est déjà en file ou en cours, le commit est ignoré (pas d'empilement).
- L'opération rejouée est stockée dans `.navin/evolve/autorun.json` ; lancer une nouvelle opération depuis le tableau de bord la met à jour.
- Si vous activez l'interrupteur sans avoir jamais lancé d'opération, une preuve de robustesse rapide construite depuis les commandes détectées est utilisée.

## Promotions et certificats

Chaque changement accepté produit une branche Git (`navin/evolve/...`), un enregistrement de promotion et un certificat signé sous `.navin/promotions/`. Le certificat atteste le finding, le candidat, le commit de référence, les scores avant/après et le verdict ; il porte un checksum d'intégrité de son contenu et une **signature Ed25519** de la clé du moteur (`.navin/evolve/identity.ed25519`, générée au premier usage, permissions restreintes au propriétaire). Modifier n'importe quel champ invalide la signature.

- **Vérifier** : recontrôle le checksum et la signature (`authentic: true`).
- **Fusionner** : fusion fast-forward de la branche de promotion dans votre branche courante. Refusée si le certificat ne se vérifie pas, si l'arbre de travail n'est pas propre, ou si la fusion n'est pas un fast-forward.
- **Annuler** : révoque une promotion fusionnée (ou supprime la branche non fusionnée) ; l'horodatage du rollback est enregistré.

En mode `safe` (le défaut), rien n'est jamais fusionné automatiquement : le moteur prépare seulement des branches que vous relisez.

## Référence de la politique : `.navin/evolve.toml`

Tout est optionnel ; un fichier absent signifie ces valeurs par défaut. Un fichier cassé est une erreur, jamais un défaut permissif silencieux.

```toml
[proof]
enabled = true
profile = "standard"        # quick | standard | deep

[evolve]
enabled = false             # opt-in par projet
mode = "safe"               # safe | trusted | autonomous

[evolve.allowed]            # familles de remédiation autorisées pour le fix engine
performance = true
memory = true
database = true
reliability = true
concurrency = false
security = false            # off : change le comportement visible de l'extérieur
dependencies = false

[evolve.promotion]
auto_merge = false          # le mode safe ne fusionne jamais tout seul

[evolve.resources]          # plafonds pour les processus enfants dans les shadows
max_cpu_percent = 15
max_memory_mb = 512
max_disk_mb = 4096
max_runtime_minutes = 30

[evolve.budget]
max_candidates = 100
max_runtime_minutes = 30
max_llm_cost_usd = 2.0

[evolve.generator]          # bridge LLM externe (le moteur n'appelle jamais un LLM lui-même)
command = "python3 -m navin.evolve.bridge"
timeout_secs = 120

[[invariants]]              # optionnel, répétable
name = "orders_consistent"
command = "python verify_orders.py"
timeout_secs = 120
```

## Référence CLI

| Commande | Ce qu'elle fait |
|---|---|
| `navin-engine inspect <path>` | Découvre comment le projet se construit, se teste et se lance |
| `navin-engine daemon <path>` | Lance le daemon pour un projet |
| `navin-engine status <path>` | File de jobs et état du daemon |
| `navin-engine policy <path>` | Affiche la politique effective (défauts fusionnés avec evolve.toml) |
| `navin-engine baseline <path> --url ...` | Temps de build, démarrage, latence P50/P95/P99, CPU, RSS |
| `navin-engine proof <path> --url ... --profile quick` | Injection de pannes + score de robustesse |
| `navin-engine diagnose <path> ...` | Findings de causes racines depuis une preuve et ses logs |
| `navin-engine fix <path> --finding ... --candidates c.json` | Vérifie des patchs candidats en shadow, propose le meilleur |
| `navin-engine optimize <path> --url ... --repeats 3` | ASSE : benche des variantes, promeut le gagnant mesuré |
| `navin-engine evolve <path> --url ...` | Pipeline complet : prove, diagnose, fix, promote |
| `navin-engine promotions <path>` | Liste les promotions enregistrées |
| `navin-engine verify-cert <path> --id ...` | Vérifie un certificat (intégrité + signature) |
| `navin-engine merge <path> --id ...` / `rollback` | Applique ou révoque une promotion sous politique |
| `navin-engine shadow list\|sweep <path>` | Inspecte ou nettoie les shadows restants |
| `navin-engine bench-loadgen` | Capacité honnête du générateur de charge intégré |

## Artefacts sur disque

| Chemin | Contenu |
|---|---|
| `.navin/evolve/endpoint.json` | Port loopback et jeton d'accès du daemon (0600 sous Unix) |
| `.navin/evolve/daemon.log`, `daemon.pid` | Sortie et PID du daemon |
| `.navin/evolve/autorun.json` | Opération rejouée par l'auto-run après commit |
| `.navin/shadow/<run-id>/` | Worktrees isolés utilisés par les runs |
| `.navin/proofs/`, `.navin/diagnoses/` | Rapports de preuve et findings |
| `.navin/optimize/`, `.navin/evolve-runs/`, `.navin/fixes/` | Rapports de campagne et propositions de fix |
| `.navin/promotions/` | Enregistrements de promotion et certificats signés |
| `.navin/evolve.toml` | Politique optionnelle (voir la référence ci-dessus) |

## Bon à savoir

- **Les shadows sont construits depuis le HEAD Git.** Les changements non commités ne font pas partie de l'expérience : commitez d'abord, lancez ensuite.
- **Les benchmarks sont limités à localhost** par conception ; le moteur refuse toute URL non locale.
- L'app testée tourne avec une **rlimit mémoire** et une durée bornée : un candidat qui s'emballe tue son propre shadow, jamais votre machine.
- Le port choisi doit être libre : le moteur démarre lui-même votre app dans le shadow.
- Les rapports sont du JSON brut ; tout ce que montre le tableau de bord peut être lu, diffé et archivé depuis `.navin/`.
