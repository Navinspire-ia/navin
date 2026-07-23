This file provides guidance to AI coding agents working with this repository.

## Project Overview

Navin is a lightweight, open-source AI agent framework written in Python with a React/TypeScript WebUI. It centers around a small agent loop that receives messages from chat channels, invokes an LLM provider, executes tools, and manages session memory.

- Package / import: `navin`
- CLI: `navin`
- Config / data: `~/.navin/`
- Env prefix: `NAVIN_*`
- WebUI locales: **English** and **French** only

## Development Commands

```bash
# Backend (repo root) — preferred
make install          # create .venv + pip install -e ".[dev]"
make start            # navin gateway (foreground, :8765)
make start-bg         # gateway in background
make stop             # stop gateway
make status / logs / lint

# Frontend
cd webui && make install
cd webui && make start        # Vite :5173 (proxies API to :8765)
cd webui && make stop
cd webui && make build        # → ../navin/web/dist

# Direct equivalents
ruff check navin/
cd webui && bun run dev       # or NAVIN_API_URL=... bun/npm run dev
navin gateway
```

## High-Level Architecture

### Core Data Flow

Messages flow through an async `MessageBus` (`navin/bus/queue.py`) that decouples chat channels from the agent core:

1. **Channels** (`navin/channels/`) receive messages from external platforms and publish `InboundMessage` events to the bus.
2. **`AgentLoop`** (`navin/agent/loop.py`) consumes inbound messages, builds context, and coordinates the turn.
3. **`AgentRunner`** (`navin/agent/runner.py`) handles the actual LLM conversation loop: send messages to the provider, receive tool calls, execute tools, and stream responses.
4. Responses are published as `OutboundMessage` events back to the appropriate channel.

### Key Subsystems

- **Agent Loop** (`navin/agent/loop.py`, `runner.py`): The core processing engine. `AgentLoop` manages session keys, hooks, and context building. `AgentRunner` executes the multi-turn LLM conversation with tool execution.
- **LLM Providers** (`navin/providers/`): Provider implementations (Anthropic, OpenAI-compatible, OpenAI Responses API, Azure, Bedrock, GitHub Copilot, OpenAI Codex, etc.) built on a common base (`base.py`). Includes image generation (`image_generation.py`) and audio transcription (`transcription.py`). `factory.py` and `registry.py` handle instantiation and model discovery.
- **Channels** (`navin/channels/`): Platform integrations (Telegram, Discord, Slack, Matrix, WhatsApp, Email, MS Teams, WebSocket, Mattermost, Signal). Channels are auto-discovered via `pkgutil` scan + entry-point plugins (`navin.channels`). Chinese platform channels (Feishu, WeChat/Weixin, WeCom, DingTalk, QQ, MoChat, Napcat) are **not** supported in this tree.
- **Tools** (`navin/agent/tools/`): Agent capabilities exposed to the LLM: filesystem (read/write/edit/list), shell execution (with sandbox backends), web search/fetch, MCP servers, cron, notebook editing, subagent spawning, long-running tasks / sustained goals (`long_task.py`), image generation, and self-modification. Tools are auto-discovered via `pkgutil` scan + entry-point plugins.
- **Memory** (`navin/agent/memory.py`): Session history persistence with Dream two-phase memory consolidation. Uses atomic writes with fsync for durability.
- **Session Management** (`navin/session/`): Per-session history, context compaction, TTL-based auto-compaction (`manager.py`), and sustained goal state tracking (`goal_state.py`).
- **Config** (`navin/config/schema.py`, `loader.py`): Pydantic-based configuration loaded from `~/.navin/config.json`. Supports camelCase aliases for JSON compatibility.
- **WebUI** (`webui/`): Vite-based React SPA that talks to the gateway over a WebSocket multiplex protocol. The dev server proxies `/api`, `/webui`, `/auth` to the gateway. Brand assets live in `webui/public/logo/`.
- **API Server** (`navin/api/server.py`): OpenAI-compatible HTTP API (`/v1/chat/completions`, `/v1/models`) for programmatic access.
- **Command Router** (`navin/command/`): Slash command routing and built-in command handlers.
- **Heartbeat** (`navin/templates/HEARTBEAT.md`): Periodic task list checked via `cron` jobs (legacy dedicated service removed).
- **Pairing** (`navin/pairing/`): DM sender approval store with persistent pairing codes per channel.
- **Skills** (`navin/skills/`): Built-in skill definitions (cron, github, image-generation, etc.) loaded into agent context.
- **Security** (`navin/security/`): PTH file guard and other security measures activated at CLI entry.

### Entry Points

- **CLI**: `navin/cli/commands.py` (script entry: `navin`)
- **Python SDK**: `navin/navin.py` (facade export `Navin`)

## Project-Specific Notes

- Architecture constraints: [`.agent/design.md`](.agent/design.md)
- Security boundaries: [`.agent/security.md`](.agent/security.md)
- Common gotchas: [`.agent/gotchas.md`](.agent/gotchas.md)

## Code Style

- Python 3.11+, asyncio throughout.
- Line length: 100.
- Linting: `ruff` with rules E, F, I, N, W (E501 ignored). Do **not** run `ruff format`.

## Common File Locations

- Config schema: `navin/config/schema.py`
- Provider base / new provider template: `navin/providers/base.py`
- Channel base / new channel template: `navin/channels/base.py`
- Tool registry: `navin/agent/tools/registry.py`
- WebUI i18n: `webui/src/i18n/` (`en`, `fr` only)
- WebUI brand: `webui/public/logo/navin.png`, `navin.svg`
- WebUI dev proxy config: `webui/vite.config.ts`
- Backend Makefile: `Makefile`
- Frontend Makefile: `webui/Makefile`
