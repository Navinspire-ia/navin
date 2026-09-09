# navin Skills

This directory contains built-in skills that extend navin's capabilities.

## Skill Format

Each skill is a directory containing a `SKILL.md` file with:
- YAML frontmatter (name, description, metadata)
- Markdown instructions for the agent

When skills reference large local documentation or logs, prefer navin's built-in
`grep` tool to narrow the search space before loading full files.

## Custom skills

Create or delete custom skills from the WebUI (`#/skills`) or by writing
`<workspace>/.navin/skills/<name>/SKILL.md`. Workspace skills override built-ins of the
same name and are included in the agent skills summary automatically.

## Built-in catalog

### Core
| Skill | Description |
|-------|-------------|
| `github` | GitHub via `gh` CLI |
| `weather` | Weather (wttr.in / Open-Meteo) |
| `summarize` | Summarize URLs/files/video |
| `tmux` | Control tmux sessions |
| `clawhub` | Install skills from ClawHub |
| `skill-creator` | Create/package skills |
| `cron` | Schedule reminders/tasks |
| `memory` | Dream memory (always on) |
| `image-generation` | Image generation |
| `my` | Runtime introspection |
| `update-setup` | Update/setup helpers |

### Intelligence
| Skill | Description |
|-------|-------------|
| `task-planner` | Decompose complex work |
| `adaptive-reasoning` | Match reasoning depth |
| `proactive-agent` | Cron / heartbeat / resume |
| `multi-agent-orchestration` | Coordinate `spawn` roles |
| `critic-reviewer` | Pre-delivery critique |
| `self-healing-retry` | Smart retries |
| `model-router` | Recommend model/preset |
| `context-compressor` | Resume briefs |

### Security
| Skill | Description |
|-------|-------------|
| `skill-vetter` | Audit skills before install |
| `permission-guard` | Least privilege |
| `human-approval` | Gate irreversible actions |
| `secrets-manager` | Safe secret handling |
| `audit-logger` | Action audit trails |
| `prompt-injection-defender` | Resist hostile content |
| `backup-rollback` | Snapshot before risky edits |
| `security-auditor` | Full app security audit (`/fortify`) |
| `debug-live` | Live debug: DebugMCP, repro, isolated branch (`/debug`) |
| `studio-html-report` | Expert HTML reports (Review / Security / Debug + studio) |
| `vulnerability-scanner` | Flaw & CVE hunting (`/probe`) |

### Navigation & research
| Skill | Description |
|-------|-------------|
| `playwright-browser` | Browser automation with the built-in `browser` tool |
| `computer-use` | Desktop control (screen, mouse, keyboard) with the built-in `computer` tool |
| `deep-web-research` | Multi-source research |
| `web-extractor` | Site → Markdown/JSON |
| `website-monitor` | Change detection |
| `entity-research` | Company/people intel |

### DevOps
| Skill | Description |
|-------|-------------|
| `fullstack-dev` | Dev mode: plan/investigate/code/run/verify |
| `project-metadata` | `.navin/metadata` knowledge base + metagraph (`/atlas`) |
| `code-reviewer` | Code/PR review (`/inspect`, OCR 5-gates + `pr_comments`) |
| `test-generator` | Generate & run tests |
| `docker-operator` | Docker / Compose |
| `kubernetes-operator` | kubectl diagnostics |
| `terraform-agent` | Terraform plan/apply discipline |
| `cicd-agent` | Actions / CI pipelines |
| `observability-agent` | Metrics/logs/traces |
| `api-engineer` | OpenAPI & connectors |
| `shell-sandbox` | Safe `exec` habits |
| `performance-auditor` | Profiling & optimization (`/turbo`) |
| `quality-gate` | Release go/no-go gate |
| `pack-builder` | Create & manage plugin packs (`/pack`) |

### Data
| Skill | Description |
|-------|-------------|
| `database-explorer` | Multi-engine schema explore |
| `sql-analyst` | Business → SQL |
| `supabase-operator` | Supabase admin patterns |
| `stripe-operator` | Stripe billing & payments |
| `rag-knowledge-builder` | RAG ingest/eval |
| `pdf-ocr-extractor` | PDF/OCR intake |
| `data-quality-agent` | DQ checks |
| `data-migration-agent` | Migration playbooks |
| `kpi-reporter` | Recurring KPI reports |
| `metrics-analyst` | Project health dashboard (`/pulse`) |

### SEO
| Skill | Description |
|-------|-------------|
| `seo-technical-auditor` | Technical SEO audits (+ audit_score.py) |
| `keyword-research` | Keyword & intent mapping |
| `competitor-seo-analysis` | Competitor gaps |
| `seo-content-writer` | Optimized articles |
| `on-page-seo-optimizer` | Improve existing pages |
| `programmatic-seo` | Data-driven page generation |
| `backlink-strategy` | Link opportunities |
| `geo-ai-search-optimizer` | AI answer/GEO visibility |
| `local-seo` | GBP, local pages, citations |
| `seo-monitoring` | Recurring rank/health watch |
| `seo-data-provider` | DataForSEO/Semrush volumes when keys exist |
| `studio-expert-contract` | Shared senior desk contract (Marketing/SEO/Leads) |

