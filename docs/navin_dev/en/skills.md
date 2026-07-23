# Dev skills

Skills are markdown playbooks the agent loads on demand (or automatically via workflow commands). Manage them in **Settings → Skills** or list them with `/skill`. The skills below power the Dev module; the full catalog contains many more (marketing, SEO, documents, HR, sales…).

## Engineering

| Skill | Purpose |
| --- | --- |
| `fullstack-dev` | End-to-end feature building: frontend, backend, database. |
| `api-engineer` | API design and implementation. |
| `task-planner` | Breaking work into ordered, verifiable steps (used by `/blueprint`). |
| `test-generator` | Writing meaningful test suites. |
| `code-reviewer` | Structured code review methodology (used by `/inspect`). |
| `critic-reviewer` | Adversarial second-pass review. |
| `skill-creator` / `skill-vetter` | Author and audit new skills. |
| `pack-builder` | Create and audit plugin packs. |

## Security

| Skill | Purpose |
| --- | --- |
| `security-auditor` | Systematic security audits (used by `/fortify`). |
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
| `model-router` | Picking the right model per task (pairs with `/pilot`). |
| `context-compressor` | Keeping long sessions within context. |
| `self-healing-retry` | Recovering from failed steps. |
| `proactive-agent` | Anticipating needs during long goals. |
| `human-approval` | Pausing for human sign-off on sensitive steps. |
| `memory` | Long-term memory conventions (pairs with Dream). |

Skills are loaded automatically by workflow commands (e.g. `/fortify` preloads `security-auditor`, `permission-guard`, `secrets-manager`) or on request: just ask the agent to "use the code-reviewer skill".
