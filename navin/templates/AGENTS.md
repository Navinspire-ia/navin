# Agent Instructions

## Workspace Guidance

Use this file for project-specific preferences, recurring workflow conventions, and instructions you want the agent to remember for this workspace. Keep durable facts about the user in `.navin/USER.md`, personality/style guidance in `.navin/SOUL.md`, and long-term memory in `.navin/memory/MEMORY.md`.

## Project Continuity

This project carries a `.navin/` continuity pack shared by every module (Code, Documents, Marketing, SEO, ...):

- Before starting substantial work, check `.navin/continuity/RESUME.md` and the board (`board` tool) to see where the project stands.
- Hard rules live in `.navin/memory/MEMORY.md` under `## Constraints` - never violate them without an explicit user request.
- After a significant work session, update `.navin/continuity/RESUME.md` (state, risks, next action) and append durable choices to `.navin/continuity/DECISIONS.md`.

## Product quality (always)

- No Unicode em dash (U+2014) or en dash (U+2013) in code, UI copy, i18n, docs, or comments. Use `-` or rephrase.
- Web UI work must lock one official design system (Google MUI, Microsoft Fluent, or IBM Carbon; ask if missing), load skills `ui-ux-pro-max` and `make-interfaces-feel-better`, install/use `framer-motion` plus `three` + `@react-three/fiber` + `@react-three/drei` on every kit (designed 3D layer, not wallpaper), and ship working controls (no stub buttons / blank dashboards). Never default to Tailwind / shadcn / a homemade kit.
- Architecture, diagrams, PPT visuals, tender/RFP technical answers, and Markdown plans/explanations use skill `archify` by default (HTML + SVG, not a Mermaid dump).
- Do not mark an app done until Preview shows a working happy path and `verify` is clean.

## Scheduled Reminders

- Before scheduling reminders, check available skills and follow skill guidance first.
- Use the built-in `cron` tool to create/list/remove jobs (do not call `navin cron` via `exec`).
- Get USER_ID and CHANNEL from the current session (e.g., `8281248569` and `telegram` from `telegram:8281248569`).
- Cron jobs run as scheduled turns in the origin chat/session and normally deliver the result back to that channel. Do not use cron for background checks that should stay silent when there is nothing useful to report; use `.navin/HEARTBEAT.md` instead.

**Do NOT just write reminders to MEMORY.md** - that won't trigger actual notifications.

## Heartbeat Tasks

`.navin/HEARTBEAT.md` is checked periodically by the protected heartbeat cron job that `navin gateway` registers when `gateway.heartbeat.enabled` is true. Do not create a duplicate heartbeat job unless the user has disabled the built-in one and explicitly wants a custom schedule.

- Use `apply_patch` for normal task-list updates, especially when adding, removing, or changing multiple lines.
- Use `edit_file` only for small exact replacements copied from the current `.navin/HEARTBEAT.md`.
- Use `write_file` for first creation or intentional full-file rewrites.

When the user asks for a recurring/periodic heartbeat task, or for a periodic background check that should only notify on actionable changes, update `.navin/HEARTBEAT.md` instead of creating a one-time reminder. Use the built-in `cron` tool for explicit reminders, scheduled tasks that should report every run, or custom schedules that should not be part of the heartbeat task list.
