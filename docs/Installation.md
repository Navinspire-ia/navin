# Installation

This page is the complete install guide: official packages, the CLI one-liner, and a source build of the gateway + WebUI.

Related:

- First session in the terminal: [CLI quickstart](./cli/quickstart.md)
- One-liner only: [CLI install](./cli/install.md)
- Product map: [Capabilities](./capabilities.md)
- Site: [navin.live/download](https://navin.live/download) · [navin.live/en/docs](https://navin.live/en/docs)

After any official install you should have two commands:

| Command | Role |
| --- | --- |
| `navin-cli` | Terminal AGI in the current folder (this is the product CLI) |
| `navin` | Desktop / workbench, doctor, status, gateway, `navin .` |

Do not use `navin tui` or `navin agent` as the entry point.

## 1. Desktop packages (recommended)

Download from [navin.live/download](https://navin.live/download).

| Platform | Files | Notes |
| --- | --- | --- |
| Windows | `.exe` setup or `.msi` | If SmartScreen appears: More info → Run anyway |
| macOS | `.dmg` (arm64 or x64) | Drag Navin into Applications. If Gatekeeper blocks: right-click → Open |
| Linux | `.AppImage`, `.deb`, `.rpm`, `.pkg.tar.zst` | AppImage may need `libfuse2` on older distros |

User data stays in `~/.navin` (or `%USERPROFILE%\.navin` on Windows) across upgrades: config, workspaces, memory, projects.

If `navin` or `navin-cli` is missing from PATH after a DMG or portable install:

```bash
navin install-cli
navin install-cli --force
```

Then:

```bash
cd your-project
navin-cli
navin .
navin --version
navin doctor
```

## 2. Official CLI one-liner

The script reads the official `releases.json`. It prefers a CLI archive when published, otherwise it extracts the desktop package. It writes `navin` and `navin-cli` to the user PATH. No sudo on Linux. Default prefix: `~/.local`.

Linux, macOS, WSL:

```bash
curl https://navin.live/install -fsS | bash
```

Windows PowerShell:

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

```bash
cd your-project
navin-cli
navin --version
navin doctor
navin status
```

Linux arm64: use [navin.live/download](https://navin.live/download) until a CLI tarball is listed.

Environment overrides:

| Variable | Meaning |
| --- | --- |
| `NAVIN_DOWNLOAD_BASE` | Alternate download root |
| `NAVIN_SITE` | Site used by the script (default `https://navin.live`) |
| `NAVIN_PREFIX` | Install prefix (default `~/.local`) |

### Local site (developers)

When the marketing site runs on this repo (`localhost:3100`) and you have prepared local archives:

```bash
make local-releases
curl http://localhost:3100/install -fsS | bash
```

`/install` then serves files from this repository and does not hit production S3.

## 3. First configuration

In `navin-cli`, **Ctrl+G → Providers**: add a key (OpenRouter, OpenAI, Anthropic, …) or a local `apiBase` (Ollama `http://127.0.0.1:11434/v1`). Then **Models**: add a configuration and set it active.

Or:

```bash
navin onboard
```

Env keys (`OPENROUTER_API_KEY`, …) are honored and are not written to `config.json`.

This tree is BYOK only. Add keys in **Ctrl+G → Providers**. There is no Navin managed provider and no navin.live account on `main`.

Config file: `~/.navin/config.json`. Same file for `navin-cli` and the desktop.

## 4. Build from source (gateway + WebUI)

Use this when you clone the repository and want to run or change Navin locally.

### Prerequisites

- Python 3.11 or newer
- Make
- Git
- Node.js + npm (WebUI)
- Linux / macOS: [rustup](https://rustup.rs) if you need the native sandbox (`make native`)

### One command

From the repository root:

```bash
sh scripts/start.sh --install
```

This creates `.venv`, installs the backend in editable mode, installs WebUI dependencies, then starts:

- the gateway (WebUI + WebSocket, default `http://127.0.0.1:8765`)
- the Vite dev server (`http://127.0.0.1:5173`)

Stop:

```bash
sh scripts/stop.sh
```

### Step by step

```bash
# backend
make install

# native helpers (Linux / macOS)
make native

# frontend
make -C webui install
```

Start:

```bash
sh scripts/start.sh              # gateway + Vite, background
sh scripts/start.sh --backend    # gateway only
sh scripts/start.sh --front      # Vite only
sh scripts/start.sh --fg         # gateway in the foreground
```

Make equivalents:

```bash
make start-bg
make status
make logs
make -C webui start-bg
make -C webui logs
```

Or without Make, from an activated venv:

```bash
python -m pip install -e ".[dev]"
navin gateway
```

```bash
cd webui
npm ci
npm run dev
```

Vite proxies `/api`, `/webui`, `/auth` and the WebSocket to the gateway. If the gateway is not on 8765:

```bash
NAVIN_API_URL=http://127.0.0.1:8766 npm run dev
```

### Use the source CLI

From the `navin-agi` clone, after install, you can launch the CLI with the venv binary. No need to activate the venv:

```bash
cd /path/to/navin-agi
.venv/bin/navin-cli
```

Windows:

```powershell
cd \path\to\navin-agi
.venv\Scripts\navin-cli
```

Or activate the venv, then run it from any project folder:

```bash
source .venv/bin/activate   # Windows: .venv\Scripts\activate
cd /path/to/your-project
navin-cli
navin doctor
```

### Ports

| Role | Default | Config |
| --- | --- | --- |
| WebUI + WebSocket | `8765` | `channels.websocket.port` |
| Gateway health | `18790` | `gateway.port` |
| OpenAI-compatible API | `8900` | `api.port` (`navin serve`) |
| Vite dev | `5173` | WebUI Makefile |

```bash
navin ports
navin ports check
```

See [Ports](./ports.md).

### Always-on gateway

```bash
navin gateway --background
navin gateway status
navin gateway logs
navin gateway install-service
```

[Long-running agent](./guides/long-running-ai-agent.md) · [Deploy the gateway](./guides/deploy-navin-gateway.md)

## 5. Uninstall

- Desktop: use the OS uninstaller.
- CLI prefix: remove `~/.local/bin/navin`, `~/.local/bin/navin-cli` and `~/.local/share/navin` (or your `NAVIN_PREFIX`).
- User data is **not** deleted automatically. Remove `~/.navin` only if you want to wipe config, chats and workspaces.

```bash
navin cache --clear
```

Does not delete chats, config, or workspaces.

## 6. Troubleshooting

```bash
navin doctor
navin --version
navin ports check
```

| Symptom | What to try |
| --- | --- |
| `navin-cli: command not found` | New terminal, or `navin install-cli`, or `export PATH="$HOME/.local/bin:$PATH"` |
| Gateway will not start | `navin ports check`, then `make logs` |
| Vite cannot reach the API | Confirm gateway port in `~/.navin/config.json`, set `NAVIN_API_URL` |
| No model replies | **Ctrl+G → Providers / Models**, or `navin doctor` |

More: [CLI troubleshooting](./cli/troubleshooting.md) · [Start without a technical background](./start-without-technical-background.md)
