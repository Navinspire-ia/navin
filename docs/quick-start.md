# Install and Quick Start

This guide has one goal: get a normal navin reply in your browser. Do not add chat apps, MCP servers, fallback models, or deployment until this path works.

If terminals, Python, or API keys are unfamiliar, use the [beginner walkthrough](./start-without-technical-background.md), which explains each term and screen.

These repository docs follow current `main`. The recommended installer uses the stable package, so a newly documented WebUI screen may not appear until the next release. Each advanced guide also provides a CLI or manual config path.

## What You Need

- Python 3.11 or newer.
- Access to one supported AI provider, company endpoint, or local model server.
- The credential, endpoint URL, and model ID required by that service. Local providers such as Ollama may not require a key.

Git is only needed for a source install. The published package already contains the WebUI. A current-source install needs `bun` or `npm` so its WebUI bundle can be built.

## 1. Install navin

The recommended installer keeps navin out of the system Python environment. All configuration then happens directly in the platform (WebUI).

**macOS / Linux**

```bash
curl -fsSL https://raw.githubusercontent.com/EIAGEN/navin-claw/main/scripts/install.sh | sh
```

**Windows PowerShell**

```powershell
irm https://raw.githubusercontent.com/EIAGEN/navin-claw/main/scripts/install.ps1 | iex
```

**Windows — navin.exe (no terminal needed)**

