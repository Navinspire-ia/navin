# Navin Workspace

This directory is your personal working area, not the Navin application
installation directory.

- Open or create your own project from **Dev → Select project**.
- Everything Navin manages lives under `.navin/` so the project tree stays clean:
  - `.navin/skills/` contains only your custom skills; built-in skills are bundled
    with the application.
  - `.navin/checkpoints/` stores local agent checkpoints.
  - `.navin/metadata/` stores generated project roles and dependency metadata.
  - `.navin/memory/` and the `.navin/` Markdown files hold your agent memory and
    preferences.

On Windows, the complete installed application is under:

`%LOCALAPPDATA%\Programs\Navin`

Do not copy application internals into this workspace. Navin keeps application
files and user/project data separate so upgrades cannot overwrite your work.
