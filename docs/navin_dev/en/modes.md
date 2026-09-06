# Composer modes

The chat composer has a **Mode** menu (left of the model picker). It controls the agent posture for the next turn: design, build, review, security audit, debug, or montage. Investigate modes tint the composer shell with a matching accent color.

When you type free text (no leading `/`), Plan / Review / Security / Debug / Montage **prefix** your message with the matching slash workflow so the agent loads the right brief, skills, and tools. Agent mode prefixes `/forge`. Explicit slash commands are never double-prefixed.

The agent can also switch the UI mode itself via the `set_composer_mode` tool (WebSocket event `composer_mode_request`), so the menu and accent stay in sync when a workflow starts or when the agent hands off.

## Quick reference

| Mode | Free-text slash | Model route role | Posture | Primary deliverable |
| --- | --- | --- | --- | --- |
| **Plan** | `/blueprint` | `plan` | Design only - no code edits | Implementation plan + handoff to Build |
| **Agent** | (none; `/forge` / `/cruise` / `/mission` / `/mobile` map here) | `dev` (mission → `deep`) | Implement, test, iterate | Working code + verification |
| **Review** | `/inspect` | `review` | Expert code review (read-only by default) | `review-report-*.html` + numbered remediation choices |
| **Security** | `/fortify` | `security` | Expert AppSec audit (read-only by default) | `security-report-*.html` + numbered hardening choices |
| **Debug** | `/debug` | `deep` | Reproduce and prove root cause | `debug-report-*.html` + numbered fix choices |
| **Montage** | `/montage` | `docs` | Project marketing kit, calendar, creatives | `marketing/montage/` + `montage-report-*.html` |

The model routing role for `/debug` is `deep`; the composer UI mode is `debug` (`set_composer_mode(mode=debug)`). Montage uses `docs` and `set_composer_mode(mode=montage)`.

Related slash families also sync the UI mode when typed explicitly:

| UI mode | Also maps from |
| --- | --- |
| Plan | `/board` |
| Agent | `/forge`, `/cruise`, `/mission`, `/mobile` |
| Review | `/inspect`, `/turbo` |
| Security | `/fortify`, `/probe`, `/unmask`, `/lineage`, `/xray`, `/gatekeeper`, `/perimeter`, `/bastion`, `/vault`, `/recon`, `/threatmap`, `/dast`, `/redteam`, `/pentest`, `/comply` |
| Debug | `/debug` |
| Montage | `/montage` |

Full command tables: [Commands](./commands.md). One-click audits: [Actions](./actions.md).

---

## Plan

**When to use:** you want a design before any code lands - constraints, options, chosen approach, ordered steps.

**How to start:**

- Pick **Plan** in the Mode menu and describe the task in free text, or
- Type `/blueprint <task>` (also available from Actions / task routing).

**What the agent does:**

1. Calls `set_composer_mode(mode=plan)` so the UI shows Plan.
2. Clarifies goals, constraints, and risks.
3. Produces a concrete step plan and mission Task Ledger (no file edits unless you explicitly ask).
4. Hands off to Build: switch to Agent or run `/forge` on the agreed plan.

Full ledger details: [Plan Mode](./plan-mode.md).

**Model routing:** Task routing role `plan` (configure under **Settings → Models → Task routing**).

**Tips:**

- Chain: `/blueprint` first, review the plan, then `/forge`.
- Autopilot without waiting for Build: `/cruise`.
- Long multi-session work: `/mission`.
- `/board` also keeps the composer in Plan when you are working the shared project board.

---

## Agent

**When to use:** implement features, fix bugs you already understand, run tests, and iterate until done.

**How to start:**

- Leave Mode on **Agent** (default) and chat normally, or
- Type `/forge <task>` for the full autonomous build brief, or `/mobile …` for mobile run/preview.

**What the agent does:**

