# Phase 4: Execute (Transformation)

## Inputs

- Strategy and steps from `appfactory action=plan target=<id>`.
- App name, app id (reverse DNS, for example `com.acme.shop`) and backend URL. Derive name from `package.json`; ask only if nothing usable exists.

## Tool calls

1. Git: `git action=status`. If the user wants a dedicated branch, ask first (`ask_user`); never switch branches silently.
2. `appfactory action=scaffold target=<id> app_name=<n> app_id=<id> backend_url=<url>`. It refuses to overwrite; if files exist, edit them in place instead.
3. Frontend (`app-frontend-adapter`):
   - `static` / `next-export`: build config inside `os/<id>` points at the web build output, relative base path.
   - `client-shell`: create `os/<id>/web` with the client pages, API client using the base URL env var.
4. Backend (`app-backend-bridge`): list the CORS origins and cookie settings the backend needs; propose the patch to the user, never apply backend changes silently.
5. Runtime:
   - Mobile: `app-capacitor-builder` (install, `npx cap add android`, sync).
   - Desktop: `app-tauri-builder` (`npx tauri init` in `os/<id>`, `frontendDist`, `beforeBuildCommand`, capabilities).
6. Native features (`app-native-capabilities`): only those the app uses, minimal permissions.
7. Mobile adaptation: safe areas, 44px touch targets, keyboard, back button, permission prompts in context.

## Outputs

- `os/<id>` populated; web project untouched (`git action=diff` shows no change outside `os/<id>` and `.navin/appfactory`, except agreed backend patches).

## Exit criteria

- Dev run works (`npx cap run android` or `npx tauri dev`) or the failure is handed to `recover.md`.

## Checkpoint

Report step `transformation`; board task done with the diff stat as evidence; journal "Application preparee dans os/<id>".