### Marketing
| Skill | Description |
|-------|-------------|
| `digital-marketing` | Full-funnel orchestrator |
| `marketing-strategist` | Positioning, ICP, plan |
| `go-to-market-planner` | Launch planning |
| `growth-marketing` | Experiment loops |
| `campaign-manager` | Multichannel campaigns |
| `social-media-manager` | Native social content |
| `email-marketing` | Newsletters & sequences |
| `paid-ads-manager` | Google/LinkedIn/Meta ads |
| `marketing-analytics` | CAC, ROAS, funnel metrics |
| `customer-persona-builder` | ICP & personas |
| `market-research` | Sourced market studies |
| `competitor-intelligence` | Ongoing competitor watch |
| `conversion-rate-optimization` | Landing/funnel CRO |
| `content-recycler` | Long-form → social assets |
| `video-generation` | AI video via generate_video (Veo/Sora/Hailuo), scripts, ffmpeg |
| `montage-studio` | Project marketing montage: kit, calendar, creatives, lazy HyperFrames |
| `ad-creative-generator` | Complete ad sets: concepts, copy, AI images & videos per platform |
| `product-visuals` | Packshots, lifestyle scenes, e-commerce sets, product videos |

### Writing & copy
| Skill | Description |
|-------|-------------|
| `content-generation` | Content router + QC |
| `professional-writer` | Letters, reports, memos |
| `copywriting-agent` | Sales copy & ads |
| `blog-writer` | Editorial long-form |
| `technical-writer` | Docs & runbooks |
| `proposal-writer` | Client proposals |
| `rfp-writer` | Tenders & compliance |
| `case-study-writer` | Client case studies |
| `email-writer` | One-to-one emails |
| `proofreader` | FR/EN/AR corrections |
| `style-editor` | Tone transformations |
| `translation-localization` | FR/EN/AR localization |
| `fact-checker` | Pre-publication checks |
| `brand-voice-manager` | Per-brand voice cards |

### Sales
| Skill | Description |
|-------|-------------|
| `lead-generation` | ICP prospect lists |
| `lead-prospector` | Expert company/people/contact hunting |
| `buying-signals` | Funding, hiring, tech-change signal scoring |
| `lead-qualification` | BANT-F scoring (+ score_leads.py) |
| `lead-enrichment` | Hunter/Apollo enrichment when keys exist |
| `cold-email-writer` | Outbound emails |
| `outreach-sequencer` | Multi-touch sequences |
| `sales-proposal-writer` | Offers & quotes |
| `discovery-call-assistant` | Call prep & summaries |
| `objection-handler` | AER playbooks, battlecards |
| `crm-update-agent` | HubSpot/Salesforce/files (prefers HubSpot MCP) |
| `pipeline-analyst` | Pipeline health & forecast |
| `account-research` | Pre-meeting account sheets |
| `tender-monitor` | Appels d'offres watch |
| `pricing-assistant` | Pricing scenarios & floors |
| `contract-reviewer` | Clause risk review |
| `meeting-followup` | Minutes, actions, emails |

### Careers & HR
| Skill | Description |
|-------|-------------|
| `career-agent` | Career desk operator (Freelance + Jobs) |
| `cv-builder` | ATS-safe CVs |
| `cv-tailoring` | CV per offer |
| `cover-letter-writer` | Motivation letters |
| `linkedin-optimizer` | Profile drafts (user pastes, no scrape) |
| `ats-analyzer` | CV/offer match scoring |
| `job-search-agent` | Search + watches on authorized sources |
| `application-tracker` | Application pipeline |
| `followup-writer` | J+3 / J+7 relances |
| `interview-coach` | Prep & mock interviews |
| `offer-analyzer` | Written offer accept / negotiate / walk |
| `salary-negotiator` | Salary and package talk tracks |
| `freelance-rate-card` | TJM / daily rate card |
| `career-advisor` | Trajectory advice |

### Documents
| Skill | Description |
|-------|-------------|
| `docx-generator` | Word via python-docx |
| `pdf-generator` | PDF via Chromium / ReportLab |
| `pptx-generator` | PowerPoint via python-pptx |
| `spreadsheet-analyst` | Excel/CSV with pandas |
| `contract-extractor` | Contract data registers |
| `invoice-reader` | Invoice extraction + checks |
| `report-generator` | Recurring report pipelines |
| `presentation-designer` | Deck story & slide plans |
| `archify` | Default architecture / sequence / workflow diagrams (HTML + SVG) |
| `template-manager` | Branded template library |
| `document-templates` | Built-in visual themes + template adaptation |

## Attribution

These skills are adapted from [OpenClaw](https://github.com/openclaw/openclaw)'s skill system
where noted; Navin-specific skills follow the same SKILL.md conventions.
