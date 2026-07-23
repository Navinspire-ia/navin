# Slash commands — full reference

Navin ships 32 built-in slash commands. Type `/` in any chat composer to open the command palette. Commands fall into three lifecycles:

- **Agent workflow** — rewrites your message into a mission brief (with the right skills preloaded) and runs a normal agent turn.
- **Side channel** — answers immediately without consuming an agent turn.
- **Turn control** — manages the current chat/turn itself.

## Agent workflows

| Command | Title | Arguments | What it does |
| --- | --- | --- | --- |
| `/blueprint` | Plan mode | `[task]` | Designs an implementation plan before writing any code: constraints, options, chosen approach, step list. No code changes. |
| `/forge` | Build mode | `[task]` | Full autonomous build mode: plan, code, run, test, and iterate until done. |
| `/atlas` | Project atlas | `[init\|refresh\|query <question>]` | Builds or refreshes `.metadata/` (role, kind, and dependencies of every file) powering the Dev workbench Graph tab, and answers "where is X?" questions from the index instead of grepping. |
| `/inspect` | Code review | `[path\|diff\|scope]` | Reviews recent changes or a target for bugs, behavioral regressions, security issues, missing tests, and maintainability. Cites files and lines. |
| `/fortify` | Security review | `[path\|scope]` | Security audit: auth flows, input validation, injection surfaces, secrets handling, unsafe defaults, dependency risks. Findings rated critical→low. |
| `/probe` | Vulnerability scan | `[path\|scope]` | Hunts exploitable weaknesses: OWASP Top 10, hardcoded secrets, vulnerable dependency versions, SSRF/path traversal, prompt injection. |
| `/turbo` | Performance audit | `[path\|scope]` | Profiles and optimizes: hot paths, N+1 queries, blocking I/O, missing caches, bundle size, memory. Measures before recommending. |
| `/pulse` | Metrics & analytics | `[focus]` | Project health scoreboard: code size/complexity, dependency freshness, lint findings, coverage, TODO debt, KPIs. |
| `/studio` | Document studio | `[format + brief]` | Creates polished PowerPoint, Word, PDF, or Excel documents. See [navin_contenant](../../navin_contenant/README.md). |
| `/campaign` | Marketing studio | `[brief]` | Generates campaigns end to end: ad copy, social posts, product images, video scripts. See [navin_marketing](../../navin_marketing/README.md). |
| `/seo` | SEO studio | `[url\|topic]` | Full SEO: technical audits, keyword research, optimized content, competitor analysis. See [navin_seo](../../navin_seo/README.md). |
| `/leads` | Leads & sales studio | `[icp\|company\|brief]` | Expert prospecting: company/people/job search, buying signals, scoring, outreach. See [navin_leads](../../navin_leads/README.md). |
| `/team` | Team studio | `[mission\|org brief]` | Org design, role sheets, RACI, hiring, OKRs — and virtual AI teams via subagents. See [navin_equipe](../../navin_equipe/README.md). |
| `/goal` | Long-running goal | `<goal>` | Registers the request as a sustained goal the agent keeps pursuing across turns until completed or stopped. |

All workflow commands accept an optional focus/target. Without one, the agent infers the most useful scope itself.

## Utilities (side channel)

| Command | Arguments | What it does |
| --- | --- | --- |
| `/checkpoint` | `[save [note]\|list\|restore <name> [all\|chat\|code]\|delete <name>]` | Checkpoints are auto-saved before each prompt. Rewind the conversation, the code, or both. They complement git, not replace it. |
| `/pilot` | `[task]` | Model-per-task routing. Tasks: `search`, `plan`, `review`, `security`, `dev`, `fast`, `deep`, `docs`. Define presets with those names in **Settings → Models**; `/pilot review` then switches to the `review` preset. Without argument, shows the current mapping. |
| `/pack` | `[list\|install <git-url\|path>\|enable <name>\|disable <name>\|remove <name>]` | Manages plugin packs (skills + MCP servers). See [Plugins](./plugins.md). |
| `/model` | `[preset]` | Shows or switches the active model preset. |
| `/status` | — | Runtime, provider, and channel status (including web-search quota when available). |
| `/skill` | — | Lists all enabled skills available to the agent. |
| `/help` | — | Lists available slash commands. |
| `/history` | `[n]` | Prints the last N persisted conversation messages. |
| `/trigger` | `<name>` | Creates a named CLI trigger bound to this chat session. |
| `/pairing` | `[list\|approve <code>\|deny <code>\|revoke <user_id>]` | Manages DM pairing requests per channel. |
| `/restart` | — | Restarts the Navin process. |

## Memory (Dream)

| Command | Arguments | What it does |
| --- | --- | --- |
| `/dream` | — | Manually triggers the two-phase memory consolidation. |
| `/dream-log` | — | Shows what the last Dream consolidation changed. |
| `/dream-restore` | — | Reverts memory to a previous Dream snapshot. |
| `/dream-prompt` | `[init]` | Customizes how Dream organizes this workspace's memory. |
| `/evaluator-prompt` | `[init]` | Customizes the heartbeat notification gate prompt. |

## Turn control

| Command | What it does |
| --- | --- |
| `/new` | Resets this chat and starts a fresh conversation (finalizes the active turn first). |
| `/stop` | Cancels the active agent turn for this chat. |

## Tips

- Combine with the file target: after opening a file in the editor, the **Actions** menu can scope any command to that file (e.g. `/inspect src/App.tsx`).
- Chain workflows: `/blueprint` first, review the plan, then `/forge` to execute it.
- Use `/pilot deep` before a hard investigation, `/pilot fast` for quick chores.
- `/checkpoint save before-refactor` before risky operations, `/checkpoint restore before-refactor code` to roll back files only.
