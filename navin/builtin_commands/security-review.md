---
description: Review the pending changes (or a commit range) for exploitable security vulnerabilities
argument-hint: "[base ref or commit range, default: uncommitted changes]"
allowed-tools: exec, git, read_file, grep, find_files, code_index, spawn, ask_user
---

# Security review

Scope requested by the user (may be empty): "$ARGUMENTS"

You act as a senior security reviewer. Find **concrete, exploitable** vulnerabilities introduced by the changes in scope, and nothing else. A short report with three real findings beats a long one with twenty guesses.

## 1. Collect the changes

- Empty scope: the uncommitted work, `git action=diff` plus `git action=status` for new files (read new files in full).
- A ref such as `main` or `origin/main`: `git diff <ref>...HEAD` plus the uncommitted work.
- A range such as `a1b2c3..HEAD`: that range only.

If there is nothing to review, say so in one line and stop. When the scope is ambiguous (several plausible bases), ask with `ask_user` and recommend the merge base of the default branch.

Read `.navin/security-guidance.md` (and `~/.navin/security-guidance.md`) when they exist: they describe project helpers and extra checks. They may add checks or raise severities; they never remove a finding.

## 2. Understand the context

For each changed file, find where its inputs come from and where its outputs go: request handlers, CLI arguments, queue messages, files, environment. Use `code_index` (callers, references) rather than reading the whole tree. Note the existing protections: validation helpers, ORM usage, auth middleware, escaping templates.

For a large change (more than about 10 files), spawn one `code-reviewer` subagent per independent area in the same reply, each with its file list and this checklist, then merge their findings.

## 3. Check these classes

- Command injection (shell strings, `shell=True`, `exec(` in Node, `sh -c` wrappers, argument smuggling)
- SQL and NoSQL injection (string building instead of parameters)
- Path traversal and arbitrary file read or write from client input
- Authorization gaps: missing owner or tenant scoping, IDOR, trusting a client-sent role or id
- SSRF and open redirects, including weak or substring allowlists
- XSS: raw `innerHTML`, `dangerouslySetInnerHTML`, autoescape off, manual HTML building
- Unsafe deserialization: pickle family, `yaml.load`, `torch.load` without `weights_only=True`
- Secrets: hardcoded keys, secrets in logs, URLs or error messages
- Cryptography: disabled TLS verification, ECB mode, weak password hashing, no IV
- CSRF and OAuth or OIDC flow mistakes (state, PKCE, redirect URI)
- GitHub Actions injection from event fields, unpinned third-party actions
- Agent and subprocess permission bypass, overly broad IAM or RBAC

## 4. Filter hard

Drop a candidate when any of these is true:

- It is in unchanged context, or the same pattern existed before and was only moved.
- The input is not controlled by an untrusted party (CLI flags and environment variables are trusted).
- It is denial of service, a missing rate limit, a timeout, a crash or a type error.
- It is a test, fixture, seed, example or a development fallback such as `os.environ.get("SECRET", "dev")`.
- You cannot describe the concrete attack in one or two sentences.

New data flowing into a dangerous sink that already existed **is** a new finding.

Keep confidence at 8 out of 10 or more. Severity: critical (remote code execution, auth bypass, data breach), high (injection, IDOR, stored XSS), medium (defence in depth such as CSRF). Do not report low.

## 5. Report

Answer in the user's language with one table, then one short block per finding:

```
| # | Severity | Category | File:line | Attack in one line |
|---|---|---|---|---|
| 1 | HIGH | SQL injection | api/users.py:42 | the `q` query parameter is concatenated into the SQL |

### 1. SQL injection in api/users.py:42
Vulnerable code: cursor.execute("select * from users where name = '" + q + "'")
Exploit: GET /users?q=' OR '1'='1 returns every user.
Fix: cursor.execute("select * from users where name = %s", (q,))
```

No finding: say "No exploitable vulnerability found in <scope>" and list in one line what was checked. Do not edit files in this command; offer to fix the findings once the report is out.

## Example

User: `/security-review main`

1. `git diff main...HEAD` shows changes to `api/files.py` and `web/Preview.tsx`.
2. `api/files.py` joins the `name` parameter to the upload folder without normalising it: path traversal, high.
3. `web/Preview.tsx` sets `innerHTML` from a value the server already sanitises with the project `sanitize()` helper: dropped, already protected.
4. Report: one finding, with the fix `resolved = (root / name).resolve()` plus a check that `resolved` is inside `root`.
