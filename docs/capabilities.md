# Navin capabilities

This page is the product map: what Navin can do, which module owns it, and where the setting lives in the source UI (`navin-cli` **Ctrl+G**, or desktop Settings). Install first: [Installation](./Installation.md). Mental model: [Navin AGI](./navin-harness.md).

Config file: `~/.navin/config.json`. Same file for `navin-cli` and the desktop.

## How you run it

| Surface | Command / URL | Notes |
| --- | --- | --- |
| Terminal AGI | `navin-cli` | Product CLI. Open a project folder first |
| Desktop / workbench | `navin .` | Same engine, GUI |
| Gateway | `navin gateway` | Long-running host, default WebUI `:8765` |
| AGI switches | `navin agi status` | All off by default |
| Settings | **Ctrl+G** | Providers, models, tools, safety, account |
| Mode | **Ctrl+T** | ask, plan, agent, review, security, debug |
| Docs on the web | [navin.live/en/docs](https://navin.live/en/docs) | Same topics as this `docs/` tree |

`navin-cli` keys: [interactive](./cli/interactive.md) · [shortcuts](./cli/shortcuts.md)

## Agent loop

Navin takes a goal and keeps working until it is done or blocked.

```text
MISSION → PLAN → ACT (files, code, shell, git, browser, MCP)
       → VERIFY (tests, lint, security, evidence)
       → CONTINUE / RETRY / REPLAN
```

| Mode | Slash / habit | What it does |
| --- | --- | --- |
| Ask | free text, no writes | Understand the repo |
| Plan | `/blueprint` | Design only, then hand off |
| Agent | `/forge` `/cruise` `/mission` `/mobile` | Build, test, iterate |
| Review | `/inspect` | Read-only review + HTML report |
| Security | `/fortify` and the security family | AppSec audit + hardening choices |
| Debug | `/debug` | Reproduce, prove root cause, fix plan |

Full mode table: [Composer modes](./navin_dev/en/modes.md) · slash list: [cli/slash-commands](./cli/slash-commands.md) · Code commands: [navin_dev/en/commands](./navin_dev/en/commands.md)

## Loop and Heartbeat

**Loop** is a multi-cycle run toward a goal. Examples: `/cruise`, `/mission`, studio desks (Career, Trading, Tenders, Marketing, Leads).

**Heartbeat** is a quiet periodic pass. It reads `.navin/HEARTBEAT.md` in the project and only reports when something is useful. It is not a noisy cron.

```bash
navin gateway --background
navin gateway install-service
```

Create a scheduled job from chat ("every weekday at 9, …") or edit `HEARTBEAT.md`.

- [Automations](./automations.md)
- [Desk loop](./studio/desk-loop.md)
- [Long-running agent](./guides/long-running-ai-agent.md)

## AGI (off by default)

These are the learning layers. A locked switch unlocks itself when the stage below has passed its exam. Nothing is fine-tuned on your chat model.

| Layer | Switch | What it does | Docs |
| --- | --- | --- | --- |
| Skills evolution | `navin agi on` | Draft, exam, promote or rollback a skill after repeated failure | [skills-evolution](./skills-evolution.md) |
| Memory | `navin agi memory on` | Episodes and recall across sessions | [memory](./memory.md) |
| World model | `navin agi world on` | Predict what a tool will answer before calling it | [world-model](./world-model.md) |
| Policy | `navin agi policy on` | Learn the next action from eval trajectories | [policy](./policy.md) |
| Transfer protocol | `navin agi transfer status` | Hidden exam + dossier; claim stays forbidden until proofs pass | [transfer-protocol](./transfer-protocol.md) |

```bash
navin agi status
navin agi drafts
navin agi world exam
navin agi policy exam
```

Flags live under the project: `.navin/skills-evolve.json`, `.navin/cognition.json`, `.navin/world-model.json`, `.navin/policy.json`.

CLI reference: [navin agi](./cli/agi.md)

## Code studio (`#/code`)

Workbench: explorer, editor, terminals, web preview, mobile preview, git, graph, multi-agent chat.

| Piece | What you get | Docs |
| --- | --- | --- |
| Workbench | Explorer, editor, terminals, preview, project picker | [workbench](./navin_dev/en/workbench.md) |
| Graph | Files / packages, impact, path | [graph](./navin_dev/en/graph.md) |
| Project Home | Resume, tasks, issues, Vision 360, brain, timeline | [project-home](./navin_dev/en/project-home.md) |
| Board autonomy | Consent, per-task branch, PR on done, issue sync | [board-autonomy](./navin_dev/en/board-autonomy.md) |
| Actions | One-click quality, security, performance, design audits | [actions](./navin_dev/en/actions.md) |
| Expert tools | `code_review`, `security_scan`, `debug_repair`, DebugMCP | [expert-tools](./navin_dev/en/expert-tools.md) |
| Mobile | Expo / RN / Flutter run + preview | [mobile](./navin_dev/en/mobile.md) · [mobile.md](./mobile.md) |
| Editor AI | Tab completions, inline edit, diff review | [editor-ai](./navin_dev/en/editor-ai.md) |
| Plugins / skills | Packs and project skills | [plugins](./navin_dev/en/plugins.md) · [skills](./navin_dev/en/skills.md) |
| App templates | Install a full app from the gallery | [app-templates](./app-templates.md) |
| Evolve | Prove a change under load before merge | [navin_evolve](./navin_evolve/README.md) |
| RiskLens | Assume failure in 6 months, revise first | [navin_risklens](./navin_risklens/README.md) |

Index: [navin_dev](./navin_dev/README.md)

## Other studios

Same agent, same memory, different desk.

| Studio | Route | Typical slash | Docs |
| --- | --- | --- | --- |
| Scraping | `#/scraping` | `/scrape` | [navin_scraping](./navin_scraping/README.md) |
| Documents | `#/content` | `/studio` | [navin_contenant](./navin_contenant/README.md) |
| Marketing | `#/marketing` | `/marketing` `/campaign` | [navin_marketing](./navin_marketing/README.md) |
| Montage | `#/montage` | `/montage` | [navin_montage](./navin_montage/README.md) |
| Ads | `#/ads` | `/ads` | [navin_ads](./navin_ads/README.md) |
| SEO | `#/seo` | `/seo` | [navin_seo](./navin_seo/README.md) |
| Tenders | `#/tenders` | `/tenders` | [navin_tenders/en](./navin_tenders/en/README.md) |
| Career | `#/career` | `/career` | [navin_career/en](./navin_career/en/README.md) |
| Trading | `#/trading` | `/trading` | [navin_trading/en](./navin_trading/en/README.md) |
| Leads | `#/leads` | `/leads` | [navin_leads](./navin_leads/README.md) |
| Notes | `#/notes` | | [navin_notes](./navin_notes/README.md) |
| Meeting | `#/meeting` | `/meeting` | [navin_meeting](./navin_meeting/README.md) |

Desk loop + heartbeat (Career, Trading, Tenders, Marketing): [studio/desk-loop](./studio/desk-loop.md)

## Settings (source of truth)

Open **Ctrl+G** in `navin-cli` (or Settings in the desktop). Sections match `navin/tui/settings.py` / the desktop Settings view.

### Providers

API keys, `apiBase`, OAuth. Local examples: Ollama, LM Studio, vLLM, any OpenAI-compatible server.

OAuth helpers: `navin provider login` (`openai_codex`, `github_copilot`, `xai_oauth`).

There is no `navin config set api-key`. Use this form, an env var, or OAuth.

[providers](./providers.md) · [provider-cookbook](./provider-cookbook.md) · [cli/settings](./cli/settings.md)

### Models

Model configurations, the active default, and **task routing** (plan / dev / review / security / deep / docs, …). Switch in chat with `/model <preset>`.

[configuration](./configuration.md) · [model fallback](./guides/configure-model-fallback.md)

### Tools and MCP

MCP servers and presets that extend the agent. **Ctrl+U** is the same hub.

[Configure MCP](./guides/configure-mcp-tools.md) · [cli/tools](./cli/tools.md)

### Skills

Enable or disable skills. Install from git, npm or a folder. **Ctrl+K**.

[Marketplace](./marketplace.md) · [skills evolution](./skills-evolution.md)

### Image / Video / Voice

Generation defaults (provider, model, aspect, size, duration). Voice: STT, realtime, TTS, music.

[image-generation](./image-generation.md) · [voice](./voice.md)

### Web

Search provider (DuckDuckGo, Brave, Exa, Tavily), API key, max results, Jina reader for page fetch.

[Configure web search](./guides/configure-web-search.md)

### System

Time zone, assistant name, machine resource caps (RAM share, agents per core).

### Security

Command confirmation (risky only / always), ask for permissions, remember decisions, security profile (auto / autonomous / assisted / strict), shell on/off, restrict to workspace, builtin deny rules, extra deny/allow regexes, localhost service access.

**Ctrl+E** opens the security hub.

### Guardrails

Per-project board autonomy: consent, auto-branch, PR on done, GitHub issue sync, autopilot loop, kill switches.

**Ctrl+Y**. [board-autonomy](./navin_dev/en/board-autonomy.md)

### Git

Global auto-branch / PR switches and forge tokens (GitHub, GitLab, Forgejo).

### Browser

Playwright: visible window vs headless, live view in the editor.

### Rules

Project rules in `.navin/rules/*.md`.

### Account

Optional navin.live subscription, managed models and usage. Not required for BYOK.

[cli/license](./cli/license.md)

### About

Version, config path, workspace, link to [navin.live/docs](https://navin.live/docs), raw schema-validated `config.json`.

## Channels and always-on

Connect Telegram, Discord, Slack, Email, Mattermost, Teams, WhatsApp and more.

- [Chat apps](./guides/chat-app-ai-agent.md)
- [guides index](./guides/README.md)
- [Deploy gateway](./guides/deploy-navin-gateway.md)

## Safety model

More autonomy does not grant more permissions by itself.

| Control | Where |
| --- | --- |
| Approvals | Settings → Security |
| Sandbox | `navin-sandbox` (`make native`) |
| Checkpoints | agent loop / git |
| Guardrails | Settings → Guardrails, **Ctrl+Y** |
| Kill switches | Guardrails + Git |
| Rollback | AGI world / policy / skill promote |

## Environment and ports

- [environment-variables](./environment-variables.md)
- [ports](./ports.md)
- [multiple instances](./multiple-instances.md)

```bash
navin ports
navin doctor
navin status
```

## Docs index

Everything else is listed in [docs/README.md](./README.md).
