# Phase 1: Detect (Analysis)

## Inputs

- The project root (workspace).
- The requested target from `/apps <target>` or the user's words ("Android app" -> `android`, "Windows app" -> `windows`, "Linux app" -> `ubuntu` then `appimage`). Mobile with no OS named -> `android`. Desktop with no OS named -> `windows`.

## Tool calls

1. `board action=create` one task per phase for this target (Analysis, Compatibility, Tools, Transformation, Build and tests, Delivery) with `depends_on` in order.
2. `appfactory action=targets` to confirm the target id, folder and required host.
3. `appfactory action=analyze`.
4. Load `app-project-analyzer` and confirm the report by reading the cited files (`package.json`, lockfile, framework config, backend entry points, auth setup, `.env.example`).

## Outputs

- Frontend framework + version, package manager, build script, build output dir.
- Backend frameworks and their base URL env vars.
- Server features with file paths (Server Actions, SSR, middleware, route handlers).
- Auth libraries and the auth transport (cookie or bearer token).
- Existing `os/` or native folders (`android/`, `ios/`, `src-tauri/`).

## Exit criteria

- Every item above is filled or explicitly `none`, each backed by a file path.
- If `os/<target>` already exists, the plan will extend it, never overwrite it.

## Checkpoint

- Report step `{step: "analysis", status: "done", note: "<framework> <version>, <pm>, backend <x>"}`.
- Board: move the Analysis task to done with the analyze summary as evidence.
- Journal: "Analyse du projet terminee".
