# Performance : mesure et baselines du chemin chaud agent

Ce document définit comment mesurer la latence de l'agent hors LLM, et fixe
les baselines de référence avant optimisation. Sans ces chiffres, aucun gain
annoncé n'est vérifiable.

## Instrumentation par phase (`phase_timings_ms`)

Chaque tour de l'agent traverse une machine à états
(`RESTORE -> COMPACT -> COMMAND -> BUILD -> RUN -> SAVE -> RESPOND`,
voir `navin/agent/loop.py`). La durée de chaque phase est mesurée par le
driver et publiée en fin de tour :

- L'événement WebSocket `turn_end` porte `phase_timings_ms`
  (ex. `{"restore": 12, "build": 85, "run": 4200, "save": 30}`) en plus de
  `latency_ms`. Le transcript persiste ce body, ce qui donne un log structuré
  interrogeable a posteriori.
- Le bus runtime (`TurnCompleted.phase_timings_ms`) expose la même donnée
  aux adaptateurs in-process.

Lecture rapide :

- `build` élevé = consolidation / construction du contexte (voir A3).
- `run` = LLM + outils ; à croiser avec les `tool_events`.
- `restore` élevé = restauration de checkpoint ou session volumineuse.

## Benchmarks reproductibles

Le script `scripts/bench_agent_hotpath.py` mesure les primitives qui dominent
la latence agent hors LLM, sur une fixture synthétique ou un vrai repo :

```bash
# Petite fixture (~200 fichiers)
.venv/bin/python scripts/bench_agent_hotpath.py

# Fixture moyenne (~5000 fichiers) + export JSON
.venv/bin/python scripts/bench_agent_hotpath.py --files 5000 --repeat 5 --json baseline.json

# Sur un vrai repo
.venv/bin/python scripts/bench_agent_hotpath.py --root /chemin/vers/repo
```

Scénarios :

| Scénario | Ce qui est mesuré | Optimisation liée |
|----------|-------------------|-------------------|
| `index_cold` | Premier `CodeIndex.ensure()` cache vide (coût du premier outil index) | A1 warmer |
| `index_warm` | `ensure()` index déjà chargé (revalidate = stat-walk) | A1 throttle |
| `index_lookup` | `index.search()` warm | A1 |
| `estimate_long_history` | Estimation tiktoken d'un prompt long (coût consolidation par BUILD) | A3 lazy |
| `governance_20_iterations` | `prepare_for_model` sur 20 itérations d'un run | A4 cache |

## Baselines (avant optimisation)

Mesurées le 31 juillet 2026, WSL2, `.venv` CPython 3.12.

### Fixture ~200 fichiers (`--files 200 --repeat 3`)

| Scénario | p50 ms | p95 ms |
|----------|--------|--------|
| `index_cold` | 143 | 1213 |
| `index_warm` | 8.9 | 11.0 |
| `index_lookup` | 18.3 | 20.9 |
| `estimate_long_history` | 18.6 | 18.7 |
| `governance_20_iterations` | 605 | 605 |

### Fixture ~5000 fichiers (`--files 5000 --repeat 5`)

| Scénario | p50 ms | p95 ms |
|----------|--------|--------|
| `index_cold` | 3289 | 3404 |
| `index_warm` | 201 | 211 |
| `index_lookup` | 336 | 458 |
| `estimate_long_history` | 18.9 | 20.4 |
| `governance_20_iterations` | 449 | 456 |

Lectures :

- Le cold start index (3.3 s sur 5k fichiers) est payé par le premier outil
  `code_index`/`grep` du tour : c'est la cible du warmer (A1).
- Même warm, `ensure()` coûte ~200 ms par appel d'outil sur 5k fichiers
  (stat-walk complet) : d'où le throttle de revalidation (A1).
- L'estimation tiktoken (~19 ms) est payée à chaque BUILD par la
  consolidation, même quand la session est loin du budget : d'où le
  fast-path heuristique (A3).
