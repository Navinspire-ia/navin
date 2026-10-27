# Phases 2 and 3: Plan (Compatibility diagnosis and tool preparation)

## Inputs

- The analyze report from phase 1.
- The target id.

## Phase 2: Compatibility diagnosis

1. `appfactory action=plan target=<id>`.
2. Load `app-compatibility-engine` and check the strategy:
   - `static`: SPA (Vite, CRA, Angular, Vue, Svelte) built as is.
   - `next-export`: Next.js without server features, static export configured only in `os/<id>`.
   - `client-shell`: Next.js with Server Actions, SSR, middleware or route handlers; adapted client frontend in `os/<id>/web`.
3. Read the risks (auth cookies across origins, CORS, absolute asset paths, mixed content) and keep them for phase 4.

Exit: strategy chosen with reasons citing files; ordered steps known.
Checkpoint: report step `compatibility`; journal "Compatibilite du frontend verifiee".

## Phase 3: Tool preparation

1. `appfactory action=doctor target=<id>`.
2. Load `app-environment-manager`.
3. If tools are missing: `ask_user` with options "Install now (recommended)", "Use a CI runner", "Stop". Never install without consent.
4. On consent, run each official install command from doctor with `exec` (long ones with `background=true`, polled with `write_stdin`).
5. Re-run `appfactory action=doctor target=<id>` until every required tool is present.
6. Host incompatible (windows target on Linux, ios/macos off macOS): propose the CI runner path (`app-release-manager` / `cicd-agent`) and continue the local work up to the build.

Exit: doctor green, or CI path agreed.
Checkpoint: report step `tools`; journal "Outils de compilation prets" (never name the tools to the client).
