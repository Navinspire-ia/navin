---
name: app-environment-manager
description: Phase 3 of App Factory. Detect and prepare the build toolchain for a mobile or desktop target (Node, JDK 21, Android SDK, Gradle, Rust, MSVC, WebView2, webkit2gtk-4.1, Xcode) with `appfactory action=doctor`, and install missing tools with official installers only after the user agrees.
metadata: {"navin":{"emoji":"🧰","category":"development"}}
---

# App Environment Manager

## When to use

Before scaffolding or building, and whenever a build fails on a missing tool.

## Inputs

- Target id, host OS.

## Procedure

1. `appfactory action=doctor target=<id>`. It never installs anything.
2. Host check: `windows` MSI needs Windows; `ios` and `macos` need macOS + Xcode. Otherwise propose a CI runner (GitHub Actions `windows-latest`, `macos-latest`, `ubuntu-latest`).
3. Missing tools: `ask_user` ("Install the Android toolchain now" recommended / "Use a CI runner" / "Stop"). Client wording never names Rust, Gradle, Android SDK, Tauri or Capacitor.
4. After consent, `exec` the official commands (long ones `background=true`, polled):
   - Rust (all desktop): `curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y`; Windows: `winget install Rustlang.Rustup`.
   - Targets: `rustup target add x86_64-pc-windows-msvc`, `aarch64-apple-darwin x86_64-apple-darwin`.
   - Windows: `winget install Microsoft.VisualStudio.2022.BuildTools --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"`, `winget install Microsoft.EdgeWebView2Runtime`.
   - Ubuntu/Debian: `sudo apt-get install -y libwebkit2gtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev rpm`.
   - Fedora/CentOS Stream/RHEL: `sudo dnf install -y webkit2gtk4.1-devel openssl-devel curl wget file libappindicator-gtk3-devel librsvg2-devel rpm-build` and `sudo dnf group install -y "c-development"` (or `"Development Tools"`).
   - macOS: `xcode-select --install` (full Xcode from the App Store for iOS).
   - JDK 21 (Android): Temurin, `winget install EclipseAdoptium.Temurin.21.JDK`, `brew install --cask temurin@21`, or `sudo apt-get install -y temurin-21-jdk` after adding the Adoptium repo.
   - Android SDK: command line tools from developer.android.com, then `sdkmanager "platform-tools" "platforms;android-35" "build-tools;35.0.0"` and `sdkmanager --licenses`; set `ANDROID_HOME` and `JAVA_HOME`.
   - iOS: Xcode, `sudo gem install cocoapods` or `brew install cocoapods`.
5. Re-run doctor until green.

## Verification

`rustc --version`, `cargo --version`, `java -version` (21), `sdkmanager --list_installed`, `adb version`, `node --version`.

## Failure modes and fixes

- `sudo` needs a password: give the user the exact command to run in `open_terminal`.
- PATH not updated after install: new shell, or `source "$HOME/.cargo/env"`.

## Deliverable

Green doctor or agreed CI path; report step `tools`.
