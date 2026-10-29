---
name: security-injection
description: Find SQLi, XSS, SSTI, XXE and command injections. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Injection (Navin Security)

## Overview

Find SQL, NoSQL, LDAP, XPath, command, template and XSS injections by tracing untrusted data into interpreters.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: database, web, api.
- Families: injection.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C1.01 | L1 | SQL/NoSQL/LDAP/XPath injection |
| C1.02 | L1 | XSS reflected, stored and DOM |
| C1.03 | L1 | SSTI and template injection |
| C1.04 | L1 | Command injection and RCE |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): semgrep, bandit.
- Other engines named by the catalog: navin_dataflow, navin_rules, sqlmap, zap. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Locate every query builder, shell call, template render and HTML sink; prefer the registered scanners' hits as the starting list.
2. For SQL: string formatting or concatenation into `execute`, ORM raw fragments, dynamic `ORDER BY` and column names.
3. For commands: `shell=True`, `os.system`, `exec`, argument injection through leading dashes, unvalidated filenames.
4. For templates and HTML: server-side template strings built from input, `dangerouslySetInnerHTML`, `innerHTML`, unescaped output.
5. Confirm with a harmless probe on a running target (a quote, a marker string, `{{7*7}}`); never extract data or run a payload that changes state.
6. Write the fix as parameterization or an allow-list, not as a blacklist of characters.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Recommending input sanitizing where parameterization is the fix.
- Running time-based or destructive payloads against a shared environment.
- Treating a clean scan as proof of security: say which controls were not assessed.
