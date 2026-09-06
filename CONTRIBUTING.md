# Contributing

Thanks for wanting to improve Navin.

## Before you start

1. Read [Installation](./docs/Installation.md) (source build) and [docs/README.md](./docs/README.md).
2. Run `navin doctor` (or `.venv/bin/navin doctor`) after `make install`.
3. Keep user data out of issues: no API keys, tokens, or `config.json` dumps.

## Local loop

```bash
make install
make -C webui install
sh scripts/start.sh
# gateway: http://127.0.0.1:8765
# WebUI:   http://127.0.0.1:5173
```

```bash
.venv/bin/navin-cli
make logs
sh scripts/stop.sh
```

The product CLI is `navin-cli`. Do not document `navin tui` or `navin agent` as the entry point.

## What we look for

- Agent runtime, CLI, Skills, MCP, providers
- Memory, world model, policy learning, evaluations
- Integrations, UI, documentation, bug fixes

Match existing style. Do not invent extra CLI surface or rewrite `--help` unless the task is the help text itself.

## Docs and copy

- No Unicode em dash (U+2014) or en dash (U+2013). Use `-` or rephrase.
- User-facing install commands must stay the official ones:

```bash
curl https://navin.live/install -fsS | bash
```

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

## Pull requests

- Small, reviewable diffs.
- Say why, not only what.
- If you change UI behavior, say how you verified it.
- If you change install or CLI docs, keep [docs/Installation.md](./docs/Installation.md) and [docs/cli/install.md](./docs/cli/install.md) in sync.

Open the PR against the branch the maintainers use for this repository.
