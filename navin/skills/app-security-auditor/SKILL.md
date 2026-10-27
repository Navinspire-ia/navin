---
name: app-security-auditor
description: App Factory security gate before delivery or publishing of a mobile/desktop app - Android permissions in AndroidManifest, Tauri capabilities and CSP, secrets scan, dependency audit (npm audit, cargo audit), signing key handling, TLS-only API.
metadata: {"navin":{"emoji":"🛡️","category":"development"}}
---

# App Security Auditor

## When to use

Phase 5, after a successful build and before `appfactory action=report` or any store upload.

## Inputs

- `os/<target>`, artifact path.

## Procedure

0. Run `appfactory action=audit target=<id>` first: it applies `navin/skills/app-factory/policies/security.yaml` (remote URL wrapper, cleartext, forbidden or reason-needed Android permissions, Tauri capabilities and CSP, HTTPS backend, secret files and secret patterns in `os/<id>`). Every `error` blocks the delivery; every `warning` needs a feature that justifies it, written in the report. The checks below go deeper by hand.
1. Android permissions: read `os/android/android/app/src/main/AndroidManifest.xml`; every `uses-permission` must map to a used plugin. Flag `READ_EXTERNAL_STORAGE`, `MANAGE_EXTERNAL_STORAGE`, `SYSTEM_ALERT_WINDOW`, `REQUEST_INSTALL_PACKAGES` without need. Check `android:usesCleartextTraffic` is absent or `false` and `android:debuggable` is not set in release.
2. Tauri: `src-tauri/capabilities/*.json` minimal (no wildcard fs scope, no unscoped `shell:allow-execute`); `app.security.csp` set without `unsafe-eval`; `devtools` off in release.
3. Secrets scan: `grep` for keys/tokens in `os/<target>` and the built web dist (`sk_live`, `AKIA`, `-----BEGIN`, `api_key=`); keystore, `.jks`, `.p12`, `.key` must not be tracked (`git action=status`, `.gitignore`). Use `secrets-scanner` when available.
4. Dependency audit: `npm audit --omit=dev` (`pnpm audit`, `yarn npm audit`, `bun audit`), `cargo install cargo-audit --locked` then `cargo audit` in `src-tauri`.
5. Signing keys: only from env or a secret store (`TAURI_SIGNING_PRIVATE_KEY`, `ANDROID_KEYSTORE_PASSWORD`), never printed in logs.
6. TLS: every API base URL `https://`; no `http://` in the dist (`grep -r "http://" <dist>` excluding `localhost` and XML namespaces).

## Verification

Each check recorded as a report test (`passed` / `failed` / `not_run` with evidence).

## Failure modes and fixes

- High/critical vulnerabilities: upgrade in `os/<target>` only; site dependencies are reported, not changed.
- Secret in the dist: move it to the backend; a frontend secret is public.

## Deliverable

Security findings list; blocking findings prevent the `delivered` status.
