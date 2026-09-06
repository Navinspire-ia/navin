# Montage packages and toolchain

Montage never bundles HyperFrames or Remotion into the Navin cold start.

FFmpeg is different: desktop builds **ship a static FFmpeg** inside the bundle, so video assembly, social exports and video attachment analysis work on a fresh install with nothing to download. A binary on `PATH` still wins, so you can pin your own build. Source installs and containers have no bundled copy and fall back to the system or user-local one.

## Package catalog

| Id | Tier | Size (approx.) | Role |
| --- | --- | --- | --- |
| `stock-pexels` / `stock-unsplash` / `stock-pixabay` | builtin | 0 | HTTP clients only. Free developer API keys. Hidden from Studio Install list. |
| `ffmpeg` | system | ~80 MB | Encode, crop, subtitle burn-in, audio mix for `package` / demos |
| `hyperframes` | lazy | ~120 MB | HTML/CSS/GSAP → MP4 under `~/.navin/montage` |
| `remotion` | lazy (optional) | ~350 MB | React compositions under `~/.navin/montage/remotion` |

Install via Studio **System** tab, or:

```text
montage(action=setup, package=ffmpeg|hyperframes|remotion|core)
```

`package=core` only detects stock + FFmpeg; it does not download npm stacks.

## Detection

| Check | How |
| --- | --- |
| FFmpeg | `PATH`, then the bundled `tools/ffmpeg`, then `~/.navin/montage/bin/ffmpeg` (`.exe` on Windows) |
| HyperFrames | CLI under `~/.navin/montage` |
| Remotion | CLI under `~/.navin/montage/remotion` |
| Chrome | System Chrome/Chromium, or Playwright Chromium cache (`ms-playwright`) |

Doctor (`montage(action=doctor)`) reports ok / warn / missing with a fix string.

## FFmpeg install order (Install button)

1. **OS package manager** when it can run non-interactively:
   - Windows: `winget` (`Gyan.FFmpeg`) then `choco`
   - macOS: `brew install ffmpeg`
   - Linux: `apt-get` / `dnf` / `yum` / `pacman` with passwordless `sudo -n` when available
2. **User-local fallback** (no root): static build extracted to `~/.navin/montage/bin`
   - Linux amd64 / arm64 (johnvansickle static)
   - Windows (Gyan essentials zip)
   - macOS Intel fallback zip when brew is absent; Apple Silicon prefers Homebrew

If FFmpeg shows “not on PATH” without an Install button, refresh after upgrading the gateway - missing FFmpeg must expose Install whenever a recipe is runnable (manager or user-local).

### Manual commands (when you prefer the OS package)

| OS | Command |
| --- | --- |
| Ubuntu / Debian | `sudo apt-get install -y ffmpeg` |
| Fedora / RHEL (dnf) | `sudo dnf install -y ffmpeg` |
| CentOS (yum) | `sudo yum install -y ffmpeg` |
| Arch | `sudo pacman -S --noconfirm ffmpeg` |
| macOS | `brew install ffmpeg` |
| Windows | `winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements` |

## HyperFrames and Remotion

| Package | Install | Required for |
| --- | --- | --- |
| HyperFrames | `setup package=hyperframes` | HTML compositions → MP4 (`render`) |
| Remotion | `setup package=remotion` | React scenes only - optional |

Prefer HyperFrames for motion type / product promos. Do not install Remotion unless the user asks for React compositions.

## AI media models (Studio tab)

Separate from OS packages. When a managed Navin plan or BYOK is configured, the **AI media providers** tab lists curated models for:

- Image generation
- Video generation
- Music (Lyria)
- Speech-to-text
- Text-to-speech

Changing a model updates Settings for that session/workspace. Labels strip vendor noise (e.g. “OpenRouter”) in the Montage UI.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| FFmpeg missing, no Install | Old gateway without user-local recipe | Restart gateway on current tree; refresh Studio |
| Install fails with sudo hint | Interactive password required | Run the printed `sudo …` command, or rely on user-local download |
| Package exports partial | FFmpeg still missing | Install FFmpeg, re-run `package` |
| HyperFrames render asks for Chrome | No browser binary | Install Chrome/Chromium or `playwright install chromium` |
| Agent suggests another product | Unlinked / non-runnable project | Link project or give live URL once |
