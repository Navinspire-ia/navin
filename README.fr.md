<div align="center">

<img src="./assets/logo.png" alt="Navin" width="220">

# Navin

**100% gratuit. Open source. Autonome. Conçu vers l'AGI.**

Un Agent Harness IA qui se souvient, agit, apprend et évolue.

[English](./README.md) · [Français](./README.fr.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e.svg)](./LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/navinspire-ai/navin-agi?style=flat)](https://github.com/navinspire-ai/navin-agi)

Navin combine **Persistent Memory**, **Auto-Skills**, **Self-Evolve**, **World Models**, **Policy Learning**, les systèmes **Multi-Agent**, les **Loops** et **Heartbeat** pour aller au-delà des assistants statiques, vers des agents qui s'améliorent avec l'expérience.

Code · Research · Scrape · Automate · Create · Market · Learn · Evolve

Votre machine. Vos modèles. Votre agent.

<br>

[Télécharger Navin](https://navin.live/download) · [Documentation](https://navin.live/fr/docs) · [Contribuer](./CONTRIBUTING.md)

<br>

⭐ Star Navin si vous voulez des agents IA que vous possédez vraiment.

</div>

<p align="center">
  <img src="./assets/navin.gif" alt="Navin Studio" width="900">
</p>

## Pourquoi Navin ?

La plupart des outils IA s'arrêtent après une réponse.

Navin prend un objectif et continue de travailler.

```text
Objectif
 ↓
Plan
 ↓
Action
 ↓
Vérification
 ↓
Mémoire
 ↓
Apprentissage
 ↓
Continuer
 ↺
```

Navin tourne en application desktop et en CLI, fonctionne en local, accepte vos propres clés API et peut utiliser des centaines de modèles texte et multimodaux.

## Installation

### CLI

```bash
curl https://navin.live/install -fsS | bash
cd your-project
navin-cli
```

Windows PowerShell :

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

`navin-cli` est l'agent terminal. `navin .` ouvre le desktop sur le dossier courant.

Après le lancement, ouvrez **Settings** pour ajouter vos clés et choisir un modèle. Dans `navin-cli`, appuyez sur **Ctrl+G**. C'est l'interface qui configure tout. Pas besoin d'éditer du JSON à la main.

### Desktop

Téléchargez depuis [navin.live/download](https://navin.live/download).

| Plateforme | Téléchargement |
| --- | --- |
| macOS (Apple Silicon) | `Navin-Desktop-macos-arm64.dmg` |
| macOS (Intel) | `Navin-Desktop-macos-x64.dmg` |
| Windows | `Navin-Desktop-windows-x64-setup.exe` · `.msi` |
| Linux | `.AppImage` · `.deb` · `.rpm` · `.pkg.tar.zst` |

### Depuis les sources

```bash
git clone https://github.com/navinspire-ai/navin-agi.git
cd navin-agi
sh scripts/start.sh --install
```

Cela démarre l'interface web locale. Configurez les providers et les modèles dans Settings, puis travaillez. Arrêt : `sh scripts/stop.sh`.

## Agents

| Mode | Rôle |
| --- | --- |
| Ask | Comprendre sans modifier le projet |
| Plan | Créer un plan d'exécution |
| Agent | Construire, éditer, lancer, tester et itérer |
| Review | Relire le code et proposer des correctifs |
| Security | Analyser et durcir l'application |
| Debug | Reproduire, diagnostiquer, corriger et vérifier |

Navin peut aussi créer des sous-agents pour un travail parallèle et spécialisé.

```text
Ask → Plan → Agent → Review → Security → Debug → Agent
```

## Agent Loop

Navin ne génère pas du code pour s'arrêter.

Il peut utiliser votre dépôt, le terminal, le navigateur, les fichiers, les outils et la mémoire jusqu'à ce que le travail soit fait, ou vraiment bloqué.

```text
MISSION
   ↓
PLAN
   ↓
ACT
fichiers · code · shell · git · navigateur · MCP
   ↓
VERIFY
tests · lint · sécurité · preuves
   ↓
CONTINUE / RETRY / REPLAN
   ↺
```

## Loop + Heartbeat

**Loop** garde un agent sur un objectif pendant plusieurs cycles d'exécution.

**Heartbeat** réveille des tâches autonomes et les fait continuer dans le temps.

Utile pour le code, la recherche, le monitoring, le scraping, les appels d'offres, la génération de leads, la recherche d'emploi, les workflows récurrents et les tâches longues.

L'autonomie reste bornée par les permissions, les budgets, les checkpoints et les kill switches.

## Conçu vers l'AGI

Navin va au-delà des assistants statiques : des agents qui apprennent de l'expérience et s'améliorent.

| Capacité | Ce que ça fait |
| --- | --- |
| Persistent Memory | Retenir l'expérience utile entre sessions, projets, code, notes et actions |
| Auto-Skills + Self-Evolve | Créer, tester, réparer et améliorer des Skills réutilisables |
| World Models | Anticiper ce qui risque d'arriver avant d'agir |
| Policy Learning | Apprendre quelle action ou quel outil est le meilleur prochain pas |
| Evaluation + Rollback | Chaque amélioration doit être mesurable, testable et réversible |

Le but n'est pas seulement un agent qui marche. C'est un agent qui devient meilleur à travailler.

Navin ne prétend pas être l'AGI aujourd'hui. Le projet construit les capacités nécessaires pour aller vers une intelligence autonome de plus en plus générale.

## Self-Evolve

Quand Navin échoue plusieurs fois sur la même chose, il peut transformer l'expérience en une meilleure capacité réutilisable.

```text
Échec répété
      ↓
Skill candidate
      ↓
Sandbox
      ↓
Évaluation
      ↓
Amélioration
      ↓
Re-évaluation
      ↓
Promotion ou Rollback
```

La règle est simple : mieux qu'avant. Rien d'important ne se dégrade.

## Memory + Graph

Navin n'a pas à repartir de zéro à chaque session.

Session Memory · Project Brain · Long-term Memory · Dream Memory · Notes Memory · Code Graph · Knowledge Graph · Project Indexing · Execution History · Checkpoints

```text
Code · Notes · Réunions · Recherche · Tâches
                  ↓
             Project Brain
                  ↓
              Agent Loop
```

## Un seul workspace IA

Navin relie beaucoup de workflows au même agent, à la même mémoire et au même contexte projet.

| Module | Ce que Navin peut faire |
| --- | --- |
| Code | Build · Debug · Review · Security · Git · Terminal |
| Research | Recherche web · Recherche multi-agents · Documents |
| Scraping | Crawl · Extraction · Structure · Analyse |
| Leads | Trouver · Enrichir · Scorer · Qualifier |
| Marketing | Recherche · Stratégie · Contenu · Campagnes |
| Tenders | Trouver des opportunités · Analyser · Préparer les réponses |
| Career | Trouver jobs et missions freelance · Analyser les opportunités |
| Meetings | Enregistrer · Transcrire · Résumer · Extraire les actions |
| Notes | Écrire · Chercher · Demander · Relier la connaissance |
| Projects | Tâches · Décisions · Contexte · Exécution agent |
| SEO | Audit · Mots-clés · Contenu · Actions |
| Media | Image · Vidéo · Musique · Voix · STT · TTS |

```text
Meeting → Décisions → Tâches → Code
Research → Leads → Marketing → Campagne
Produit → Démo → SEO → Leads
```

Un contexte. Une mémoire. Un système d'agents.

## Modèles

Utilisez les modèles que vous voulez. Ajoutez vos clés et choisissez un modèle dans **Settings**.

**Local :** Ollama · LM Studio · vLLM · serveurs compatibles OpenAI

**BYOK :** vos propres clés API, 28+ providers.

**Navin Providers :** 380+ modèles texte et multimodaux, dont OpenAI, Anthropic, Google, xAI, Qwen, Z.ai / GLM, Kimi, MiniMax, DeepSeek, Mistral, NVIDIA et d'autres.

Workflows multimodaux : Image · Vidéo · Musique · Vision · Voix · STT · TTS

## Outils et intégrations

Fichiers · Code · Shell · Git · Navigateur · APIs · Bases · MCP · Plugins · SaaS

Étendez Navin avec des Skills, serveurs MCP, plugins, outils custom, agent packs, workflows et intégrations.

Canaux : WhatsApp · Telegram · Slack · Discord · Email · Teams et d'autres.

## Local-first

**Votre machine. Vos modèles. Vos données.**

Modèles locaux. Vos clés API. Modèles managés seulement si vous les voulez.

Pas de cloud obligatoire. Pas de provider obligatoire.

## Sécurité

Sandbox · Checkpoints · Permissions · Validations humaines · Isolation · Limites de ressources · Rollback · Kill switches

Plus d'autonomie ne veut pas dire plus de permissions.

## Documentation

[navin.live/fr/docs](https://navin.live/fr/docs) · [Installation](./docs/Installation.md) · [Capabilities](./docs/capabilities.md)

## Contribuer

Navin est open source. Les contributions sont les bienvenues : runtime agent, CLI, Skills, MCP, providers, mémoire, world models, policy learning, évaluations, intégrations, UI, documentation et correctifs.

Lisez [CONTRIBUTING.md](./CONTRIBUTING.md) avant d'ouvrir une pull request.

## Contributeurs

Construit par [Navinspire IA](https://navinspire.ai) et la communauté Navin.

[@aymenghad](https://github.com/aymenghad) ·
[@anisf](https://github.com/anisf) ·
[@Amira-ben-henda-eiagen](https://github.com/Amira-ben-henda-eiagen) ·
[@hasseniImen](https://github.com/hasseniImen) ·
[@maryem955](https://github.com/maryem955) ·
[@medkhalilklai](https://github.com/medkhalilklai) ·
[@SkanderBS2024](https://github.com/SkanderBS2024) ·
[@yosra-wanen](https://github.com/yosra-wanen) ·
[@nabilmersni2](https://github.com/nabilmersni2)

## Licence

Navin est open source sous [licence MIT](./LICENSE).

<div align="center">

100% gratuit. Open source. Autonome. Conçu vers l'AGI.

Plan · Act · Verify · Remember · Learn · Evolve

[Télécharger Navin](https://navin.live/download) · [Documentation](https://navin.live/fr/docs) · [Contribuer](./CONTRIBUTING.md)

<br>

⭐ Star Navin si vous voulez des agents open source qui apprennent vraiment.

<br>

Votre machine. Vos modèles. Votre agent.

Fait par [Navinspire IA](https://navinspire.ai) · Paris

</div>

<sub>Un petit amont précoce de [nanobot](https://github.com/HKUDS/nanobot) (MIT) figure avec les autres notices dans [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).</sub>