Download `navin.exe` from the [GitHub releases](https://github.com/EIAGEN/navin-claw/releases) and double-click it. The launcher finds Python (offering a silent winget install if missing), creates `~/.navin/venv`, installs Navin from GitHub, then starts the gateway and opens the WebUI in a dedicated app window (Chromium `--app` mode — no address bar, own taskbar entry; set `NAVIN_WEBUI_TAB=1` for a classic tab) — you configure everything in the platform. For a permanent desktop icon, install Navin as an app from the Edge/Chrome menu (**Apps → Install Navin**). Run `navin.exe --update` to upgrade, or `navin.exe <command>` to use any CLI command. A full standalone build (Python bundled, ~200 MB: `navin-windows-x64.zip`, plus Linux/macOS archives) is published alongside it — unzip and run `navin.exe` inside, no Python required at all.

The installer chooses an active virtual environment, `uv`, `pipx`, or a managed environment under `~/.navin/venv`. It installs the stable PyPI release unless you explicitly pass `--dev`. At the end it prints the exact command it used to run navin; if `navin` is not on `PATH`, reuse that full command in the examples below.

If you prefer to inspect the scripts first, open [`install.sh`](../scripts/install.sh) or [`install.ps1`](../scripts/install.ps1).

## 2. Start and configure in the platform

Start the platform:

```bash
navin webui
```

This prepares the local WebUI channel, starts the gateway, and opens the browser. On first start, an alert at the top of the platform asks you to configure a model provider. Open **Settings → Providers** and:

1. Choose the provider or endpoint that owns your credential.
2. Enter its API key or base URL.
3. Pick a model ID that the same provider can run in **Settings → Models**.

The first start creates:

| Path | Purpose |
|---|---|
| `~/.navin/config.json` | Provider, model, WebUI, channel, tool, and runtime settings |
| `~/.navin/workspace/` | Sessions, memory, skills, automations, and generated files |

## 3. Check the Setup

```bash
navin status
```

You want:

- a check mark for **Config** and **Workspace**;
- the model or preset you selected;
- a configured state for the provider used by that model.

Most other providers can say `not set`. This command validates local setup but does not call the model.

## 4. Get the First Reply

```bash
navin gateway
```

Quick Start has already prepared the local WebSocket channel. Leave the gateway terminal open and visit `http://127.0.0.1:8765`; the first-run WebUI is bound to localhost, so other devices on your network cannot reach it. On current source versions, you can run `navin webui` instead to perform the local WebUI checks, start the gateway, and open the browser automatically.

Send:

```text
Hello!
```

Any normal assistant answer is success. It proves that navin can load the config, reach the selected model, use the workspace, and serve the browser UI.

Leave the terminal open while using the WebUI. If you prefer a managed background process, stop the foreground process with `Ctrl+C`, then run:

```bash
navin gateway --background
navin gateway status
```

Use `navin gateway logs`, `restart`, and `stop` to manage that background gateway.

## Terminal-Only Check

If you do not want the browser or need to isolate a WebUI problem, send one message directly:

```bash
navin agent -m "Hello!"
```

Then start an interactive terminal chat with:

```bash
navin agent
```

In interactive mode, `Enter` sends and `Alt+Enter` inserts a newline. Exit with `exit`, `/exit`, `:q`, or `Ctrl+D`.

## Choose One Next Step

After the first reply works, add one capability and test again:

| Goal | Recommended path |
|---|---|
| Learn sessions, workspaces, tools, and access modes | [WebUI guide](./webui.md) |
| Connect a chat platform | Open **Settings → Channels**, then use [Chat Apps](./chat-apps.md) for platform prerequisites |
| Change or add a model | Open **Settings → Models**; use the [Provider Cookbook](./provider-cookbook.md) for a recipe |
| Add web search, voice, or image generation | Use the matching WebUI Settings page, then consult [Configuration](./configuration.md) for advanced fields |
| Add an App or MCP integration | Open **Apps** or follow [Configure MCP Tools](./guides/configure-mcp-tools.md) |
| Schedule agent work | Read [Automations](./automations.md) |
| Run continuously or remotely | Read [Deployment](./deployment.md) |
| Integrate from code | Use the [Python SDK](./python-sdk.md) or [OpenAI-Compatible API](./openai-api.md) |

## Other Install Methods

Use one method, then continue at [Complete Quick Start](#2-complete-quick-start).

**uv**

```bash
uv tool install navin-ai
navin webui
```

**pip in a virtual environment**

```bash
python -m pip install navin-ai
navin webui
```

If pip reports `externally-managed-environment`, use the recommended installer, `uv tool install navin-ai`, `pipx install navin-ai`, or create a virtual environment. Do not force a system-wide install.

**Current source**

`bun` or `npm` must be available. Activate a virtual environment first, then run:

```bash
git clone https://github.com/EIAGEN/navin-claw.git
cd navin
python -m pip install .
navin webui
```

On Windows, if `python -m pip install .` reports that it cannot launch `npm`, run `cd webui`, `npm.cmd install --package-lock=false`, `npm.cmd run build`, and `cd ..` in order, then retry the install.

The source path follows current `main` and can be newer than the published package. A non-editable install triggers the build hook that bundles the current WebUI. For editable Python or frontend development, follow [`../AGENTS.md`](../AGENTS.md) and [`../webui/README.md`](../webui/README.md).

If the package is installed but the shell cannot find `navin`, use the runner that owns the installation. The recommended installer prints the exact command to reuse. Common forms are:

```bash
uv tool run --from navin-ai navin --version
pipx run --spec navin-ai navin --version
~/.navin/venv/bin/python -m navin --version
```

On Windows, the managed-environment form is `& "$HOME\.navin\venv\Scripts\python.exe" -m navin --version`. Replace `--version` with `webui`, `gateway`, or any other arguments you need. Use plain `python -m navin` only when that Python executable belongs to the environment where navin was installed.

## Manual Configuration Fallback

Use this only when you intentionally manage JSON instead of the platform settings. First run `navin webui --no-open --yes` once (it creates the default config), then merge a provider and a named model preset into `~/.navin/config.json`.

A generic OpenAI-compatible setup has this shape:

```json
{
  "providers": {
    "custom": {
      "apiKey": "${PROVIDER_API_KEY}",
      "apiBase": "https://api.example.com/v1"
    }
  },
  "modelPresets": {
    "primary": {
      "provider": "custom",
      "model": "model-id-from-your-provider"
    }
  },
  "agents": {
    "defaults": {
      "modelPreset": "primary"
    }
  }
}
```

Replace the provider, endpoint, and model together. Do not pair a credential from one service with a model ID from another. See [Provider Cookbook](./provider-cookbook.md) for hosted, OAuth, company, and local examples, and [Configuration](./configuration.md) for exact fields.

## Updating

Upgrade with the same method you used to install:

```bash
# Recommended installer
curl -fsSL https://raw.githubusercontent.com/EIAGEN/navin-claw/main/scripts/install.sh | sh

# Or one of these
uv tool upgrade navin-ai
pipx upgrade navin-ai
python -m pip install -U navin-ai
```

For a source checkout:

```bash
git pull
python -m pip install .
```

Then check `navin --version`. Run `navin onboard --refresh` when you want to add newly introduced default fields while preserving existing settings.

## If the First Reply Fails

Do not change several settings at once. Start with:

```bash
navin --version
navin status
navin agent -m "Hello!"
```

| Symptom | First check |
|---|---|
| `navin: command not found` | Reuse the installer command or method-specific runner described under [Other Install Methods](#other-install-methods) |
| JSON parse error | Check commas and braces; remember that docs examples are usually snippets |
| `401` or invalid API key | Verify the selected provider owns that key and remove accidental spaces |
| Model not found | Use a model ID available from the provider selected in the active preset |
| CLI works but WebUI does not open | Use port `8765`, not gateway health port `18790` |
| WebUI works but a chat app does not | Check **Settings → Channels**, then run `navin channels status` |

Continue with the ordered [Troubleshooting guide](./troubleshooting.md) if the cause is still unclear.
