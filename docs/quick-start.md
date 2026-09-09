# Quick Start

Install Navin, configure a model provider, and send a first message.

## 1. Install

Download the desktop app from [navin.live/download](https://navin.live/download) and install it.

- **Windows**: run the `.exe` installer. A Valid Navinspire signature does not hide SmartScreen until that file hash has reputation. If the blue warning appears: More info → Run anyway.
- **macOS**: open the `.dmg`, drag Navin to Applications. If Gatekeeper blocks it: right-click → Open.
- **Linux**: use the AppImage (may need `libfuse2`) or the `.deb` / `.rpm` / `.pkg.tar.zst`.

Or install from a terminal (same official build, `navin` on PATH):

```bash
curl https://navin.live/install -fsS | bash
```

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

Then `navin-cli` in a project, or `navin .` for the desktop window. [CLI](./cli/overview.md).

From a `navin` clone: `make install` then `make start`, and `.venv/bin/navin-cli`.

## 2. First launch

On first launch, Navin shows a short setup wizard:

1. Choose the interface language.
2. Add your own API key (BYOK). The product is free to use with your keys.
3. Open the demo workspace, or go to chat.

## 3. First prompt

On an empty chat, use a suggested action (plan, analyze, brainstorm, code) or type your own request.

## 4. Data

API keys, chats, files, and memory stay on your machine.

For a guided walkthrough with no terminal experience required, see [Start Without Technical Background](./start-without-technical-background.md).