- `prepare_for_model` paie 2 estimations tiktoken complètes par itération
  du run : ~22-30 ms x 2 x N itérations (A4).

## Résultats après optimisations (A1-A6, 31 juillet 2026)

Mêmes conditions que la baseline, fixture 5000 fichiers :

| Scénario | Avant p50 | Après p50 | Gain |
|----------|-----------|-----------|------|
| `index_warm` | 201 ms | ~0 ms | throttle TTL 2 s + `mark_dirty` sur édits agent |
| `index_lookup` | 336 ms | ~12 ms | index casefold + early exit (x28) |
| `governance_20_iterations` | 449 ms | ~210-250 ms | memo d'estimation dans `prepare_for_model` (x2+) |
| `index_cold` | 3289 ms | inchangé, mais hors tour | warmer WebUI + CLI à l'ouverture |

Optimisations livrées :

- A1 (`navin/index/warmer.py`) : l'index se construit en tâche de fond dès
  que le WebUI ouvre/rattache un chat ou change le scope workspace, et dès
  le démarrage CLI (`agent` / `serve`) ; les écritures agent (via
  `record_file_before`) marquent l'index dirty pour que le throttle ne
  serve jamais un snapshot périmé après un édit. `search()` utilise
  `_by_name_lower` avec early exit dès que les meilleurs ranks remplissent
  le `limit`.
- UI : le footer de latence sous chaque réponse assistant expose un
  collapse « phases » alimenté par `phase_timings_ms` (live + replay
  transcript).
- A2 (`code_index` action=semantic) : le sync d'embeddings tourne en tâche
  de fond dédupliquée par racine ; la recherche répond sur les vecteurs déjà
  présents au lieu de bloquer le tour pendant l'embedding.
- A3 (`navin/agent/memory.py`) : la consolidation ne paie l'estimation
  tiktoken complète que quand la projection char-based (conservatrice, 3
  chars/token) dépasse 70 % du budget ; sinon le BUILD est instantané.
- A4 (`context_governance.py`, `runner.py`) : memo d'estimation partagé
  entre compact et snip dans un même `prepare_for_model` ; deepcopy des
  contextes de hooks remplacés par des copies superficielles quand le hook
  est un no-op.
- A6 (`runtime_context.py`) : les providers de contexte runtime se
  résolvent en parallèle (ordre stable), BUILD paie le max au lieu de la
  somme de leurs latences.