1. Plans as needed, then edits code with tools.
2. Runs lint / typecheck / tests when scripts exist.
3. Iterates on failures until the goal is met or blocked.
4. Can call `set_composer_mode(mode=agent)` when leaving Plan / investigate modes.

**Model routing:** role `dev` for `/forge` (and related build workflows).

**Tips:**

- Prefer Plan → Agent for large or ambiguous work.
- After Review / Security / Debug, pick a remediation number (`Start with #1`) then switch to Agent or `/forge` to implement.

---

## Review

**When to use:** expert code review of a diff, path, or whole project - deeper than a casual glance.

**How to start:**

- Mode **Review** + free text, or `/inspect [path|diff|scope]`, or Actions → **Code review**.

**Scope:** `git status` + staged/unstaged/recent diff, or a named path. Uses metagraph for hot paths when helpful.

**Evidence tools:** `read_file`, ripgrep, `exec` for lint/typecheck/tests (`ruff`, `eslint`, `tsc`, `mypy`, `pytest`, …).

**Layers covered:**

| Layer | Focus |
| --- | --- |
| A. Correctness | Logic bugs, null/undefined, error handling, races, async hazards |
| B. Data & SQL | Raw SQL, missing parameterization, N+1, transactions, migrations, ORM misuse |
| C. API contracts | Shapes, authz on handlers, mass assignment, breaking changes |
| D. Frontend | XSS sinks, CSRF, client-only auth checks, form validation gaps |
| E. Security smells | Injection, secrets, insecure crypto, path traversal |
| F. Tests & quality | Missing/broken/flaky tests; quality gate when scripts exist |
| G. Performance | Hot loops, unbounded queries, missing indexes/pagination |
| H. Maintainability | Dead code, god objects, naming, dead deps |

**Each finding must include:**

- Severity (Critical / High / Medium / Low / Info)
- `file:line` (or hunk range)
- Impact
- Concrete fix
- A **real example** - vulnerable/buggy code excerpt, failing test output, or small PoC (not a generic checklist blurb)

**Close:**

1. Call `code_review(action=report, …)` - writes `review-report-[YYYYMMDD-HHMMSS].html` and **opens File Preview automatically** in the WebUI (`preview_opened=true`).
2. In chat: short summary + **Ask which remediation number to start with** (`Start with #1`, `#2`, …).
3. Read-only unless you asked for Auto-fix / fix everything.
4. Optional: `pr_comments(preview)` then `pr_comments(post, kind=review, …)` when a GitHub PR is open (`gh` required).

**Dedicated tools:** `code_review` (`scope` / `filter` / `report`). Details: [Expert tools](./expert-tools.md).

**Model routing:** role `review`.

**Verdict:** Approve or Request changes, plus a numbered remediation plan (effort S/M/L, risk if delayed, first concrete step).

---

## Security

**When to use:** elite AppSec + defensive (and related offensive) workflows with evidence from source to sink.

**How to start:**

- Mode **Security** + free text → runs `/fortify`, or
- Type `/fortify [path|scope]`, or Actions → **Security audit**, or any security-family slash (`/probe`, `/xray`, `/pentest`, …).

**`/fortify` phases (all covered; say clean with evidence when empty):**

1. **Surface map** - languages, lockfiles, routes, GraphQL/WS/webhooks, auth, DB/SQL, forms, uploads, jobs, IaC/Docker/K8s, CI secrets, admin panels
2. **Scanners** (when present) - gitleaks/trufflehog/detect-secrets; npm audit/pip-audit/osv-scanner/cargo audit/govulncheck; bandit/semgrep; trivy/checkov/tfsec/kube-linter/hadolint; sqlfluff
3. **Injection & data** - SQL/NoSQL/ORM, command/LDAP/XPath/template injection, path traversal, XXE, unsafe deserialization, SSRF, header/host injection
4. **Frontend & client** - XSS, `dangerouslySetInnerHTML` / `innerHTML`, open redirects, CSRF, CSP, clickjacking, postMessage, prototype pollution, client-only authz (Playwright when a running UI helps)
5. **AuthN / AuthZ** - password hashing, JWT `alg:none`/weak secrets, sessions, MFA, IDOR/BOLA, privilege escalation, mass assignment
6. **Network & transport** - TLS, HSTS, CORS, security headers, rate limits, websocket auth, webhook signatures, exposed admin/debug ports, open SG/firewall in IaC
7. **Supply chain & secrets** - lockfile CVEs, typosquatting, leaked keys in tree/history, secrets in logs
8. **Privacy & compliance** - PII flows, encryption, retention, tenant isolation, GDPR/CCPA/PCI gaps
9. **LLM / agent** (if relevant) - prompt injection, tool over-scope, sandbox escape

