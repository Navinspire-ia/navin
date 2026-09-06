# Actions menu - full reference

The **Actions** button (checklist icon, next to the project selector) opens the one-click audit and workflow menu of the **Code** module (`#/code`). Each entry builds a precise instruction and sends it to the agent chat automatically.

Actions rely on the matching [slash commands](./commands.md) and [skills](./skills.md). The agent works on the project currently open in the workbench. Composer turn modes (Plan / Agent / Review / Security / Debug) are covered in [Modes](./modes.md).

## Menu options

| Option | Behavior |
| --- | --- |
| **Whole project** | Scope = the entire open project (`the whole project`). |
| **File: …** | Scope = the file currently open in the editor. Disabled when no file is open. |
| **Auto-fix** | Checked: after reporting, the agent applies confirmed fixes and verifies them. Unchecked: it proposes fixes ordered by impact without changing code. |

**Auto-fix** applies to Quality, Security audit, Performance, Design & UX, and Maintenance. Actions in **Offensive & compliance** are read-only (no code changes): reconnaissance, testing, non-destructive PoCs, and reporting.

## Overview

| Group | Actions | Purpose |
| --- | --- | --- |
| [Quality](#quality) | 5 | Review, Debug, tests, quality gate, Run Mobile |
| [Security audit](#security-audit) | 9 | Defensive audits of code and configuration |
| [Offensive & compliance](#offensive--compliance) | 7 | Recon, DAST, red team, pentest, compliance, report |
| [Performance](#performance) | 2 | Hot paths, health metrics |
| [Design & UX](#design--ux) | 3 | UX, visual consistency, accessibility |
| [Maintenance](#maintenance) | 2 | Refactoring, documentation |

Total: **28 actions**.

---

## Quality

| Action | Sends | What happens |
| --- | --- | --- |
| Code review | `/inspect <scope>` | Expert review (Review mode): bugs, SQL/data, API, frontend, security smells, tests, perf - with `code_review`, file/line citations, real examples, `review-report-*.html` (auto File Preview), and a numbered remediation plan. See [Modes](./modes.md) and [Expert tools](./expert-tools.md). |
| Debug | `/debug <scope>` | Reproduce the failure, prove root cause with evidence (`debug_repair` / DebugMCP), close with `debug-report-*.html` (auto File Preview) and numbered fix choices - asks which `#` to start. See [Modes](./modes.md) and [Expert tools](./expert-tools.md). |
| Tests | prompt | Runs the test suite for the scope, reports failures with root causes; with auto-fix, repairs code/tests until the suite passes. |
| Quality gate | prompt | Lint + type checks + tests + quick security scan + performance smells, summarized as a pass/fail scoreboard. |
| Run Mobile | `/mobile android` | Detect Expo / React Native / Flutter, doctor the toolchain, start the packager, then use the Mobile preview tab. See [Mobile](./mobile.md). |

---

## Security audit

**Defensive** audits: code, config, and dependency analysis. Read-only unless **Auto-fix** is checked.

| Action | Sends | What happens |
| --- | --- | --- |
| Security audit | `/fortify <scope>` | Expert AppSec pass (Security mode): `security_scan`, full phase coverage, scanners when present, real PoCs, `security-report-*.html` (auto File Preview), numbered hardening choices. See [Modes](./modes.md) and [Expert tools](./expert-tools.md). |
| Vulnerability scan | `/probe <scope>` | OWASP Top 10 patterns, hardcoded secrets, vulnerable dependencies, SSRF / path traversal, deserialization, prompt injection - with location proof and minimal patch. |
| Secrets scan | `/unmask <scope>` | Keys, tokens, passwords, connection strings in the working tree **and** git history. Prefers gitleaks / trufflehog / detect-secrets; masks values; requires rotation + history purge for each real leak. |
| Supply-chain audit | `/lineage <scope>` | Dependency tree (resolved lockfiles, including transitive): CVEs, outdated / abandoned packages, integrity, typosquatting, dependency confusion, licenses. Prioritized upgrade plan; SBOM if asked. |
| Deep static analysis | `/xray <scope>` | SAST: dangerous sinks (eval, exec, raw SQL, `dangerouslySetInnerHTML`, pickle/yaml load…) with source → sink tracing before declaring a finding. Cites `file:line`, proves the path, rates severity. |
| Access control | `/gatekeeper <scope>` | End-to-end AuthN/AuthZ: password storage, JWT (reject `alg:none`), sessions, MFA, server-side roles, IDOR / BOLA, privilege escalation. |
| API & web surface | `/perimeter <scope>` | Everything exposed (HTTP, GraphQL, websockets, webhooks) against the OWASP API Top 10: object/function authz, mass assignment, CORS, CSRF, headers, rate limiting, SSRF. |
| Infra & IaC | `/bastion <scope>` | Dockerfiles, Kubernetes, Terraform / cloud, CI/CD: root user, `latest` tags, baked secrets, privileged, over-broad IAM, unpinned actions, `pull_request_target` misuse. Prefers trivy / checkov / tfsec / hadolint. |
| Data & privacy | `/vault <scope>` | PII / PHI / financial data flows: encryption in transit and at rest, leaks in logs / telemetry / prompts, retention, deletion, minimization, multi-tenant isolation. GDPR / CCPA / HIPAA / PCI gaps where relevant. |

---

## Offensive & compliance

**Offensive and compliance** workflows, shaped like a pentest cycle. Always **read-only** (no auto-fix): non-destructive PoCs, no exfiltration, strictly in authorized scope.

| Action | Sends | What happens |
| --- | --- | --- |
| Reconnaissance | `/recon <scope>` | Maps the attack surface before any exploitation: routes, GraphQL / websockets, forms, uploads, auth flows, third-party integrations, services / ports, subdomains, tech fingerprint. Delivers an asset inventory and priority targets. |
| Threat model | `/threatmap <scope>` | Decomposition (assets, entry points, deps), trust boundaries, STRIDE per element, ranked threat table, attack paths to hand to `/probe` or `/redteam`. Mermaid diagram welcome. |
| Dynamic testing (DAST) | `/dast <scope>` | Starts or attaches to the target in a sandbox, then probes live: injection, sessions, IDOR, SSRF, XSS / CSRF via a real browser, business-logic abuse. Each finding confirmed with a minimal PoC + request/response evidence. |
| Attack simulation | `/redteam <scope>` | Chains confirmed weaknesses into realistic exploit paths (e.g. SSRF → metadata → IAM). Non-destructive PoCs, detection guidance, and priority fix per chain. |
| Autonomous pentest | `/pentest <scope>` | Full orchestrated cycle: (1) recon, (2) threat model, (3) OWASP scan and beyond, (4) exploitation validated by PoC, (5) report (severity / CVSS, evidence, impact, remediation). May dispatch phases to subagents. |
| Compliance mapping | `/comply <scope>` | Maps against a standard (default OWASP ASVS L2; also CIS, SOC 2, ISO 27001 Annex A, PCI-DSS). Per control: status (met / partial / gap / N-A), evidence, gap, remediation, effort. Scorecard + roadmap. **Readiness gap analysis, not certification.** |
| Pentest report | `/report <scope>` | Compiles findings already gathered (without re-testing): executive summary, scope / methodology, ranked table with CVSS, evidence / PoC, impact, remediation, roadmap. OWASP / compliance mapping. Saved as a workspace file (Markdown by default, or PPTX / DOCX / PDF if asked). |

### Recommended pentest flow

```text
Reconnaissance  →  Threat model  →  Vulnerability scan / DAST
        ↓
Attack simulation  (or  Autonomous pentest  to run everything at once)
        ↓
Compliance mapping  →  Pentest report
```

For a fast pass: **Autonomous pentest** alone, then **Pentest report**.

### Ethical guardrails

These actions instruct the agent to:

- stay **strictly in scope** (open project / provided target);
- use **non-destructive PoCs** only;
- **never** exfiltrate real secrets or data;
- **never** touch out-of-scope or production systems without explicit consent.

You are responsible for auditing only what you are authorized to test.

---

## Performance

| Action | Sends | What happens |
| --- | --- | --- |
| Performance | `/turbo <scope>` | Hot paths, N+1 queries, blocking I/O, caches, bundle size, memory - measured before recommending. Optimizations ordered by impact / effort. |
| Supervision & metrics | `/pulse <scope>` | Project health: complexity, dependency freshness, lint, coverage, TODO debt, KPIs - scored dashboard and top three improvements. |

---

## Design & UX

| Action | Sends | What happens |
| --- | --- | --- |
| UX/UI review | prompt | User flows, navigation clarity, empty / loading / error states, spacing, responsiveness, interaction feedback - ordered by user impact, with the file involved. |
| Design consistency | prompt | Palette, typography scale, component variants, borders / radii / shadows, dark mode, design tokens - inconsistencies plus a unified proposal. |
| Accessibility | prompt | WCAG 2.2 AA: contrast, keyboard navigation, focus, ARIA, form labels, alt texts, screen-reader flow - violations by severity. |

---

## Maintenance

| Action | Sends | What happens |
| --- | --- | --- |
| Refactoring | prompt | Dead code, duplication, oversized functions / components, tangled dependencies, naming - ordered by payoff vs risk. |
| Documentation | prompt | README accuracy, setup instructions, missing docstrings, API docs, outdated sections; with auto-fix, writes or updates the docs directly. |

---

## Actions ↔ slash commands map

| Group | Action | Command / prompt |
| --- | --- | --- |
| Quality | Code review | `/inspect` |
| Quality | Debug | `/debug` |
| Quality | Tests | free-form prompt |
| Quality | Quality gate | free-form prompt |
| Quality | Run Mobile | `/mobile` |
| Security audit | Security audit | `/fortify` |
| Security audit | Vulnerability scan | `/probe` |
| Security audit | Secrets scan | `/unmask` |
| Security audit | Supply-chain audit | `/lineage` |
| Security audit | Deep static analysis | `/xray` |
| Security audit | Access control | `/gatekeeper` |
| Security audit | API & web surface | `/perimeter` |
| Security audit | Infra & IaC | `/bastion` |
| Security audit | Data & privacy | `/vault` |
| Offensive & compliance | Reconnaissance | `/recon` |
| Offensive & compliance | Threat model | `/threatmap` |
| Offensive & compliance | Dynamic testing (DAST) | `/dast` |
| Offensive & compliance | Attack simulation | `/redteam` |
| Offensive & compliance | Autonomous pentest | `/pentest` |
| Offensive & compliance | Compliance mapping | `/comply` |
| Offensive & compliance | Pentest report | `/report` |
| Performance | Performance | `/turbo` |
| Performance | Supervision & metrics | `/pulse` |
| Design & UX | UX/UI review | free-form prompt |
| Design & UX | Design consistency | free-form prompt |
| Design & UX | Accessibility | free-form prompt |
| Maintenance | Refactoring | free-form prompt |
| Maintenance | Documentation | free-form prompt |

Slash commands listed above can also be typed directly in chat. See [Commands](./commands.md).

---

## Examples

### File-scoped review

1. Open `src/App.tsx` in the editor.
2. In **Actions**, switch scope to **File: App.tsx**.
3. Click **Code review**.

The agent receives `/inspect /path/to/project/src/App.tsx` and reviews only that file.

### Project security audit with fixes

1. Scope = **Whole project**.
2. Check **Auto-fix**.
3. Click **Secrets scan** or **Security audit**.

The agent audits, then applies and verifies confirmed fixes.

### Pentest cycle

1. **Reconnaissance** on the project (or a URL in chat via `/recon https://…`).
2. **Threat model**, then **Vulnerability scan** and / or **Dynamic testing**.
3. **Attack simulation** on confirmed findings.
4. **Compliance mapping**, then **Pentest report** to deliver the document.

Or in one click: **Autonomous pentest**, then **Pentest report**.

---

## Related pages

- [Modes](./modes.md) - Plan / Agent / Review / Security / Debug
- [Commands](./commands.md) - full slash-command reference
- [Skills](./skills.md) - security and development skills loaded by the agent
- [Workbench](./workbench.md) - Code module panels
- [Overview](./README.md) - Dev / Code module
