# Installation

## 1. Official CLI one-liner

The script reads the official `releases.json`. On Linux it prefers the native package for the distribution, then falls back to the CLI archive or another supported format. On hosts with `pacman`, including Arch and Omarchy, it installs the official `.pkg.tar.zst` with `sudo pacman -U` (or `pkexec` when `sudo` is absent), which registers the desktop launcher. Other Linux hosts extract the package without sudo. macOS prefers the CLI archive when available. It writes `navin` and `navin-cli` to the user PATH. Default prefix: `~/.local`.

Extracted CLI packages are checked before the commands switch to the new engine. Reinstallations keep previous extraction directories so open CLI sessions can finish using them.

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

## 2. First configuration

In `navin-cli`, **Ctrl+G → Providers**: add a key (OpenRouter, OpenAI, Anthropic, …) or a local `apiBase` (Ollama `http://127.0.0.1:11434/v1`). Then **Models**: add a configuration and set it active.

Or:

```bash
navin onboard
```

Env keys (`OPENROUTER_API_KEY`, …) are honored and are not written to `config.json`.

This tree is BYOK only. Add keys in **Ctrl+G → Providers**. There is no Navin managed provider and no navin.live account on `main`.

Config file: `~/.navin/config.json`. Same file for `navin-cli` and the desktop.

## 3. Build from source (gateway + WebUI)

Use this when you clone the repository and want to run or change Navin locally.

`make install` installs missing system packages when it can (`apt` on Debian/Ubuntu, `dnf`/`yum` on Fedora/RHEL, `pacman` on Arch, `zypper`, Homebrew on macOS), then the Python backend and the WebUI.

### Prerequisites

If you skip system install (`NAVIN_SKIP_SYSTEM=1` or `--no-system`):

- Python 3.11 or newer
- Make
- Git
- Node.js 18+ and npm
- Linux / macOS: [rustup](https://rustup.rs) if you need the native sandbox (`make native`)

### Two commands

```bash
git clone https://github.com/Navinspire-ia/navin.git
cd navin
make install
make start
```

Or the same with scripts:

```bash
sh scripts/install.sh
sh scripts/start.sh
```

One shot (install + start):

```bash
sh scripts/start.sh --install
```

This is **dev**:

- Vite hot reload: `http://localhost:5173/`
- the gateway (health + API). The same gateway serves the **production** WebUI from `navin/web/dist` at `http://localhost:8765/` (or `channels.websocket.port`)

```bash
make stop
# or: sh scripts/stop.sh
```

### Front / backend

Make and scripts accept the same scopes:

| Command | What it does |
| --- | --- |
| `make install` / `sh scripts/install.sh` | System + backend + WebUI |
| `make install backend` | Backend only (`.venv`) |
| `make install front` | WebUI only (`npm ci`) |
| `make start` / `sh scripts/start.sh` | DEV: gateway + Vite `http://localhost:5173/` |
| `make start-prod` / `sh scripts/start.sh --prod` | PROD: build if needed + gateway (no Vite) |
| `make build` / `sh scripts/build.sh` | Front (`navin/web/dist`) + backend (pip editable) |
| `make build front` | Vite production bundle only |
| `make build backend` | Refresh `.venv` (`pip install -e .`) |
| `make start backend` | Gateway only (serves the build on `:8765`) |
| `make start front` | Vite only |
| `make start-fg` / `sh scripts/start.sh --fg` | Gateway in the foreground |
| `make stop` / `restart` / `status` | Same scopes: `front` or `backend` |

```bash
make start backend
make start front
make restart
make status
make logs
```

Native helpers (Linux / macOS), after `make install`:

```bash
make native
```

Vite proxies `/api`, `/webui`, `/auth` and the WebSocket to the gateway. If the gateway is not on 8765:

```bash
NAVIN_API_URL=http://127.0.0.1:8766 npm run dev
```

### Docker (this repo)

No third-party Navin image. Build from `https://github.com/Navinspire-ia/navin` (this tree):

```bash
docker compose up navin-gateway
```

The image installs the `navin` CLI and serves the bundled WebUI. Gateway health defaults to `18790`, WebUI to `8765`. Config: `~/.navin` mounted into the container.

### Use the source CLI

From the `navin` clone, after install, you can launch the CLI with the venv binary. No need to activate the venv:

```bash
cd /path/to/navin
.venv/bin/navin-cli
```

Windows:

```powershell
cd \path\to\navin
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

## 4. Uninstall

- Desktop: use the OS uninstaller.
- CLI prefix: remove `~/.local/bin/navin`, `~/.local/bin/navin-cli` and `~/.local/share/navin` (or your `NAVIN_PREFIX`).
- User data is **not** deleted automatically. Remove `~/.navin` only if you want to wipe config, chats and workspaces.

```bash
navin cache --clear
```

Does not delete chats, config, or workspaces.

## 5. Troubleshooting

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
