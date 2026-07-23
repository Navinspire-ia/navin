# Actions menu

The **Actions** button (checklist icon, next to the project selector) opens a menu of one-click audits and fixes. Each action builds a precise instruction and sends it to the agent chat automatically.

## Options

- **Scope** — apply to the **whole project** or only the **active file** (the file currently open in the editor).
- **Auto-fix** — when checked, the agent doesn't stop at reporting: it applies the confirmed fixes and verifies them. When unchecked, it proposes fixes ordered by impact without touching the code.

## Available actions

### Quality

| Action | Sends | What happens |
| --- | --- | --- |
| Code review | `/inspect <scope>` | Full review: bugs, regressions, security issues, missing tests, maintainability — with file/line citations. |
| Tests | prompt | Runs the test suite for the scope, reports failures with root causes; with auto-fix, repairs code/tests until the suite passes. |
| Quality gate | prompt | Lint + type checks + tests + quick security scan + performance smells, summarized as a pass/fail scoreboard. |

### Security

| Action | Sends | What happens |
| --- | --- | --- |
| Security audit | `/fortify <scope>` | Auth, input validation, injections, secrets, unsafe defaults, dependency risks — rated critical→low. |
| Vulnerabilities | `/probe <scope>` | OWASP Top 10 patterns, hardcoded secrets, vulnerable dependencies, SSRF/path traversal, prompt injection. |

### Performance

| Action | Sends | What happens |
| --- | --- | --- |
| Performance | `/turbo <scope>` | Hot paths, N+1 queries, blocking I/O, caches, bundle size, memory — measured before recommended. |
| Monitoring | `/pulse <scope>` | Health metrics: complexity, dependency freshness, lint findings, coverage, debt — scored dashboard. |

### Design & UX

| Action | What happens |
| --- | --- |
| UX/UI audit | User flows, navigation clarity, empty/loading/error states, spacing, responsiveness, interaction feedback — ordered by user impact. |
| Visual design | Palette, typography scale, component variants, borders/radii/shadows, dark mode, design tokens — inconsistencies with a unified proposal. |
| Accessibility | WCAG 2.2 AA: contrast, keyboard navigation, focus management, ARIA, form labels, alt texts, screen-reader flow — violations by severity. |

### Maintenance

| Action | What happens |
| --- | --- |
| Refactoring | Dead code, duplication, oversized functions/components, tangled dependencies, naming — ordered by payoff vs risk. |
| Documentation | README accuracy, setup instructions, missing docstrings, API docs, outdated sections; with auto-fix, writes the missing docs directly. |

## File-scoped example

Open `src/App.tsx` in the editor, switch scope to **file**, click **Code review** → the agent receives `/inspect /path/to/project/src/App.tsx` and reviews only that file.