- A5 (`schema.py`, `runner.py`) : `fail_on_tool_error` passe à `False` par
  défaut (une erreur d'outil revient au modèle qui se corrige) avec un
  garde-fou : le même appel identique qui échoue 2 fois reçoit un hint
  d'escalade "ne répète pas cet appel". Les frontières de sécurité
  (SSRF, workspace) gardent leur traitement non contournable.
- B1 : `max_concurrent_subagents` / injections parent passent à **200** ;
  les checkouts `isolate=true` sont poolés (reset + reuse) jusqu'à cette
  taille au lieu d'un `git worktree add` froid à chaque spawn.
- Cache web : `web_search` / `web_fetch` mémorisent les succès 120 s
  (process-local, max 256 entrées) ; les erreurs ne sont jamais stockées.
- Tree-sitter : primaire dans `navin-core` pour JS/TS/Go/Rust/Java/
  C/C++/C#/Ruby/PHP/Kotlin/**Swift** ; `languages.json` reste le
  fallback (vue/sql). Pas de rewrite - la suite sûre est d'ajouter une
  grammar à la fois.
- Provider : `NAVIN_MAX_CONCURRENT_REQUESTS` défaut **200** (aligné sur
  `max_concurrent_subagents`). À cette largeur, la limite atteinte en
  premier est le débit du fournisseur de modèle, pas la machine.

## Gouverneur de ressources

Le plafond de 200 est un maximum, pas une cible : `navin/agent/resources.py`
mesure la machine et abaisse la limite quand elle ne peut pas la tenir. Sans
cela, le même nombre s'appliquerait à un portable, à un serveur 64 cœurs et à
un conteneur limité à un cœur et un gigaoctet.

- Mesure (stdlib uniquement, comme `runtime_health` : pas de psutil) :
  `os.cpu_count()`, `os.sched_getaffinity()`, `MemAvailable` de
  `/proc/meminfo`, croisés avec les limites cgroup v1 et v2. Le croisement
  est indispensable en conteneur, où `/proc` rapporte l'hôte et non
  l'allocation réelle du processus.
- Calcul : `agents_per_core` (32 par défaut, volontairement élevé - un agent
  qui attend l'API du modèle n'occupe pas un cœur) et `memory_per_agent_mb`
  (96 Mo, volontairement surestimé - se tromper vers le bas signifie swapper).
  Seuls **70 %** de la RAM mesurée sont alloués aux agents (`max_utilisation`),
  le reste revient à la gateway, au webui et aux serveurs de langage.
- Garde-fous : le gouverneur ne peut qu'abaisser une limite déjà accordée par
  le plan et la config, jamais l'élever ; une mesure impossible rend le
  plafond intact, soit le comportement d'avant ; le plancher est de 1 agent,
  parce que 0 bloquerait un tour en attente d'un sous-agent.
- Les trois plateformes sont mesurées, pas seulement Linux : `MemAvailable`
  sous Linux et WSL, `GlobalMemoryStatusEx` via ctypes sous Windows, et sous
  macOS la RAM totale via `sysconf` décotée de moitié (faute d'équivalent bon
  marché de `MemAvailable`, l'alternative honnête étant un sous-processus
  `vm_stat`). Ne mesurer que Linux aurait laissé Windows et macOS dimensionnés
  sur leur seul nombre de cœurs, qui ne dit rien du terme qui s'épuise.
- Ordres de grandeur obtenus : 192 agents sur un poste Linux 6 cœurs / 38 Go
  libres, 179 sur un Windows 16 cœurs / 24 Go, 200 sur un Mac 64 Go ou un
  serveur, 59 sur une machine à 8 Go libres, 7 dans un conteneur 1 cœur / 1 Go.
- Réglage : bloc `resources` de la config (`enabled`, `maxUtilisation`,
  `agentsPerCore`, `memoryPerAgentMb`). `enabled: false` restitue la limite
  configurée brute. `memoryPerAgentMb` (96 Mo) est une surestimation
  volontaire : c'est le paramètre à baisser pour obtenir plus d'agents sur une
  machine à RAM modeste, au risque de swapper si l'estimation devient fausse.
- Re-mesure : portée par le thread de sync licence (toutes les 10 min), qui
  applique déjà les limites au loop vivant et réveille les files d'attente
  quand la limite remonte.

## File d'attente des sous-agents

Au-delà de la limite, un `spawn` est **mis en file, pas refusé**. Le refus
faisait d'une conversation chargée une impasse : le modèle recevait un non,
n'avait rien à réessayer, et le travail n'avait simplement jamais lieu.

- Autorité unique : `SubagentManager.spawn`. Les deux copies du test de limite
  (`tools/spawn.py` et `handle_multitask_spawn`) ont été retirées.
- Ce qui est capturé à la mise en file : runtime, `workspace_scope`, métadonnées
  parent et `TurnPolicy` du tour appelant. Sans cela, un agent démarré plus tard
  tournerait avec des règles plus faibles que celui qui l'a demandé, les
  contextvars du tour ayant disparu.
- Dépilement : depuis le `done_callback` de chaque tâche, seul endroit où un
  slot est rendu (fin normale, erreur ou annulation). La limite y est relue,
  car une sync licence ou le gouverneur ont pu l'abaisser entre-temps.
- Visibilité : les agents en file comptent comme en vol dans
  `get_running_count_by_session` (le tour reste vivant tant qu'il reste du
  travail accepté) et apparaissent dans `running_snapshot` en phase `queued`.
  `cancel_by_session` vide la file avant d'annuler, pour qu'un slot libéré ne
  démarre pas le travail qu'on est en train d'arrêter.
- Contre-pression conservée : le refus était le seul frein, la file l'aurait
  supprimé. `MAX_QUEUED_SUBAGENTS` (1000 par conversation) est le nouveau mur,
  placé très au-delà de tout fan-out délibéré : une vague de quelques centaines
  passe, un modèle qui boucle sur `spawn` le rencontre bien avant de faire
  gonfler le processus.
- Récolte : `_MAX_OUTCOME_HISTORY` passe de 50 à **400**, car c'est le filet de
  sécurité des résultats que le tour parent n'a pas pu porter en injections.
  Une rétention plus étroite que la largeur de concurrence perdait
  silencieusement le verdict de sous-agents qui avaient réellement tourné.
- Vérifié par un test de charge (`HundredsOfAgentsTest`) : une vague de 300
  sous-agents à 50 slots part entièrement, aucun n'est perdu par la file, les
  300 annonces atteignent le parent, les 300 verdicts restent lisibles ensuite,
  et la concurrence observée ne dépasse jamais la limite.
- Isolation `git worktree add` : au plus **4** créations à la fois, timeout
  porté à 180 s. Sans ça, une vague `isolate=true` sur un gros dépôt se
  battait pour le verrou git, dépassait 60 s, retombait dans l'arbre partagé
  et les agents s'écrasaient les uns les autres.
- Pool bloquant : plafond **256** threads, dimensionné pour une vague d'agents
  (plus 32 de marge) et plus seulement `16 × cœurs`. Un plafond à 128 mettait
  la seconde moitié d'une vague de 100 derrière la première.
- Annulation : un `/stop` ou un cancel de session **annonce** toujours le
  résultat au parent. Avant, le tour attendait 300 s un verdict qui ne
  viendrait jamais.
- Heartbeat 60 s : une carte encore vivante mais silencieuse (compile longue,
  file de worktree) est republicée, pour que l'IDE ne lise pas ça comme un
  agent ni fini ni arrêté. Rien n'est tué pour silence : une compile de 20 min
  est du travail, pas un deadlock.

## Critères de sortie (validés)

- A1 : warm `ensure()` < 100 ms -> ~0 ms mesuré (gate x10 largement passé :
  201 ms -> <1 ms sur lookup warm).
- A3 : plus d'estimation tiktoken sur BUILD d'une session clairement sous
  budget (couvert par `tests/test_compaction_handoff.py::LazyEstimateTest`).
- A4 : `governance_20_iterations` >= x2 -> x2.1 mesuré.
- A5 : un outil qui échoue ne stoppe plus le run ; répétition verbatim
  bornée (couvert par `tests/test_soft_tool_errors.py`).

Refaire tourner le bench avec `--json` après chaque optimisation et comparer
au JSON de baseline.

## Budget de tokens en entrée

La latence hors LLM n'est qu'une moitié du coût d'un tour. L'autre est ce que
chaque appel envoie au modèle, et un total ne suffit pas à décider : une
requête de 62k n'appelle pas le même correctif selon qu'elle est faite de
schémas d'outils ou de fichiers rejoués.

### Profiler par section (`NAVIN_PROMPT_PROFILE`)

`navin/agent/prompt_profile.py` découpe chaque requête sortante en buckets et
les journalise avant l'appel provider :

```bash
NAVIN_PROMPT_PROFILE=1 navin serve
```

Il lit la requête finie plutôt que d'être câblé dans les constructeurs de
contexte. Tous les chemins qui atteignent un provider sont donc couverts,
boucle principale, subagents et tours éphémères compris, et les totaux
restent exacts même quand un en-tête de section n'est pas reconnu (il tombe
dans `system.other` au lieu de disparaître).

