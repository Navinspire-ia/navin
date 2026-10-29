---
name: security-mobile
description: Audit Android, iOS and Capacitor. Loaded by the Navin Security router for the matching stack or findings; also usable on its own.
metadata: {"navin":{"emoji":"🛡️","category":"security"}}
---

# Mobile (Navin Security)

## Overview

Audit Android, iOS and Capacitor apps against the mobile verification standard.
Default posture is read-only and non-destructive: report evidence and a proposed fix, never exploit, extract data or change the target.

## When it applies

- Triggers: mobile.
- Families: mobile_desktop.

## Controls covered

| Control | Level | Check |
|---|---|---|
| C5.01 | L1 | Local storage: tokens, databases, preferences, keychain/keystore |
| C5.02 | L1 | Transport: TLS, pinning, cleartext traffic |
| C5.03 | L1 | IPC, intents, deep links and URL schemes |
| C5.04 | L1 | WebView: JavaScript, injection, local files |
| C5.05 | L1 | Declared permissions versus real usage |
| C5.06 | L1 | Secrets embedded in binaries |
| C5.09 | L1 | Code protection: debug, obfuscation, root/jailbreak |
| C5.10 | L1 | Sensitive client-side logs and screenshots |

## Engines

- Registered scanners (use `navin.security.scanners`, results arrive as normalized findings): gitleaks.
- Other engines named by the catalog: jadx, mobsf, navin_rules. Use one only when it is installed.
- A scanner that is not installed or did not run means the check was not done, not that it passed.

## Method

1. Storage: tokens and personal data in preferences, files, databases; keychain or keystore use.
2. Network: cleartext traffic allowed, certificate pinning, debug trust managers.
3. Platform: exported components, intent filters, deep links and URL schemes, WebView settings and JavaScript bridges.
4. Permissions declared against permissions used; backups allowed; debuggable flags in release builds.
5. Secrets and keys embedded in the binary or in web assets; scan the unpacked build with the registered secret scanner.
6. Logs and screenshots that expose sensitive data.

## Evidence and status

- `potential`: a rule or a suspicion with static evidence only.
- `confirmed`: a dataflow trace from a controlled source to the sink, or a dynamic observation on the running target.
- `exploitable`: a non-destructive reproduction showed the impact, in an authorized environment.
- Every finding carries file and line or endpoint, the evidence, impact, a concrete fix and the control ids above.

## Anti-patterns

- Auditing a debug build and reporting its debug flags as release issues.
- Skipping the web layer of a Capacitor app.
- Treating a clean scan as proof of security: say which controls were not assessed.
