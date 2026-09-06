# Slash commands - full reference

Navin ships built-in slash commands. Type `/` in any chat composer to open the command palette. Commands fall into three lifecycles:

- **Agent workflow** - rewrites your message into a mission brief (with the right skills preloaded) and runs a normal agent turn.
- **Side channel** - answers immediately without consuming an agent turn.
- **Turn control** - manages the current chat/turn itself.

Most security / quality workflows are also available from the Code module [Actions menu](./actions.md). Composer turn modes (Plan / Agent / Review / Security / Debug) are documented in [Modes](./modes.md).

## Agent workflows

### Build & quality

| Command | Title | Arguments | What it does |
| --- | --- | --- | --- |
| `/blueprint` | Plan mode | `[task]` | Designs an implementation plan and mission Task Ledger before writing any code: constraints, facts, missing info, acceptance criteria, ordered steps with validation. No code changes. Sets composer mode to Plan. See [Plan Mode](./plan-mode.md). |
| `/forge` | Build mode | `[task]` | Full autonomous build: Progress Ledger loop (claim → work → validate with evidence → next), local replan on stall. Sets composer mode to Agent. |
| `/cruise` | Autopilot build | `[task]` | Plan, execute, test, detect stalls/loops, replan locally until done or paused - no Build wait. Sets composer mode to Agent. |
| `/mission` | Long mission | `[goal]` | Durable multi-turn mission with ledger, checkpoints, resume, sub-agents, and a final report. Sets composer mode to Agent. |
| `/mobile` | Run Mobile | `[android\|ios\|web\|metro\|doctor]` | Detect Expo / React Native / Flutter, doctor the toolchain, start the packager (and optional Android emulator), then use the Mobile preview tab to see and interact with the device. |
| `/atlas` | Project atlas | `[init\|refresh\|query <question>]` | Builds or refreshes `.metadata/` (role, kind, and dependencies of every file) powering the Dev workbench Graph tab, and answers "where is X?" questions from the index instead of grepping. |
| `/inspect` | Code review | `[path\|diff\|scope]` | Expert review (Review mode): correctness, SQL/data, API, frontend, security smells, tests, perf, maintainability. Tools: `code_review` (scope/filter/report). Each finding needs `file:line`, impact, fix, and a real example. Closes with `review-report-*.html` (auto File Preview) and a numbered remediation plan - asks which `#` to start. Optional `pr_comments` (PR + `gh`). Read-only unless asked to fix. |
| `/debug` | Debug mode | `[signal\|path\|scope]` | Reproduce the failure, prove root cause with real evidence (stack/logs/test/DebugMCP). Tools: `debug_repair` (mcp_status/start_branch/report). Closes with `debug-report-*.html` (auto File Preview) and numbered fix choices. Asks which `#` to start. Applies code only if asked. |
| `/turbo` | Performance audit | `[path\|scope]` | Profiles and optimizes: hot paths, N+1 queries, blocking I/O, missing caches, bundle size, memory. Measures before recommending. |
| `/pulse` | Metrics & analytics | `[focus]` | Project health scoreboard: code size/complexity, dependency freshness, lint findings, coverage, TODO debt, KPIs. |
| `/board` | Task board | `[task <id>\|loop\|status\|plan <goal>]` | Works the shared project board: plan, take tasks, dispatch, and report. |
| `/goal` | Long-running goal | `<goal>` | Registers the request as a sustained goal the agent keeps pursuing across turns until completed or stopped. |

### Security audit (defensive)

| Command | Title | Arguments | What it does |
| --- | --- | --- | --- |
| `/fortify` | Security review | `[path\|scope]` | Expert AppSec pass (Security mode): surface map, `security_scan`, host scanners when present, injection/data, frontend, authz, network, supply chain, privacy, LLM/agent. Each finding needs severity, proof, real PoC, minimal fix. Closes with `security-report-*.html` (auto File Preview) and numbered hardening choices - asks which `#` to start. Optional `pr_comments`. Read-only unless asked to fix. |
| `/probe` | Vulnerability scan | `[path\|scope]` | Hunts exploitable weaknesses: OWASP Top 10, hardcoded secrets, vulnerable dependency versions, SSRF/path traversal, prompt injection. |
| `/unmask` | Secrets scan | `[path\|scope]` | Leaked keys, tokens and passwords in code, config and git history. |
| `/lineage` | Supply-chain audit | `[path\|scope]` | Dependencies: CVEs, outdated packages, lockfile integrity, licenses. |
| `/xray` | Deep static analysis | `[path\|scope]` | SAST: traces user input to dangerous sinks across the code. |
| `/gatekeeper` | Access control audit | `[path\|scope]` | Authentication and authorization: sessions, tokens, roles, IDOR. |
| `/perimeter` | API & web surface | `[path\|scope]` | Exposed endpoints: authz, CORS, CSRF, SSRF, headers, rate limits. |
| `/bastion` | Infra & IaC audit | `[path\|scope]` | Docker, Kubernetes, Terraform, CI/CD and cloud configuration. |
| `/vault` | Data & privacy audit | `[path\|scope]` | PII handling, encryption, logging leaks, retention, GDPR gaps. |

### Offensive & compliance