Il est éteint par défaut : le découpage réencode le prompt, ce qui est du
travail perdu sur un tour que personne ne mesure.

### Baseline (15 août 2026, ce dépôt, catalogue d'outils par défaut)

| Groupe | Mesuré | Budget | Écart |
|--------|--------|--------|-------|
| `core_system` | 10 017 | 7 000 | 1,4x |
| `skills` | 1 255 | 3 000 | ok |
| `tools` | 19 193 | 7 000 | 2,7x |
| `history` | variable | 20 000 | - |
| `tool_results` | variable | 20 000 | - |
| **charge fixe** | **30 527** | - | - |

Détail de la charge fixe : `tool_contract.md` 6 400, bootstrap 1 734,
identity 1 414, index des skills 776, skills actifs 479, règles projet 164,
mémoire 149, plus 19 193 de schémas pour 45 outils.

Lectures :

- La divulgation progressive des skills fonctionne : `skills` tient largement
  sous son plafond parce que seuls les noms sont injectés, les descriptions
  étant servies à la demande par l'outil `skill`. Ce groupe ne demande aucun
  chantier.
- Les schémas d'outils sont le premier poste, à 2,7x le budget. Les plus gros
  sont `board` (1 294), `git` (1 110), `montage` (1 063), `browser` (1 055),
  `mobile` (824) et `scrape` (802).