**Each finding:** severity, `file:line` or scanner proof, impact, **real PoC** (payload / curl / code), minimal fix.

**Close:**

1. `security_scan(…, write_report=true)` writes `security-report-[YYYYMMDD-HHMMSS].html` and **opens File Preview automatically** in the WebUI.
2. Numbered hardening choices; ask which `#` to start.
3. Read-only unless you asked to fix.
4. Optional: `pr_comments(preview)` then `pr_comments(post, kind=security, …)` on an open PR.

**Dedicated tools:** `security_scan` (kinds `secrets|sast|sca|quick|full`). Details: [Expert tools](./expert-tools.md).

**Model routing:** role `security` for `/fortify` and most audit/offensive workflows.

**Ethics:** stay in authorized scope; non-destructive PoCs; never exfiltrate real secrets. You are responsible for testing only what you are allowed to audit.

---

## Debug

**When to use:** something is broken and you need root cause with proof - not speculative shotgun patches.

**How to start:**

- Mode **Debug** + free text, or `/debug [signal|path|scope]`, or Actions → **Debug** (when available).

**Process:**

1. **Lock the signal** - exact error, failing test, stack, status code, bad field/query, or repro steps. If none: git status/diff, recent logs, package scripts.
2. **Reproduce** with `exec` - failing test, typecheck, lint, or broken command. Capture stdout/stderr/exit codes. For SQL/data: schema/queries/migrations. For API: handler → validation → DB. For frontend: props/state → network → server.
3. **Inspect** - `debug_repair(action=mcp_status)`: if DebugMCP is up (preset `debugmcp` → `http://127.0.0.1:3001/mcp`), use breakpoints / variables / evaluate; else logs / `pdb`. Real evidence only.
4. **Isolate** - `debug_repair(action=start_branch)` before any code edits.
5. **Narrow** - `read_file`, git blame/diff, logs, metrics; smallest instrumentation that proves the cause. Watch races, bad caching, wrong env, flaky tests, N+1, connection leaks, timeout/retry storms.
6. **Board** - file each confirmed defect (`status=fix`) when the project board is in use.
7. **Close** with `debug_repair(action=report, payload_json=…)`: writes `debug-report-[YYYYMMDD-HHMMSS].html` and **opens File Preview automatically** (root cause + evidence, latent bugs, `#N` choices). Ask which `#` to start.

**Apply code only if asked;** otherwise the agent may switch to Agent (`set_composer_mode(mode=agent)`) and point to `/forge` after you choose.

**Dedicated tools:** `debug_repair`. Details: [Expert tools](./expert-tools.md).

**Model routing:** role `deep` (UI mode `debug`).

---

## Montage

**When to use:** you imported a project and want a marketing push plan - kit, day-by-day calendar, social images/videos - without packaging HyperFrames in the Navin install.

**How to start:**

- Mode **Montage** + free text, or `/montage [brief]`, or Marketing studio card **Project montage**.

**Process:**

