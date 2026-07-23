# Packaging

Two complementary Windows/desktop distributions, built from this directory.

## 1. `navin.exe` — launcher (~30 KB)

A tiny native Windows executable (`packaging/windows/NavinLauncher.cs`) that
makes Navin double-clickable:

1. Finds Python 3.11+ (`py`, `python`, common install dirs); offers a silent
   `winget` install of Python 3.12 if none is found.
2. Creates/reuses a dedicated venv at `%USERPROFILE%\.navin\venv`.
3. Installs Navin from `https://github.com/EIAGEN/navin-claw` on first run.
4. Starts the WebUI directly — providers and models are configured in the platform (Settings → Providers), no terminal wizard.
5. Starts the gateway and opens the WebUI in the browser.

Extra modes: `navin.exe --update` (force upgrade), `navin.exe <args>` (CLI
pass-through, e.g. `navin.exe gateway status`).

Build (Windows or WSL — uses the C# compiler bundled with .NET Framework,
no SDK to install):

```bash
make exe            # → dist/navin.exe
```

## 2. Standalone binaries — PyInstaller (~200 MB)

A self-contained one-folder build with the Python runtime, all dependencies,
the bundled WebUI, templates, and skills. No Python needed on the target
machine. Spec: `packaging/pyinstaller/navin.spec`; entry point starts the
WebUI when double-clicked without arguments, otherwise behaves as the CLI.

```bash
make binary         # → dist/navin-standalone/navin (or navin.exe on Windows)
```

PyInstaller does not cross-compile: build each OS artifact on that OS. The
GitHub Actions workflow `.github/workflows/release-binaries.yml` builds all
three (Windows x64, Linux x64, macOS arm64) plus the launcher on every `v*`
tag and attaches them to the release.

## Icon

`packaging/windows/navin.ico` is generated from `webui/public/logo/navin.png`:

```bash
.venv/bin/python -c "from PIL import Image; Image.open('webui/public/logo/navin.png').convert('RGBA').save('packaging/windows/navin.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
```
