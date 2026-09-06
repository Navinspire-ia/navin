# navin Documentation

**Navin AGI** is the local **AGI**: one loop for the model, tools, memory, permissions and channels. Studios (Code, Scraping, Marketing, Leads, Documents, Meeting…) sit on that AGI.

Use these docs to add one capability at a time. Repository docs follow the current source tree and can be newer than the latest package release.

Read [What is Navin AGI](./navin-harness.md) first if you want the product mental model.

For desktop install steps on Windows, macOS and Linux, use the installation guide on [navin.live/docs/install](https://navin.live/docs/install).

## CLI

Same engine as the desktop app. [CLI](./cli/overview.md).

| Goal | Guide |
|---|---|
| AGI switches from the terminal | [`navin agi`](./cli/agi.md) |
| One-liner and packages | [Install](./cli/install.md) |
| First session | [Quickstart](./cli/quickstart.md) |
| Commands | [Commands](./cli/commands.md) |
| Terminal UI | [navin-cli](./cli/interactive.md) |
| Slash commands | [Slash commands](./cli/slash-commands.md) |
| Keys | [Shortcuts](./cli/shortcuts.md) |
| Tools | [Tools](./cli/tools.md) |
| Providers and models | [Settings](./cli/settings.md) |
| Triggers and local API | [Scripting](./cli/scripting.md) |
| PATH, doctor, devices | [Troubleshooting](./cli/troubleshooting.md) |

## Start

| Goal | Guide |
|---|---|
| Understand Navin as an AGI | [Navin AGI](./navin-harness.md) |
| Packages, CLI one-liner, and source build | [Installation](./Installation.md) |
| What Navin can do (modules, loop, settings) | [Capabilities](./capabilities.md) |
| Install and send a first message | [Quick start](./quick-start.md) |
| Guided setup without terminal experience | [Start without a technical background](./start-without-technical-background.md) |

## Add One Capability

| Goal | Guide |
|---|---|
| Configure providers, model presets, and routing | [Configuration](./configuration.md) |
| Choose a hosted, OAuth, company, or local model | [Provider Cookbook](./provider-cookbook.md) |
| Add model fallbacks | [Configure Model Fallback](./guides/configure-model-fallback.md) |
| Enable web search | [Configure Web Search](./guides/configure-web-search.md) |
| Add an MCP tool server | [Configure MCP Tools](./guides/configure-mcp-tools.md) |
| Present HTML / Markdown / Mermaid beside chat | [Artifacts / Canvas](./artifacts.md) |
| Microphone STT, TTS and realtime voice | [Voice](./voice.md) |
| Team seats ($40/seat), orgs, shared quota | [Team plans](./team-plans.md) |
| Shared projects, ownership zones, PM cockpit | [Organization projects](./org-projects.md) |
| Enterprise SSO, audit, evals | [Enterprise](./enterprise.md) |
| Browse / publish Navin Marketplace skills | [Marketplace](./marketplace.md) |
| Install a complete AI app from the gallery | [App templates](./app-templates.md) |
| Generate images | [Image Generation](./image-generation.md) |
| Scrape / crawl / export web data | [Scrape Tool](./scrape-tool.md) · [Scraping studio](./navin_scraping/README.md) |
| Schedule work or create a local trigger | [Automations](./automations.md) |
| Run a 100% local bot 24/7 (service, missions, heartbeat) | [Long-running agent](./guides/long-running-ai-agent.md) · [Deploy the gateway](./guides/deploy-navin-gateway.md) |
| Let the agent chain tasks, branch, and open PRs | [Board Autonomy](./navin_dev/en/board-autonomy.md) |
| Resume a long-running project in one click | [Project Home](./navin_dev/en/project-home.md) |
| Understand and manage long-term memory | [Memory](./memory.md) |
| Let Navin draft, examine and promote its own skills (off by default) | [Skills evolution](./skills-evolution.md) |
| Let Navin predict what a tool will answer before calling it (off by default) | [World model](./world-model.md) |
| Let Navin learn which tool to call next, from eval trajectories only (off by default) | [Policy learning](./policy.md) |
| Read how a transfer campaign and the safety case are judged; the claim stays forbidden until both pass (off by default) | [Transfer protocol](./transfer-protocol.md) |
| Run separate bots or workspaces | [Multiple Instances](./multiple-instances.md) |

For shorter, outcome-focused walkthroughs, browse the [task guide index](./guides/README.md).

## Studio Modules (English + Français)

| Module | Route | Docs |
|---|---|---|
| RiskLens - assume failure in 6 months, revise before you build, `/risklens` | `#/risklens` | [navin_risklens](./navin_risklens/README.md) |
| Dev / Code - workbench, slash commands, Actions, skills, plugins, Mobile | `#/code` | [navin_dev](./navin_dev/README.md) · [Modes](./navin_dev/en/modes.md) · [Expert tools](./navin_dev/en/expert-tools.md) · [Mobile](./mobile.md) |
| Evolve Engine - prove, optimize and auto-fix your projects with signed certificates | `#/evolve` | [navin_evolve](./navin_evolve/README.md) · [FR](./navin_evolve/fr/README.md) |
| Scraping - crawl, clean, enrich, export (CSV/JSON/XML/Excel), `/scrape` | `#/scraping` | [navin_scraping](./navin_scraping/README.md) · [Scrape Tool](./scrape-tool.md) |
| Documents - templates (PPTX/DOCX/PDF/XLSX), `/studio` | `#/content` | [navin_contenant](./navin_contenant/README.md) |
| Marketing - Agent OS, growth loop, `/marketing` `/campaign` | `#/marketing` | [navin_marketing](./navin_marketing/README.md) · [Loop + heartbeat](./md/README.md) · [Marketing](./md/marketing.md) |
| Montage - live product demos, social video exports, FFmpeg / HyperFrames, `/montage` | `#/montage` | [navin_montage](./navin_montage/README.md) |
| Ads - Google / Meta / TikTok / Reddit Ads live MCP, `/ads` | `#/ads` | [navin_ads](./navin_ads/README.md) |
| SEO - audits, keywords, optimized content, `/seo` | `#/seo` | [navin_seo](./navin_seo/README.md) |
| Tenders - official collect, Go/No-Go, dossier writer, `/tenders` | `#/tenders` | [Loop + write](./md/README.md) · [Tenders](./md/tenders.md) · [Write](./md/tenders-write.md) · [studio](./navin_tenders/README.md) |
| Career - jobs and missions desk, `/career` | `#/career` | [EN](./navin_career/en/README.md) · [write](./navin_career/en/write.md) · [FR](./navin_career/fr/README.md) · [loop](./navin_career/en/loop.md) · [contrat](./md/career.md) · [write contrat](./md/career-write.md) · [deux horloges](./studio/desk-loop.md) |
| Trading - paper Trading Agent OS, `/trading` | `#/trading` | [EN](./navin_trading/en/README.md) · [loop](./navin_trading/en/loop.md) · [contrat](./md/trading.md) · [deux horloges](./studio/desk-loop.md) |
| Leads & Sales - open-data hunt, BANT-F, Start loop, `/leads` | `#/leads` | [navin_leads](./navin_leads/README.md) · [loop](./navin_leads/en/loop.md) · [Tauri](./navin_leads/en/desktop.md) |
| Notes - Markdown knowledge space, graph, Navin IA, export MD/TXT/PDF | `#/notes` | [navin_notes](./navin_notes/README.md) · [EN](./navin_notes/en/README.md) · [FR](./navin_notes/fr/README.md) |
| Meeting - local capture, transcription, minutes, calendar, audit trail, `/meeting` | `#/meeting` | [navin_meeting](./navin_meeting/README.md) · [Transcription](./navin_meeting/en/transcription.md) · [Privacy](./navin_meeting/en/privacy.md) |

## Operate and reference

| Need | Read |
|---|---|
| Providers, models, presets, and config fields | [Configuration](./configuration.md) |
| Provider/model matching and selection | [Providers and Models](./providers.md) |
| Provider setup cookbook | [Provider Cookbook](./provider-cookbook.md) |
| Runtime self-inspection and tuning | [My Tool](./my-tool.md) |
| Scrape / crawl / export reference | [Scrape Tool](./scrape-tool.md) |
| Expo / React Native / Flutter run + preview | [Mobile Agent](./mobile.md) |
| Phone / tablet chat client (WebUI PWA) | [Mobile usage PWA](./mobile-usage-app.md) |
| Environment variables | [Environment variables](./environment-variables.md) |
| Local ports (WebUI, gateway, API, MCP) | [Ports](./ports.md) |

Keep real API keys, bot tokens, and passwords out of issues and public logs.

## Extend or Contribute

| Goal | Read |
|---|---|
| Understand source ownership and runtime flow | [Architecture](./architecture.md) |
| Add a channel package | [Channel Plugin Guide](./channel-plugin-guide.md) |
| Build the WebUI source | [WebUI Development](../webui/README.md) |

If a command or screen no longer matches these docs, please [open an issue](https://github.com/navinspire-ai/navin-agi/issues) with your navin version, operating system, and the page that needs correction.
