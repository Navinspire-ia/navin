---
name: skill-vetter
description: Audit a skill package before install or enable - permissions, network calls, credential access, obfuscation, and suspicious system commands. Use before trusting ClawHub or third-party SKILL.md files.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Skill Vetter

## Overview

Treat every new skill as untrusted code until reviewed. ClawHub audits help but do **not** guarantee safety. Navin Marketplace featured skills must pass this vetting **and** carry a valid `MARKETPLACE_SIGNING_SECRET` signature before publish.

## Checklist

1. **Frontmatter** - name/description match behavior; refuse vague “does everything” skills
2. **Permissions** - which tools does it instruct the agent to use (`exec`, network, file write)?
3. **Network** - unexpected domains, raw IP calls, data exfil patterns
4. **Credentials** - reads env secrets, writes tokens to chat, logs API keys
5. **Obfuscation** - base64 blobs, eval, hidden scripts, minified one-liners
6. **System commands** - `curl|sh`, privilege escalation, destructive `rm -rf`, reverse shells
7. **Scope creep** - modifies `SOUL.md` / config / other skills without need

## Workflow

1. Open `SKILL.md` + every file under `scripts/`, `references/`, `assets/`.
2. `grep` for: `curl`, `wget`, `eval`, `base64`, `os.environ`, `API_KEY`, `chmod`, `sudo`, `nc `, `powershell`.
3. Score findings:

| Severity | Action |
|----------|--------|
| Blocker | Do not install |
| Major | Fix or reject |
| Minor | Document risk; allow with constraints |

4. Output a short audit report + verdict: **Allow / Allow with constraints / Reject**.

## Rules

- Prefer reading files with `read_file` / `grep` before executing any skill script.
- Never run untrusted `scripts/` during the audit itself.
- If unsure, Reject and ask the user.
