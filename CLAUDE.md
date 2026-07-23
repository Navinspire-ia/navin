@AGENTS.md

# Claude / coding agents

This repository is **Navin** (package `navin`, CLI `navin`, config `~/.navin`).

Before changing architecture, security-sensitive code, or contribution flow, read:

- [`AGENTS.md`](./AGENTS.md) — project map, commands, subsystems
- [`.agent/design.md`](./.agent/design.md) — architectural constraints
- [`.agent/security.md`](./.agent/security.md) — security boundaries
- [`.agent/gotchas.md`](./.agent/gotchas.md) — common pitfalls

Prefer Make targets for local runs:

```bash
# Backend (repo root)
make install && make start-bg

# Frontend
cd webui && make install && make start
```
