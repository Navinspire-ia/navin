# Install

Official builds only. Same engine as the desktop app.

After install, a new terminal has `navin` and `navin-cli`.

## One-liner

Linux, macOS, WSL:

```bash
curl https://navin.live/install -fsS | bash
```

Windows PowerShell:

```powershell
irm 'https://navin.live/install?win32=true' | iex
```

The script reads the official `releases.json`, prefers a CLI archive when published, otherwise extracts the desktop package. It puts `navin` and `navin-cli` on the user PATH (no sudo on Linux).

```bash
cd your-project
navin-cli
navin --version
navin .
```

Linux arm64: use [navin.live/download](https://navin.live/download) until a CLI tarball is listed.

Overrides: `NAVIN_DOWNLOAD_BASE`, `NAVIN_SITE`, `NAVIN_PREFIX` (default `~/.local`).

## Localhost

Against a local site, `/install` serves files from this repo (`make local-releases`) and does not hit production S3.

```bash
curl http://localhost:3100/install -fsS | bash
```

## Desktop packages

From [navin.live/download](https://navin.live/download): Windows `.exe` / `.msi`, macOS `.dmg`, Linux `.deb` / `.rpm` / pacman / AppImage. Distro packages put `navin` on PATH; `navin-cli` is added next to it.

## PATH

```bash
navin install-cli
navin install-cli --force
```

Writes both `navin` and `navin-cli`. Needed after a macOS DMG if the command is missing.

## First config

```bash
navin onboard
navin doctor
navin status
```

Providers: **Ctrl+G** in `navin-cli`.

## Uninstall

Use the OS uninstaller. User data stays in `~/.navin` until you delete it.

```bash
navin cache --clear
```

Does not delete chats, config, or workspaces.