- **La charge fixe (30 527) n'est atteignable par aucun rognage d'historique**,
  puisqu'elle est payée sur une session vide. Seule une réduction de la surface
  d'outils la fait bouger.

### Deux natures de plafond

`core_system`, `skills` et `tools` sont **structurels** : identiques sur chaque
requête d'une session. Ce sont des cibles d'ingénierie, et leur valeur est
fixée sous la mesure pour que le budget demande effectivement quelque chose.
`tools` à 7 000 correspond à ce que le chantier des paquets d'outils permet
d'atteindre ; viser 3 000 supposerait de descendre à environ sept outils.

`history` et `tool_results` **croissent avec la session**, et leur vraie limite
est la fenêtre de contexte du modèle, que `ContextGovernor` fait déjà
respecter. Leurs valeurs marquent une session assez lourde pour mériter un
coup d'oeil, pas une cible : les fixer à l'échelle structurelle ferait sonner
l'alerte sur du travail parfaitement normal.

`request_max` vaut 200 000 et sert uniquement à détecter un emballement, une
session qui a grossi sans que le gouverneur l'attrape ou un résultat d'outil
énorme. La requête moyenne mesurée fait 62 126 tokens.

Un plafond qui sonne à chaque appel ne dit plus rien. Pour la même raison, un
dépassement structurel n'est journalisé **qu'une fois par processus** : il est
identique sur les 96 requêtes d'une journée, donc le premier avertissement dit
déjà tout. `reset_budget_warnings()` remet le compteur à zéro.

Sur ce dépôt, le profiler émet donc exactement deux avertissements au
démarrage, `tools=19193/7000 (274%)` et `core_system=10017/7000 (143%)`, puis
se tait.

Sur une journée réelle (15 août 2026, `google/gemini-3.7-flash`) : 96 requêtes,
5 964 106 tokens en entrée dont 5 354 842 servis par le cache (89,8 %), et
12 247 tokens en sortie. Soit **128 tokens de sortie par requête**, c'est-à-dire
un seul appel d'outil par aller-retour.

### Le budget rapporte, il ne bloque pas

`DEFAULT_BUDGET` déclare un plafond par groupe et le profiler journalise un
`WARNING` dès qu'un groupe le dépasse. Il ne refuse jamais d'envoyer, pour
deux raisons.

D'abord, refuser transforme un problème de coût en tâche échouée, et la
section qui déborde est en général celle qui porte le travail.

Ensuite, et c'est le point structurant : **toute optimisation qui réécrit
l'historique casse le cache.** Le cache provider fonctionne par préfixe
identique. Recompresser ou re-résumer le contexte à chaque appel produit un
préfixe différent à chaque appel, donc zéro réutilisation. Sur
`gemini-3.7-flash` le tarif non caché est 4x le tarif caché (0,375 $/M contre
0,09375 $/M), si bien qu'une requête de 20k jamais cachée coûte le même prix
qu'une requête de 62k cachée à 90 %. Une réduction de volume payée par une
perte de cache est nulle au mieux.

