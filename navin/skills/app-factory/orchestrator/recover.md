# Recover

## Inputs

- The failing command, its full log (poll output or `.navin/tool-results`), the target id.

## Procedure

1. Load `app-build-repair` and match the error against its failure table.
2. Fix only inside `os/<id>` (or the toolchain, with consent). Never patch the web project to make a build pass.
3. Toolchain missing or wrong version: back to phase 3 (`appfactory action=doctor`, `ask_user`, install).
4. Re-run only the failed step, then continue the pipeline.
5. Stop after 3 failed attempts on the same error: record the step as `failed` with the error excerpt, propose the next action (CI runner, user input), and call `appfactory action=report` with status reflecting reality.

## Host limits

- MSI on Linux/macOS, iOS or macOS off macOS: not a fix loop. Prepare the CI workflow and report the target as `partial` with "build not executed locally".

## Checkpoint

Board comment on the phase task with the error, the fix and the result. Report step note lists the attempts.
