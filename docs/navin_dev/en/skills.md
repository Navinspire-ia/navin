# Dev skills

Skills are markdown playbooks the agent loads on demand (or automatically via workflow commands). Manage them in **Settings → Skills** or list them with `/skill`. The skills below power the Dev module; the full catalog contains many more (marketing, SEO, documents, HR, sales…).

## Engineering

| Skill | Purpose |
| --- | --- |
| `fullstack-dev` | End-to-end feature building: frontend, backend, database. Web UI defaults: one official DS (MUI / Fluent / Carbon, `ask_user` if missing) + `ui-ux-pro-max` + `framer-motion`. |
| `ui-ux-pro-max` | Tokens / landings mapped onto MUI, Fluent, or Carbon + mandatory `framer-motion`. Never Tailwind as the default. Used by `/forge` `/cruise` `/mission` `/blueprint`. |
| `make-interfaces-feel-better` | UI polish: motion, radius, shadows, typography details (pairs with `ui-ux-pro-max`). |
| `mobile-dev` | Expo / React Native / Flutter: detect, doctor, run, preview, tap/swipe, fix redbox (used by `/mobile`). |
| `api-engineer` | API design and implementation. |
| `task-planner` | Breaking work into ordered, verifiable steps (used by `/blueprint`). |
| `test-generator` | Writing meaningful test suites. |
| `code-reviewer` | Structured code review methodology (used by `/inspect` + `code_review` tool). |
| `critic-reviewer` | Adversarial second-pass review. |
| `skill-creator` / `skill-vetter` | Author and audit new skills. |
| `pack-builder` | Create and audit plugin packs. |
| `debug-live` | Evidence-first debug: DebugMCP, isolated branch, `debug_repair`, HTML report (used by `/debug`). |
| `studio-html-report` | Expert / studio HTML report contract (auto File Preview for Review/Security/Debug). |

## Security

| Skill | Purpose |
| --- | --- |
| `security-auditor` | Systematic security audits (used by `/fortify` + `security_scan` tool). |
| `vulnerability-scanner` | Exploit hunting: OWASP, CVEs, secrets (used by `/probe`). |
| `prompt-injection-defender` | Detecting and defusing prompt-injection surfaces. |
| `secrets-manager` | Safe handling of credentials and tokens. |
| `permission-guard` | Enforcing permission boundaries. |
| `audit-logger` | Traceability of sensitive operations. |

## Performance & quality

| Skill | Purpose |
| --- | --- |
| `performance-auditor` | Profiling and optimization (used by `/turbo`). |
| `metrics-analyst` | Project metrics collection and scoring (used by `/pulse`). |
| `kpi-reporter` | KPI dashboards and reporting. |
| `quality-gate` | Multi-gate pass/fail assessment. |

## Ops & infrastructure

| Skill | Purpose |
| --- | --- |
| `docker-operator` | Containers: build, run, debug. |
| `kubernetes-operator` | K8s deployments and troubleshooting. |
| `terraform-agent` | Infrastructure as code. |
| `cicd-agent` | Pipelines and continuous delivery. |
| `observability-agent` | Logs, traces, metrics wiring. |
| `backup-rollback` | Safe state snapshots and restores. |
| `shell-sandbox` | Sandboxed shell execution practices. |
| `tmux` | Long-running terminal session management. |

## Data

| Skill | Purpose |
| --- | --- |
| `database-explorer` | Schema discovery and querying. |
| `sql-analyst` | SQL analysis and optimization. |
| `supabase-operator` | Supabase projects: DB, auth, storage. |
| `stripe-operator` | Stripe integration and operations. |
| `data-migration-agent` | Safe schema/data migrations. |
| `data-quality-agent` | Data validation and cleaning. |

## Agent autonomy

| Skill | Purpose |
| --- | --- |
| `multi-agent-orchestration` | Coordinating subagents on a task. |
| `adaptive-reasoning` | Choosing the right depth of reasoning. |
| `model-router` | Picking the right model per task (auto via Task routing + `/pilot`). |
| `context-compressor` | Keeping long sessions within context. |
| `self-healing-retry` | Recovering from failed steps. |
| `proactive-agent` | Anticipating needs during long goals. |
| `human-approval` | Pausing for human sign-off on sensitive steps. |
| `memory` | Long-term memory conventions (pairs with Dream). |

Skills are loaded automatically by workflow commands (e.g. `/fortify` preloads `security-auditor`, `permission-guard`, `secrets-manager`) or on request: just ask the agent to "use the code-reviewer skill".

Agent tools behind Review / Security / Debug: [Expert tools](./expert-tools.md).
