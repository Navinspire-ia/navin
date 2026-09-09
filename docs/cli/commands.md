# Commands

`navin -h` and `navin <command> -h` print the live flags.

`navin` with no arguments prints help. `navin <folder>` opens that project (`navin .`).

| Flag | Meaning |
|---|---|
| `-c` / `--config` | `config.json` (default `~/.navin/config.json`) |
| `-w` / `--workspace` | Workspace directory |

## `navin agi`

The AGI panel of the Code workbench, from the terminal. All off by default. A locked switch unlocks by itself once the stage below has passed its exam.

```bash
navin agi status                 # ladder + switches, drafts, jobs
navin agi on                     # skills evolution (navin agi off to stop)
navin agi set promote_project off
navin agi memory on --episodes on --recall on
navin agi drafts
navin agi draft fix-apply-patch -d "Read the file before patching" --now
navin agi run
navin agi guard
navin agi publish <name>
```

```bash
navin agi world status
navin agi world on               # journal + train; advice stays off
navin agi world train
navin agi world exam
navin agi world ab
navin agi world set advise on
navin agi world beliefs
navin agi world rollback
```

```bash
navin agi policy status
navin agi policy on              # refused until the world model exam is up
navin agi policy train
navin agi policy exam
navin agi policy ab
navin agi policy set steer on
navin agi policy rollback
navin agi policy publish --yes
```

```bash
navin agi transfer status
navin agi transfer protocol
navin agi transfer on            # refused until skills, world model and policy exams have passed
navin agi transfer freeze --author a --attester c
navin agi transfer campaign
navin agi transfer safety
navin agi transfer kill-drill
```

Every subcommand takes `-p/--project <folder>` (default: current directory). Full page: [`navin agi`](./agi.md).

## `navin-cli`

Terminal UI in the current folder.

```bash
cd your-project
navin-cli
navin-cli ~/code/app
navin-cli -s review
```

From a `navin` clone: `.venv/bin/navin-cli` (Windows: `.venv\Scripts\navin-cli`).

| Flag | Meaning |
|---|---|
| `[path]` | Project folder (default `.`) |
| `-s` / `--session` | Session id |
| `-w` / `--workspace` | Workspace the tools see |
| `-c` / `--config` | Config file |

Keys: [navin-cli](./interactive.md). Settings: **Ctrl+G**.

## `navin onboard`

Create or refresh `~/.navin/config.json` and the workspace.

```bash
navin onboard
navin onboard --refresh
```

## `navin doctor`

What this install can do. Exit 1 if a required check fails.

## `navin status`

Config path, workspace, active model, which providers have a key or a local endpoint.

## `navin install-cli`

Put `navin` and `navin-cli` on PATH. `--force` replaces a stale shim.

## `navin cache`

Cache sizes. `--clear` drops regenerable data. Never deletes config, chats, or workspaces.

## `navin provider`

OAuth only. Hosted API keys are set in Settings.

```bash
navin provider login openai_codex
navin provider login github_copilot
navin provider logout openai_codex
```