1. `set_composer_mode(mode=montage)`.
2. **Live demo (preferred for product showcases):** `open_preview` / navigate → `browser(record_start)` → drive the happy path (live Agent browser) → `record_stop` → `montage(demo_register)` → captions `.srt` → `montage(package)` for **9:16 / 1:1 / 16:9** under `marketing/montage/exports/`.
3. `montage(action=analyze)` → `marketing/montage/project-kit.md`.
4. Propose a calendar (`montage(action=calendar)`) and wait for validation before expensive AI video batches.
5. Stills/creatives via browser screenshots + `generate_image` / `generate_video`.
6. HyperFrames: `doctor` → lazy `setup` → `render`. Fallback: AI clips + ffmpeg.
7. Deliver under `marketing/montage/` + `montage-report-*.html`. **Never auto-publish.**

**Dedicated tools:** `montage`, `browser` (`record_start` / `record_stop`). Skills: `montage-studio`, `playwright-browser`.

**Model routing:** role `docs` (UI mode `montage`).

**Module docs:** [Montage studio](../../navin_montage/en/README.md) (actions, FFmpeg/HyperFrames/Remotion packages, profiles).

---

## Agent autonomy and UI sync

| Mechanism | Role |
| --- | --- |
| Mode menu | You pick Plan / Agent / Review / Security / Debug / Montage; choice is persisted for the chat |
| Free-text prefix | Non-Agent modes prepend `/blueprint`, `/inspect`, `/fortify`, `/debug`, or `/montage` |
| Slash → mode | Sending a known workflow slash updates the menu + accent to match |
| `set_composer_mode` | Agent tool; emits `composer_mode_request` over WebSocket |
| Workflow start | Starting `/inspect`, `/fortify`, `/debug`, `/montage`, `/blueprint`, `/forge`, … also requests the matching UI mode |

The tool does not change mid-turn tools by itself - it updates what you see in the composer so the accent matches the posture.

---

## HTML reports (Review / Security / Debug)

Investigate modes **must** close with a polished, self-contained HTML report (skill `studio-html-report`):

| Mission | File name pattern |
| --- | --- |
| Review | `review-report-[YYYYMMDD-HHMMSS].html` |
| Security | `security-report-[YYYYMMDD-HHMMSS].html` |
| Debug | `debug-report-[YYYYMMDD-HHMMSS].html` |

**Report contents:**

1. Header (mission, scope, timestamp)
2. Executive summary with severity counters
3. Finding cards - severity chip, location, impact, **real example**, fix
4. **Remediation plan** - numbered choices (`#1`, `#2`, …) with effort, risk if delayed, first step
5. Deliverables table (HTML + any CSV/MD/XLSX/JSON alongside)
6. Footer

**Rules:**

- All CSS inline; no external fonts, CDNs, or JavaScript (File Preview sandboxes scripts)
- The `code_review` / `security_scan` / `debug_repair` tools **open File Preview automatically** in the WebUI - do not paste the full HTML into chat
- In chat: 3-6 sentences + ask which plan item to start
- Do not auto-start fixes until you pick a number (unless Auto-fix / "fix everything")

Same HTML pattern is used by several studios (RiskLens, SEO, Marketing, Leads, Scraping) under different file names - see the skill for the full naming table. Tool reference: [Expert tools](./expert-tools.md).

---

## Recommended flows

```text
Plan  →  (approve plan)  →  Agent /forge
```

```text
Review or Security or Debug
        ↓
  HTML report in File Preview
        ↓
  You: "Start with #1"  (or #2, …)
        ↓
  Agent /forge implements that item
```

```text
Security family (optional depth):
  /recon → /threatmap → /probe or /dast → /redteam → /comply → /report
  (or /pentest for the full cycle; /fortify for the deep defensive pass)
```

---

## Related pages

- [Expert tools](./expert-tools.md) - `code_review`, `security_scan`, `debug_repair`, `pr_comments`, DebugMCP
- [Commands](./commands.md) - full slash-command reference
- [Actions](./actions.md) - one-click audits in the Code module
- [Skills](./skills.md) - skills preloaded by workflows
- [Workbench](./workbench.md) - panels and File Preview
- [Overview](./README.md) - Dev / Code module
