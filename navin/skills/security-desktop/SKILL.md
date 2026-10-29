---
name: security-desktop
description: Audit Tauri, Rust, IPC and permissions. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Desktop (Navin Security)

## Overview

Audit Tauri and Electron apps: IPC surface, permissions, content security and updates.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: desktop.
- Families: mobile_desktop.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C5.07 | L1 | Tauri/Electron: allowlist, CSP, exposed commands, isolation |
| C5.08 | L1 | Updates: signature, channel, rollback |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): cargo_audit.
- Other engines named by the catalog: navin_desktop, navin_rules. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Tauri: capabilities and allowlist narrow, commands validate their arguments, no arbitrary shell or filesystem scope.
2. Electron: `contextIsolation` on, `nodeIntegration` off, preload exposes narrow functions, `webSecurity` not disabled.
3. CSP on the app origin; remote content never loaded into a window with privileged IPC.
4. Updates: signed, over TLS, channel pinned, rollback possible.
5. Rust dependencies: run the registered advisory scanner on the lockfile.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Reviewing only the web code of a desktop app.
- Calling an IPC command safe because the frontend only passes known values.
- Treating a clean scan as proof of security: say which controls were not assessed.