Les seules compressions qui gagnent vraiment sont donc celles qui sont
**stables une fois écrites** : réduire la surface d'outils, dégraisser le
prompt système, ou compresser un résultat d'outil au moment où il entre dans
la transcription, jamais rétroactivement.

C'est aussi pourquoi `ContextGovernor.clear_stale_turn_tool_results` ne se
déclenche que sous pression : blanchir un vieux résultat casse le cache lui
aussi, mais quand la fenêtre déborde, tenir dedans prime sur le prix.

### Correctifs livrés (15 août 2026)

- `tool_contract.md` demande explicitement de grouper les appels d'outils
  indépendants dans une même réponse. Le runtime savait déjà les exécuter en
  parallèle (`concurrent_tools`, `_partition_tool_batches`), rien ne le
  demandait au modèle. Coût : 165 tokens de consigne.
- Les subagents reçoivent `concurrent_tools=True`. Le drapeau vaut `False` par
  défaut et seule `AgentLoop` le positionnait, donc un subagent exécutait ses
  lectures strictement en série alors que l'exploration est l'essentiel de son
  travail.
- `parallel_tool_calls` n'est **pas** envoyé aux providers : les appels
  parallèles sont actifs par défaut chez OpenAI comme chez Anthropic, et
  aucun code Navin ne les désactivait. Envoyer le paramètre n'aurait ajouté
  qu'un risque de 400 sur les endpoints qui ne le connaissent pas.

### Critère de sortie

Le rapport **tokens de sortie / nombre de requêtes**. Un appel d'outil pèse
environ 130 tokens une fois sérialisé, donc une exécution qui ne groupe rien
se pose sur ce plancher. S'il monte vers 250-350, le modèle groupe ses appels
et le nombre d'allers-retours baisse mécaniquement. S'il reste sous 160 sur
une semaine, la consigne ne suffit pas et il faut la remonter plus haut dans
le prompt système.

La consigne de groupage s'adresse à un modèle : rien dans le code ne peut la
rendre mécanique. Ce que le code peut faire, c'est refuser qu'une régression
passe inaperçue, et c'est le rôle de la mesure ci-dessous.

### Lire la mesure : `navin doctor`

Le compteur d'usage enregistre déjà `requests` et `completion_tokens` par jour
et par modèle. La section **Token efficiency** de `navin doctor` en tire le
rapport sur les sept derniers jours, sans instrumentation supplémentaire ni
coût à l'exécution :

```
Token efficiency
  OK   tool call batching     225 output tokens per request over 383 requests - batching some calls
```

Trois régimes : sous 160 le rapport est signalé comme un aller-retour par
appel, entre 160 et 250 le modèle groupe une partie de ses appels, au-delà de
250 il groupe correctement. Le constat n'est jamais bloquant (`required=False`)
puisqu'il décrit un coût, pas une installation cassée.

**La journée isolée ne veut rien dire.** Relevé du 4 au 15 août 2026, le
rapport quotidien va de 60 à 356 selon la nature du travail : les journées de
rédaction montent, les journées d'outillage descendent. C'est pourquoi la
fenêtre est de sept jours et pondérée par le nombre de requêtes et non par le
nombre de jours, une journée courte et bavarde ne pouvant pas renverser le
verdict. Sur cette même période l'agrégat se tient autour de 225.

C'est un indicateur de tendance, pas une preuve. Il sert à détecter deux
dérives : le groupage qui ne prend pas (le rapport reste bas), et le
sur-groupage où le modèle lirait des fichiers par anticipation (le rapport
monte **et** le total de tokens monte avec lui). La seconde dérive se lit en
croisant cette section avec le total mensuel de l'onglet Compte.
