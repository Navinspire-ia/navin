# Common Gotchas

## Do not use `ruff format`

Do **not** run `ruff format` — it destroys git blame history. Only `ruff check` should be used.

## Naming / paths

This tree is branded **Navin**:

- Python package: `navin/`
- CLI: `navin`
- Config / runtime data: `~/.navin/`
- Env vars: `NAVIN_*`
- WebUI build output: `navin/web/dist/`
- Brand assets: `webui/public/logo/` (`navin.png`, `navin.svg`)

Do not reintroduce legacy upstream package paths, CLI names, or home-directory defaults.

## WebUI locales

Only **`en`** and **`fr`** are registered (`webui/src/i18n/`). Do not add Chinese or other locale packs unless product explicitly requests them.

## Channels not in this tree

Feishu, WeChat/Weixin, WeCom, DingTalk, QQ, MoChat, and Napcat channel modules were removed. Do not re-add them or their optional extras / WebUI catalog entries without an explicit product decision.

Supported built-ins: Telegram, Discord, Slack, WhatsApp, Matrix, Mattermost, MS Teams, Email, Signal, WebSocket.

## Make targets

Prefer Make for local install/start/stop:

- Backend: root `Makefile` (`make install`, `make start`, `make start-bg`, `make stop`)
- Frontend: `webui/Makefile` (`cd webui && make install && make start`)

Gateway health defaults to `http://127.0.0.1:8765/health`. WebUI Vite defaults to `:5173` and proxies `/api`, `/webui`, `/auth` via `NAVIN_API_URL`.

## Config `${VAR}` References

`config/loader.py` resolves `${VAR}` patterns in `config.json` at load time. This is **not** a shell-like default-value syntax. If the environment variable is missing, `load_config` raises `ValueError` and the agent falls back to default configuration.

Example valid usage:
```json
{ "providers": { "openrouter": { "apiKey": "${OPENROUTER_KEY}" } } }
```

## Windows Compatibility

Navin explicitly supports Windows. Key differences to keep in mind:
- `ExecTool` defaults to PowerShell on Windows (`pwsh` when available, otherwise Windows PowerShell); pass `shell="cmd"` for cmd.exe syntax or cmd built-ins (`shell.py`).
- `cli/commands.py` forces `sys.stdout`/`stderr` to UTF-8 on startup to handle multilingual input.
- MCP stdio server commands are normalized for Windows path separators (`mcp.py`).
- Always use `pathlib.Path` for path manipulation; do not assume `/` separators.

## Prompt Templates

Agent system prompts and scenario-specific instructions live in `navin/templates/` as Jinja2 markdown files (`identity.md`, `platform_policy.md`, `HEARTBEAT.md`, `SOUL.md`, etc.). Changing these files alters agent behavior as directly as changing Python code. They are loaded by `utils/prompt_templates.py`.

Tool descriptions, skills, and replayed session history also shape model behavior. Treat changes to those surfaces like runtime code: keep them narrow, add a focused regression test when possible, and avoid teaching the model to repeat internal markers, local paths, or tool-call text.

## Context Pollution Persists

Anything written into memory, session history, or prompt inputs can be replayed into future LLM calls. Metadata such as timestamps, local media paths, tool-call echoes, and raw fallback dumps must be bounded and sanitized before they become examples for the model to imitate.

## Skills as Extension Point

Built-in skills live in `navin/skills/` (markdown + YAML frontmatter format). Agent capabilities that are "know-how" rather than code should be added as skills, not hardcoded into the agent loop. External skills can be published to and installed from ClawHub.

## Atomic Session Writes

`agent/memory.py` writes `history.jsonl` atomically (temp file + fsync + rename + directory fsync). This guarantees durability across crashes. Do not replace this with a plain `open(..., "w")` write.
