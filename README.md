<div align="center">

<img src="./assets/logo.png" alt="Navin" width="220">

# Navin

**100% Free. Open Source. Autonomous. Built toward AGI.**

An AI Agent Harness that can remember, act, learn and evolve.

[English](./README.md) · [Français](./README.fr.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-22c55e.svg)](./LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/navinspire-ai/navin-agi?style=flat)](https://github.com/navinspire-ai/navin-agi)

Navin combines **Persistent Memory**, **Auto-Skills**, **Self-Evolve**, **World Models**, **Policy Learning**, **Multi-Agent** systems, **Loops** and **Heartbeat** to move beyond static AI assistants toward agents that improve from experience.

Code · Research · Scrape · Automate · Create · Market · Learn · Evolve

Your machine. Your models. Your agent.

<br>

[Download Navin](https://navin.live/download) · [Documentation](https://navin.live/en/docs) · [Contributing](./CONTRIBUTING.md)

<br>

⭐ Star Navin if you want AI agents you can actually own.

</div>

<p align="center">
  <img src="./assets/navin.gif" alt="Navin Studio" width="900">
</p>

## Why Navin?

Most AI tools stop after generating an answer.

Navin is built to take a goal and keep working.

```text
Goal
 ↓
Plan
 ↓
Act
 ↓
Verify
 ↓
Remember
 ↓
Learn
 ↓
Continue
 ↺
```

Navin runs as a desktop app and CLI, works locally, supports your own API keys and can use hundreds of text and multimodal models.

## Installation

### CLI

```bash
curl https://navin.live/install -fsS | bash
cd your-project
navin-cli
```

Windows PowerShell:

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

`navin-cli` is the terminal agent. `navin .` opens the desktop on the current folder.

After launch, open **Settings** to add your keys and pick a model. In `navin-cli`, press **Ctrl+G**. The web UI is where you configure everything. You do not edit JSON by hand.

### Desktop

Download from [navin.live/download](https://navin.live/download).

| Platform | Download |
| --- | --- |
| macOS (Apple Silicon) | `Navin-Desktop-macos-arm64.dmg` |
| macOS (Intel) | `Navin-Desktop-macos-x64.dmg` |
| Windows | `Navin-Desktop-windows-x64-setup.exe` · `.msi` |
| Linux | `.AppImage` · `.deb` · `.rpm` · `.pkg.tar.zst` |

### From source

```bash
git clone https://github.com/navinspire-ai/navin-agi.git
cd navin-agi
sh scripts/start.sh --install
```

This starts the local web UI. Configure providers and models in Settings, then start working. Stop with `sh scripts/stop.sh`.

## Agents

| Mode | Role |
| --- | --- |
| Ask | Understand without modifying the project |
| Plan | Create an execution plan |
| Agent | Build, edit, run, test and iterate |
| Review | Review code and propose fixes |
| Security | Analyze and harden your application |
| Debug | Reproduce, diagnose, fix and verify |

Navin can also create sub-agents for parallel and specialized work.

```text
Ask → Plan → Agent → Review → Security → Debug → Agent
```

## Agent Loop

Navin does not generate code and stop.

It can use your repository, terminal, browser, files, tools and memory to continue until the work is done or genuinely blocked.

```text
MISSION
   ↓
PLAN
   ↓
ACT
files · code · shell · git · browser · MCP
   ↓
VERIFY
tests · lint · security · evidence
   ↓
CONTINUE / RETRY / REPLAN
   ↺
```

## Loop + Heartbeat

**Loop** keeps an agent working toward a goal across multiple execution cycles.

**Heartbeat** lets autonomous tasks wake up and continue over time.

Useful for coding, research, monitoring, scraping, tenders, lead generation, job search, recurring workflows and long-running tasks.

Autonomy stays bounded by permissions, budgets, checkpoints and kill switches.

## Built toward AGI

Navin is moving beyond static assistants toward agents that can learn from experience and improve how they work.

| Capability | What it does |
| --- | --- |
| Persistent Memory | Remember useful experience across sessions, projects, code, notes and actions |
| Auto-Skills + Self-Evolve | Create, test, repair and improve reusable Skills automatically |
| World Models | Learn to predict what is likely to happen before taking an action |
| Policy Learning | Learn which tool or action is likely to be the best next step |
| Evaluation + Rollback | Every improvement must be measurable, testable and reversible |

The goal is not just an agent that works. It is an agent that gets better at working.

Navin does not claim to be AGI today. The project is building the capabilities required to move toward increasingly general autonomous intelligence.

## Self-Evolve

When Navin repeatedly fails at something, it can turn experience into a better reusable capability.

```text
Repeated failure
      ↓
Create candidate Skill
      ↓
Sandbox
      ↓
Evaluate
      ↓
Improve
      ↓
Re-evaluate
      ↓
Promote or Rollback
```

The rule is simple: better than before. Nothing important gets worse.

## Memory + Graph

Navin does not have to start from zero every session.

Session Memory · Project Brain · Long-term Memory · Dream Memory · Notes Memory · Code Graph · Knowledge Graph · Project Indexing · Execution History · Checkpoints

```text
Code · Notes · Meetings · Research · Tasks
                  ↓
             Project Brain
                  ↓
              Agent Loop
```

## One AI workspace

Navin connects many workflows to the same agent, memory and project context.

| Module | What Navin can do |
| --- | --- |
| Code | Build · Debug · Review · Security · Git · Terminal |
| Research | Web research · Multi-agent research · Documents |
| Scraping | Crawl · Extract · Structure · Analyze |
| Leads | Find · Enrich · Score · Qualify |
| Marketing | Research · Strategy · Content · Campaigns |
| Tenders | Find opportunities · Analyze · Prepare responses |
| Career | Find jobs and freelance missions · Analyze opportunities |
| Meetings | Record · Transcribe · Summarize · Extract actions |
| Notes | Write · Search · Ask · Connect knowledge |
| Projects | Tasks · Decisions · Context · Agent execution |
| SEO | Audit · Keywords · Content · Actions |
| Media | Image · Video · Music · Speech · STT · TTS |

```text
Meeting → Decisions → Tasks → Code
Research → Leads → Marketing → Campaign
Product → Demo → SEO → Leads
```

One context. One memory. One agent system.

## Models

Use the models you want. Add keys and pick a model in **Settings**.

**Local:** Ollama · LM Studio · vLLM · OpenAI-compatible servers

**BYOK:** bring your own API keys across 28+ providers.

**Navin Providers:** 380+ text and multimodal models, including OpenAI, Anthropic, Google, xAI, Qwen, Z.ai / GLM, Kimi, MiniMax, DeepSeek, Mistral, NVIDIA and more.

Multimodal workflows: Image · Video · Music · Vision · Speech · STT · TTS

## Tools and integrations

Files · Code · Shell · Git · Browser · APIs · Databases · MCP · Plugins · SaaS

Extend Navin with Skills, MCP servers, plugins, custom tools, agent packs, workflows and integrations.

Channels: WhatsApp · Telegram · Slack · Discord · Email · Teams and more.

## Local-first

**Your machine. Your models. Your data.**

Run local models. Bring your own API keys. Use managed models only if you want them.

No mandatory cloud. No mandatory model provider.

## Safety

Sandbox execution · Checkpoints · Permissions · Human approvals · Isolated work · Resource limits · Rollback · Kill switches

More autonomy does not automatically mean more permissions.

## Documentation

[navin.live/en/docs](https://navin.live/en/docs) · [Installation](./docs/Installation.md) · [Capabilities](./docs/capabilities.md)

## Contributing

Navin is open source and contributions are welcome: agent runtime, CLI, Skills, MCP, providers, memory, world models, policy learning, evaluations, integrations, UI, documentation and bug fixes.

Please read [CONTRIBUTING.md](./CONTRIBUTING.md) before opening a pull request.

## Contributors

Built by [Navinspire IA](https://navinspire.ai) and the Navin community.

[@aymenghad](https://github.com/aymenghad) ·
[@anisf](https://github.com/anisf) ·
[@Amira-ben-henda-eiagen](https://github.com/Amira-ben-henda-eiagen) ·
[@hasseniImen](https://github.com/hasseniImen) ·
[@maryem955](https://github.com/maryem955) ·
[@medkhalilklai](https://github.com/medkhalilklai) ·
[@SkanderBS2024](https://github.com/SkanderBS2024) ·
[@yosra-wanen](https://github.com/yosra-wanen) ·
[@nabilmersni2](https://github.com/nabilmersni2)

## License

Navin is open source under the [MIT License](./LICENSE).

<div align="center">

100% Free. Open Source. Autonomous. Built toward AGI.

Plan · Act · Verify · Remember · Learn · Evolve

[Download Navin](https://navin.live/download) · [Documentation](https://navin.live/en/docs) · [Contribute](./CONTRIBUTING.md)

<br>

⭐ Star Navin if you want open-source agents that actually learn.

<br>

Your machine. Your models. Your agent.

Made by [Navinspire IA](https://navinspire.ai) · Paris

</div>

<sub>A small early upstream from [nanobot](https://github.com/HKUDS/nanobot) (MIT) is listed with other third-party notices in [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).</sub>