| Command | Title | Arguments | What it does |
| --- | --- | --- | --- |
| `/recon` | Reconnaissance | `[path\|target]` | Maps the attack surface: routes, services, subdomains, tech fingerprint. |
| `/threatmap` | Threat model | `[path\|scope]` | Attack surface, trust boundaries and STRIDE threats with mitigations. |
| `/dast` | Dynamic testing | `[target\|scope]` | Runs the app and probes live: injection, auth, IDOR, SSRF, XSS/CSRF. |
| `/redteam` | Attack simulation | `[path\|scope]` | Chains weaknesses into realistic exploit paths with safe proof-of-concept. |
| `/pentest` | Full autonomous pentest | `[target\|scope]` | End-to-end cycle: recon, threat model, scan, exploit with PoC, validated report. |
| `/comply` | Compliance mapping | `[framework\|scope]` | Gap analysis vs OWASP ASVS, CIS, SOC 2, ISO 27001, PCI-DSS. |
| `/report` | Pentest report | `[format\|scope]` | Compiles validated findings into a shareable, compliance-ready report. |

### Studios

| Command | Title | Arguments | What it does |
| --- | --- | --- | --- |
| `/studio` | Document studio | `[format + brief]` | Creates polished PowerPoint, Word, PDF, or Excel documents. See [navin_contenant](../../navin_contenant/README.md). |
| `/campaign` | Marketing studio | `[brief]` | Generates campaigns end to end: ad copy, social posts, product images, video scripts. See [navin_marketing](../../navin_marketing/README.md). |
| `/seo` | SEO studio | `[url\|topic]` | Full SEO: technical audits, keyword research, optimized content, competitor analysis. See [navin_seo](../../navin_seo/README.md). |
| `/leads` | Leads & sales studio | `[icp\|company\|brief]` | Desk hunt (open data first), BANT-F, Start loop, heartbeat watch. See [navin_leads](../../navin_leads/README.md), [loop](../../navin_leads/en/loop.md), [desktop](../../navin_leads/en/desktop.md). |
All workflow commands accept an optional focus/target. Without one, the agent infers the most useful scope itself.

## Utilities (side channel)

| Command | Arguments | What it does |
| --- | --- | --- |
| `/checkpoint` | `[save [note]\|list\|restore <name> [all\|chat\|code]\|delete <name>]` | Checkpoints are auto-saved before each prompt. Rewind the conversation, the code, or both. They complement git, not replace it. |
| `/pilot` | `[task]` | Manual model-per-task switch. Tasks: `search`, `plan`, `review`, `security`, `dev`, `fast`, `deep`, `docs`. Assign each role a **named preset** in **Settings → Models → Task routing** (`modelRoutes`). Workflows (`/forge`, `/blueprint`, audits, studios…) apply those routes automatically; `/pilot review` also switches the session preset. Without argument, shows the current mapping. |
| `/pack` | `[list\|install <git-url\|path>\|enable <name>\|disable <name>\|remove <name>]` | Manages plugin packs (skills + MCP servers). See [Plugins](./plugins.md). |
| `/model` | `[preset]` | Shows or switches the active model preset. |
| `/status` | - | Runtime, provider, and channel status (including web-search quota when available). |
| `/skill` | - | Lists all enabled skills available to the agent. |
| `/help` | - | Lists available slash commands. |
| `/history` | `[n]` | Prints the last N persisted conversation messages. |
| `/trigger` | `<name>` | Creates a named CLI trigger bound to this chat session. |
| `/pairing` | `[list\|approve <code>\|deny <code>\|revoke <user_id>]` | Manages DM pairing requests per channel. |
| `/restart` | - | Restarts the Navin process. |

## Memory (Dream)

| Command | Arguments | What it does |
| --- | --- | --- |
| `/dream` | - | Manually triggers the two-phase memory consolidation. |
| `/dream-log` | - | Shows what the last Dream consolidation changed. |
| `/dream-restore` | - | Reverts memory to a previous Dream snapshot. |
| `/dream-prompt` | `[init]` | Customizes how Dream organizes this workspace's memory. |
| `/evaluator-prompt` | `[init]` | Customizes the heartbeat notification gate prompt. |

## Turn control

| Command | What it does |
| --- | --- |
| `/new` | Resets this chat and starts a fresh conversation (finalizes the active turn first). |
| `/stop` | Cancels the active agent turn for this chat. |

## Tips

- Composer modes: pick Plan / Agent / Review / Security / Debug in the Mode menu - free text is prefixed automatically. See [Modes](./modes.md).
- Combine with the file target: after opening a file in the editor, the **Actions** menu can scope any command to that file (e.g. `/inspect src/App.tsx`).
- Chain workflows: `/blueprint` first, review the plan, then `/forge`. Use `/cruise` for autopilot and `/mission` for long durable work (see [Plan Mode](./plan-mode.md)).
- After Review / Security / Debug: open the HTML report in File Preview, reply `Start with #1` (or another number), then Agent / `/forge`.
- Pentest cycle: `/recon` → `/threatmap` → `/probe` or `/dast` → `/redteam` → `/comply` → `/report` (or `/pentest` to run the whole cycle).
- Task routing is automatic for workflows (`/forge` → `dev`, `/blueprint` → `plan`, `/inspect` → `review`, `/fortify` → `security`, `/debug` → `deep`, …). Use `/pilot <task>` only for free-form chat when you want to stay on a role.
- `/checkpoint save before-refactor` before risky operations, `/checkpoint restore before-refactor code` to roll back files only.
- Full menu reference: [Actions](./actions.md).
