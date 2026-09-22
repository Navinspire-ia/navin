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

The script reads the official `releases.json`. On Linux it prefers the native package for the distribution, then falls back to the CLI archive or another supported format. On hosts with `pacman`, including Arch and Omarchy, it installs the official `.pkg.tar.zst` with `sudo pacman -U` (or `pkexec` when `sudo` is absent), which registers the desktop launcher. Other Linux hosts extract the package without sudo. macOS prefers the CLI archive when available. It writes `navin` and `navin-cli` to the user PATH. Default prefix: `~/.local`.

Extracted CLI packages are checked before the commands switch to the new engine. Reinstallations keep previous extraction directories so open CLI sessions can finish using them.

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

Providers: **Ctrl+G** in `navin-cli`. This tree is BYOK only (your keys or a local endpoint). There is no Navin managed provider and no Account sign-in.

### From a `navin` clone

```bash
make install
make start
```

Or `sh scripts/install.sh` then `sh scripts/start.sh`. After that, launch the CLI from the repo root:

```bash
cd /path/to/navin
.venv/bin/navin-cli
```

Windows: `.venv\Scripts\navin-cli`

## Uninstall

Use the OS uninstaller. User data stays in `~/.navin` until you delete it.

```bash
navin cache --clear
```

Does not delete chats, config, or workspaces.
