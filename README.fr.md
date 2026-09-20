<div align="center">

<img src="./assets/readme/hero.svg" alt="Navin : votre IA, votre machine. Développer, rechercher et automatiser." width="100%">

# Un objectif. Un agent qui fait avancer le travail.

**Un espace de travail IA open source pour coder, rechercher, utiliser des outils et retrouver le contexte d'une session à l'autre.**

Dans votre terminal, votre navigateur ou l'application desktop. Choisissez vos modèles. Gardez le contrôle.

[English](./README.md) · [Français](./README.fr.md) · [العربية](./README.ar.md) · [Español](./README.es.md) · [Português](./README.pt-BR.md) · [Deutsch](./README.de.md) · [简体中文](./README.zh-CN.md)

[![Licence AGPL-3.0](https://img.shields.io/badge/licence-AGPL--3.0-66d9b0?style=flat-square)](./LICENSE) [![Exécution locale](https://img.shields.io/badge/local-first-5599ff?style=flat-square)](./docs/configuration.md) [![GitHub stars](https://img.shields.io/github/stars/Navinspire-ia/navin?style=flat-square&color=ffd166)](https://github.com/Navinspire-ia/navin/stargazers)

**[Démarrer](#démarrer)** · **[Télécharger l'application](https://navin.live/download)** · **[Documentation](./docs/README.md)** · **[Contribuer](./CONTRIBUTING.md)**

</div>

## Confiez une mission à Navin

> « Trouve la cause de ce bug, corrige-le, lance les tests pertinents et explique les changements. »

> « Recherche ces concurrents, compare leurs offres avec des sources et transforme les résultats en rapport. »

> « Surveille ce projet et préviens-moi lorsqu'un changement demande mon attention. »

Navin relie le modèle à vos fichiers, au terminal, au navigateur et à vos outils. Il peut planifier, déléguer des tâches précises à des sous-agents, vérifier les résultats et poursuivre dans les limites configurées.

**Ce qui reste après la conversation compte aussi :** la connaissance du projet, les décisions, les compétences réutilisables et le contexte du travail.

## Démarrer

**Linux / macOS / WSL**

```bash
curl https://navin.live/install -fsS | bash
```

**Windows PowerShell**

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

Ouvrez un terminal dans votre projet, puis lancez :

```bash
cd your-project
navin-cli
```

1. Ouvrez **Settings** avec **Ctrl+G** et configurez un fournisseur avec votre clé API ou un modèle local.
2. Choisissez un modèle et un mode : **Ask**, **Plan**, **Agent**, **Review**, **Security** ou **Debug**.
3. Essayez : **« Lis ce projet et explique son fonctionnement. Propose ensuite une amélioration utile. »**

Vous préférez une fenêtre ? [Téléchargez Navin Desktop pour Windows, macOS ou Linux](https://navin.live/download).

Le logiciel est gratuit sous sa licence open source. Les API de modèles et les services externes peuvent facturer leur utilisation. [Installation et dépannage](./docs/Installation.md).

<details>
<summary><strong>Installer depuis les sources</strong></summary>

```bash
git clone https://github.com/Navinspire-ia/navin.git
cd navin
make install
make start
```

La WebUI de développement est disponible sur [localhost:5173](http://localhost:5173). Pour lancer le CLI depuis le dépôt :

```bash
.venv/bin/navin-cli
```

Windows : `.venv\Scripts\navin-cli`. Prérequis et version de production : [guide d'installation](./docs/Installation.md).

</details>

## Un espace de travail, plusieurs métiers

| Vous voulez... | Navin vous apporte |
| --- | --- |
| **Développer une application** | Exploration du dépôt, édition du code, terminal, Git, aperçu navigateur, tests et revue. |
| **Comprendre un projet** | Graphe du projet, index du code, définitions, références et analyse d'impact. |
| **Rechercher et extraire** | Recherche web, navigateur, scraping, extraction structurée et rapports sourcés. |
| **Créer des livrables** | Documents, présentations, tableurs, schémas d'architecture et workflows multimédias. |
| **Développer votre activité** | Prospection, enrichissement de leads, SEO, études marketing et préparation de campagnes. |
| **Gérer des tâches métier** | Analyse d'appels d'offres, recherche d'emploi, transcription de réunions, notes et actions. |
| **Déléguer un travail complexe** | Sous-agents spécialisés qui travaillent en parallèle et rendent leurs résultats à l'agent principal. |
| **Poursuivre dans la durée** | Boucles sur objectif, tâches planifiées et vérifications Heartbeat lorsque le gateway fonctionne. |

Certains workflows nécessitent des dépendances supplémentaires, des intégrations configurées ou un modèle compatible. Consultez la [carte des capacités](./docs/capabilities.md).

<p align="center">
  <img src="./assets/readme/cli.png" alt="CLI Navin avec un exemple de découverte de projet" width="100%">
  <br>
  <sub>Aperçu du CLI avec un exemple de découverte de projet.</sub>
</p>

## Ce qui fait la différence

**Le contexte survit à la conversation.** La mémoire du projet et le graphe du code aident l'agent à retrouver les décisions et les fichiers utiles. La mémoire épisodique, optionnelle, permet de rappeler des travaux précédents. [Mémoire](./docs/memory.md) · [Graphe du projet](./docs/navin_dev/fr/graph.md)

**Les outils relient la réflexion à l'action.** Fichiers, shell, Git, navigateur, API et MCP permettent d'exécuter le travail. Les Skills décrivent des méthodes réutilisables ; les plugins et intégrations étendent les possibilités. [Configurer MCP](./docs/guides/configure-mcp-tools.md) · [Skills](./navin/skills/README.md)

**Vous choisissez les modèles.** Connectez OpenAI, Anthropic, Google, Mistral, DeepSeek, Qwen et d'autres fournisseurs, ou des modèles locaux avec Ollama, LM Studio et vLLM. Affectez différents modèles aux différentes tâches. Les capacités dépendent du fournisseur et du modèle choisis. [Configuration](./docs/configuration.md)

**Le travail peut durer, avec des limites explicites.** Les boucles poursuivent un objectif ; Heartbeat recherche les suites utiles. Validations humaines, budgets de ressources, checkpoints et commandes d'arrêt encadrent l'exécution. [Automatisations](./docs/automations.md) · [Sécurité](./SECURITY.md)

L'exécution locale conserve votre espace de travail sur votre machine. Si vous choisissez un modèle distant ou un service connecté, les données nécessaires à la requête lui sont transmises.

## De l'objectif au résultat vérifié

<p align="center">
  <img src="./assets/readme/agent-workflow.svg" alt="Objectif, planification, permissions, outils, vérification et livraison, avec retour d'expérience vers la mémoire du projet" width="100%">
</p>

[Téléchargez le schéma interactif](./assets/readme/agent-workflow.html) et ouvrez le fichier HTML dans un navigateur. Le schéma et ses commandes sont en anglais.

## Un agent qui peut apprendre de l'expérience

Navin inclut des mécanismes d'apprentissage expérimentaux, à activer volontairement :

| Mécanisme | Ce qu'il explore |
| --- | --- |
| **Self-Evolve / Auto-Skills** | Transformer les échecs répétés en Skills candidats, les évaluer, puis les promouvoir ou revenir en arrière. |
| **World model** | Apprendre des prédictions locales sur les résultats des outils à partir des trajectoires enregistrées. |
| **Policy learning** | Évaluer des suggestions d'actions suivantes sur des cas réservés à l'évaluation. |

Ces mécanismes sont **désactivés par défaut**. Des évaluations conditionnent leur activation. Publier un Skill pour tous les projets exige une action humaine. Le modèle de conversation n'est pas réentraîné.

L'ambition : des agents autonomes de plus en plus capables. **Navin ne prétend pas être une AGI.**

[Évolution des Skills](./docs/skills-evolution.md) · [World model](./docs/world-model.md) · [Policy learning](./docs/policy.md)

## Connectez vos outils

- **Modèles :** vos clés API ou une exécution locale.
- **Extensions :** serveurs MCP, Skills, plugins et outils personnalisés.
- **Canaux :** Telegram, Slack, Discord, WhatsApp, Email, Teams et d'autres via les intégrations configurées.
- **Interfaces :** CLI, WebUI et applications desktop téléchargeables.

Ce dépôt GitHub contient le moteur agent public, le CLI, la WebUI et les outils associés. Le packaging desktop, le service navin.live, l'infrastructure du site et la publication des versions sont maintenus séparément.

## Construisons Navin ensemble

Essayez une vraie tâche. Dites-nous où l'agent vous a aidé et où il a bloqué.

- [Signalez un bug reproductible ou proposez une fonctionnalité](https://github.com/Navinspire-ia/navin/issues).
- Ajoutez un fournisseur, améliorez un Skill, développez une intégration ou un cas d'évaluation.
- Améliorez une traduction ou partagez un workflow reproductible.
- Lisez [CONTRIBUTING.md](./CONTRIBUTING.md) et le [CLA](./CLA.md) avant une pull request.

Créé par [Navinspire IA](https://navinspire.ai), maintenu par [@aymenghad](https://github.com/aymenghad) et les [contributeurs de Navin](https://github.com/Navinspire-ia/navin/graphs/contributors).

## Licence

[AGPL-3.0](./LICENSE), avec une [offre de licence commerciale](./COMMERCIAL_LICENSE.md) proposée par Navinspire IA. Les contributions suivent le [Contributor License Agreement](./CLA.md).

<div align="center">

**Donnez plus d'autonomie à votre prochain projet.**

[Installer Navin](#démarrer) · [Lire la documentation](./docs/README.md) · [Mettre une étoile](https://github.com/Navinspire-ia/navin)

Si Navin vous aide, une étoile permet à d'autres de le découvrir.

</div>
