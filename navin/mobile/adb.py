"""Dynamic discovery of adb and the Android SDK.

Looks beyond PATH so macOS Homebrew / Android Studio installs, Linux SDK
trees, and WSL hosts that only have Android Studio on Windows still work
without manual export steps. When adb is missing, readiness reports
OS-specific install steps and may attempt a non-interactive install when
the process already has sufficient rights (root apt, passwordless sudo, brew).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from navin.utils.host import HostPlatform, host_platform
from navin.utils.proc import no_window_kwargs

# Re-export for callers and tests that patch ``navin.mobile.adb.host_platform``.
__all__ = [
    "AdbLocation",
    "HostPlatform",
    "host_platform",
    "resolve_adb_binary",
    "resolve_adb_location",
]


@dataclass(frozen=True, slots=True)
class AdbLocation:
    """Resolved adb binary and optional SDK root."""

    adb: str
    sdk: str | None = None
    source: str = "path"  # path | env | linux | macos | windows-wsl | homebrew | navin

    def to_dict(self) -> dict[str, str | None]:
        return {"adb": self.adb, "sdk": self.sdk, "source": self.source}


# host_platform lives in navin.utils.host; imported above for this module's API.

# Set by preview_readiness when an alternate adb actually sees devices while
# the default resolution does not (typical on WSL: Linux adb on PATH sees
# nothing, Windows adb.exe owns the USB phone / Android Studio AVD).
_preferred_adb: str | None = None


def _env_state_path() -> Path:
    """Persisted mobile-environment state, survives gateway restarts/updates."""
    return Path.home() / ".navin" / "mobile-env.json"


def _load_env_state() -> dict[str, Any]:
    try:
        data = json.loads(_env_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_env_state(update: dict[str, Any]) -> None:
    """Merge ``update`` into the saved state. Best-effort, never raises.

    Skips the write when nothing changed so readiness polling does not
    rewrite the file every few seconds.
    """
    state = _load_env_state()
    if all(state.get(k) == v for k, v in update.items()):
        return
    state.update(update)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path = _env_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


@lru_cache(maxsize=1)
def resolve_adb_location() -> AdbLocation | None:
    """Return the best adb available on this machine, or None."""
    explicit = (os.environ.get("NAVIN_ADB") or "").strip()
    if explicit and Path(explicit).is_file() and _is_runnable(explicit):
        return AdbLocation(adb=explicit, sdk=_sdk_near(explicit), source="navin")

    # Runtime preference first (device seen this process), then the state
    # saved by a previous run - a Navin update / gateway restart must not
    # redo the whole discovery when the prepared environment is still valid.
    saved = _load_env_state()
    for preferred in (_preferred_adb, str(saved.get("preferred_adb") or "").strip()):
        if preferred and Path(preferred).is_file() and _is_runnable(preferred):
            return AdbLocation(
                adb=preferred,
                sdk=_sdk_near(preferred) or _sdk_from_env(),
                source=_source_for_path(Path(preferred)),
            )

    which = shutil.which("adb")
    if which:
        return AdbLocation(
            adb=which,
            sdk=_sdk_near(which) or _sdk_from_env(),
            source="path",
        )

    # Homebrew / common absolute locations before scanning whole SDK trees.
    for candidate in _common_adb_binaries():
        if candidate.is_file() and _is_runnable(str(candidate)):
            source = "homebrew" if "homebrew" in str(candidate).lower() or "/usr/local/" in str(candidate) else _source_for_path(candidate)
            return AdbLocation(
                adb=str(candidate),
                sdk=_sdk_near(str(candidate)) or _sdk_from_env(),
                source=source,
            )

    for sdk in _sdk_candidates():
        for name in ("adb", "adb.exe"):
            candidate = sdk / "platform-tools" / name
            if candidate.is_file() and _is_runnable(str(candidate)):
                return AdbLocation(
                    adb=str(candidate),
                    sdk=str(sdk),
                    source=_source_for_path(candidate),
                )
    return None


def resolve_adb_binary() -> str | None:
    loc = resolve_adb_location()
    return loc.adb if loc else None


def resolve_android_sdk() -> Path | None:
    loc = resolve_adb_location()
    if loc and loc.sdk:
        return Path(loc.sdk)
    for sdk in _sdk_candidates():
        return sdk
    return _sdk_from_env()


def clear_adb_cache() -> None:
    """Drop cached resolution (after install / PATH changes)."""
    resolve_adb_location.cache_clear()


def adb_setup_help() -> str:
    """Human-readable, OS-specific next steps when adb is missing."""
    plat = host_platform()
    looked = [
        "Navin looked in PATH, ANDROID_HOME / ANDROID_SDK_ROOT,",
        "NAVIN_ADB, common SDK folders, and platform-tools.",
    ]
    if plat == "macos":
        looked.append(
            "On macOS also: ~/Library/Android/sdk, Homebrew "
            "(/opt/homebrew, /usr/local), and Android Studio defaults."
        )
    elif plat == "windows":
        looked.append(
            "On Windows also: %LOCALAPPDATA%\\Android\\Sdk, "
            "%USERPROFILE%\\AppData\\Local\\Android\\Sdk, "
            "scoop/chocolatey shims, and ~/.navin/android-platform-tools."
        )
    elif plat == "wsl":
        looked.append(
            "On WSL also: ~/Android/Sdk and Windows Android Studio under "
            "/mnt/c/Users/*/AppData/Local/Android/Sdk."
        )
    else:
        looked.append(
            "On Linux also: ~/Android/Sdk, /usr/lib/android-sdk, /opt/android-sdk."
        )

    lines = ["adb was not found automatically.", *looked, "", "Do this next:"]
    for i, step in enumerate(_install_steps(plat), start=1):
        lines.append(f"  {i}) {step['title']}")
        for cmd in step.get("commands") or []:
            lines.append(f"       {cmd}")
        if step.get("detail"):
            lines.append(f"       {step['detail']}")
    lines.extend(
        [
            "",
            "Then verify:",
            "       adb devices   # must show at least one 'device' line",
            "Click Refresh in the Mobile tab after installing.",
        ]
    )
    if plat == "macos":
        lines.extend(
            [
                "",
                "On macOS Navin also prepares the iOS stack (Xcode simctl + "
                "libimobiledevice for USB iPhone). Prepare installs the right "
                "tools for this OS.",
            ]
        )
    return "\n".join(lines)


def adb_missing_fixes() -> list[dict[str, str]]:
    """Structured fix buttons for the WebUI when adb is missing."""
    plat = host_platform()
    fixes: list[dict[str, str]] = []
    for step in _install_steps(plat):
        commands = step.get("commands") or []
        if not commands and not step.get("prompt"):
            continue
        fix: dict[str, str] = {
            "id": step["id"],
            "label": step["label"],
        }
        if commands:
            joiner = "; " if plat == "windows" else " && "
            fix["command"] = joiner.join(commands)
        if step.get("prompt"):
            fix["prompt"] = step["prompt"]
        fixes.append(fix)
    fixes.append(
        {
            "id": "ask_agent_stack",
            "label": "Prepare stack for this OS",
            "prompt": _agent_install_prompt(plat),
        }
    )
    return fixes


def can_auto_install_adb() -> bool:
    """True when a non-interactive install can proceed without a password prompt.

    User-local Google platform-tools zip works on every desktop OS without admin.
    Package managers (brew/apt/choco/scoop) are preferred when already usable.
    """
    plat = host_platform()
    return plat in {"macos", "windows", "linux", "wsl"}


def try_install_adb() -> dict[str, Any]:
    """Attempt a non-interactive adb install when rights allow.

    Never prompts for a password. On failure, returns OS-specific next steps
    so the UI / agent can guide the user.
    """
    clear_adb_cache()
    if resolve_adb_location() is not None:
        loc = resolve_adb_location()
        assert loc is not None
        return {
            "ok": True,
            "already": True,
            "adb": loc.adb,
            "detail": f"adb already available: {loc.adb}",
        }

    plat = host_platform()
    if plat == "macos":
        result = _try_install_macos()
    elif plat in {"linux", "wsl"}:
        result = _try_install_debian_family()
    elif plat == "windows":
        result = _try_install_windows()
    else:
        return {
            "ok": False,
            "already": False,
            "detail": (
                "Automatic install is not supported on this host. "
                "Follow the platform steps shown in help."
            ),
            "help": adb_setup_help(),
            "fixes": adb_missing_fixes(),
        }
    # Remember the installed binary so the next run (after a Navin update
    # or restart) resolves it instantly instead of reinstalling/rescanning.
    if result.get("ok") and result.get("adb"):
        _save_env_state({"installed_adb": str(result["adb"])})
    return result


_auto_install_attempted = False


def _sdk_prepared_by_another_build(sdk: str | None) -> bool:
    """Whether Prepare should be re-run because Navin was updated since.

    Imported lazily: bootstrap imports this module, so a top-level import
    would close the loop.
    """
    try:
        from navin.mobile.bootstrap import sdk_toolchain_stale

        return sdk_toolchain_stale(sdk)
    except Exception:
        return False


def preview_readiness(*, auto_install: bool | None = None) -> dict[str, object]:
    """Structured status for the WebUI Mobile tab (dynamic empty state).

    ``auto_install``: when True, attempt a non-interactive package install if
    rights allow. Default follows ``NAVIN_MOBILE_AUTO_INSTALL`` (off) except a
    single automatic try per process when rights look sufficient and the env
    flag ``NAVIN_MOBILE_AUTO_INSTALL`` is ``1``/``true``.
    """
    global _auto_install_attempted

    clear_adb_cache()
    loc = resolve_adb_location()
    install_attempt: dict[str, Any] | None = None

    if loc is None:
        if auto_install is True:
            should_install = True
        elif auto_install is False:
            should_install = False
        else:
            should_install = _env_flag("NAVIN_MOBILE_AUTO_INSTALL", default=False)
        if should_install:
            _auto_install_attempted = True
            install_attempt = try_install_adb()
            clear_adb_cache()
            loc = resolve_adb_location()

    if loc is None:
        payload: dict[str, object] = {
            "ready": False,
            "platform": host_platform(),
            "adb": None,
            "sdk": None,
            "source": None,
            "devices": [],
            "pending_devices": [],
            "avds": [],
            "error": "adb_missing",
            "help": adb_setup_help(),
            "fixes": adb_missing_fixes(),
            "can_auto_install": can_auto_install_adb(),
            "guide": _onboarding_guide(
                platform=host_platform(),
                adb_ok=False,
                device_ok=False,
            ),
        }
        if install_attempt is not None:
            payload["install_attempt"] = install_attempt
        from navin.mobile.ios import merge_ios_into_readiness

        return merge_ios_into_readiness(payload)

    global _preferred_adb

    devices: list[str] = []
    pending_devices: list[str] = []
    avds: list[str] = []
    error: str | None = None
    try:
        devices, pending_devices = _list_devices_detailed(loc.adb)
        avds = _list_avds_with(loc.adb, loc.sdk)
    except Exception as exc:
        error = str(exc)

    # The resolved adb sees nothing online: probe alternate adb binaries.
    # On WSL the USB phone / Android Studio AVD lives on the Windows adb.exe,
    # not on the Linux adb from PATH.
    if not devices:
        for alt in _alternate_adb_binaries(loc.adb):
            try:
                alt_online, alt_pending = _list_devices_detailed(alt, timeout_s=8)
            except Exception:
                continue
            for line in alt_pending:
                if line not in pending_devices:
                    pending_devices.append(line)
            if alt_online:
                _preferred_adb = alt
                _save_env_state({"preferred_adb": alt})
                clear_adb_cache()
                switched = resolve_adb_location()
                if switched is not None:
                    loc = switched
                devices = alt_online
                try:
                    avds = _list_avds_with(loc.adb, loc.sdk)
                except Exception:
                    avds = []
                error = None
                break

    from navin.mobile.bootstrap import _is_emulator_serial, host_mobile_capability

    capability = host_mobile_capability()
    soft = bool(capability.get("soft_accel_emulator"))
    # Soft-GPU qemu freezes - ignore those serials, but keep USB phones / Windows AVDs.
    if soft:
        usable_devices = [d for d in devices if not _is_emulator_serial(d)]
    else:
        usable_devices = list(devices)
    ready = bool(usable_devices)
    help_text = None
    fixes: list[dict[str, str]] = []
    recommendations = list(capability.get("recommendations") or [])

    if soft and devices and not usable_devices:
        error = "unstable_emulator"
        help_text = (
            "A software-GPU emulator (-accel off) is running. It will freeze and "
            "break Back/Home/tap. Stop it, then use a hardware-accelerated AVD "
            "or a USB phone."
        )
        fixes.append(
            {
                "id": "stop_soft_emu",
                "label": "Ask the agent to stop the soft emulator",
                "prompt": (
                    "Un émulateur en -accel off tourne et est instable. "
                    "Arrête-le (kill qemu / mobile stop), ne le relance PAS sans accélération. "
                    "Sur WSL: AVD Android Studio Windows ou téléphone USB. "
                    "Puis Actualiser Mobile."
                ),
            }
        )
    elif not devices and pending_devices:
        error = "device_unauthorized"
        serial = pending_devices[0].split()[0]
        state = pending_devices[0].split()[1] if len(pending_devices[0].split()) > 1 else ""
        if state == "unauthorized":
            help_text = (
                f"Phone detected ({serial}) but not authorized yet. Unlock the "
                "phone and accept the 'Allow USB debugging' prompt, then Refresh."
            )
        else:
            help_text = (
                f"Phone detected ({serial}) but its state is '{state or 'offline'}'. "
                "Unplug/replug the cable, make sure USB debugging is enabled, "
                "then Refresh."
            )
    elif not devices:
        help_text = _no_device_help(loc)
        fixes.extend(_no_device_fixes(host_platform()))
        if capability.get("block_local_emulator"):
            help_text = f"{help_text}\n\n{capability.get('acceleration_detail')}"

    # USB phone / healthy AVD => stable for preview even if local KVM is missing.
    stable = bool(usable_devices) or (
        bool(capability.get("stable")) and not soft
    )

    # Remember a working environment: after a Navin update or gateway
    # restart, resolution reuses this adb directly instead of redoing the
    # full discovery (PATH scan, /mnt/c probing, install attempts).
    if ready:
        _save_env_state(
            {
                "preferred_adb": _preferred_adb or loc.adb,
                "adb": loc.adb,
                "sdk": loc.sdk,
                "source": loc.source,
                "platform": host_platform(),
                # Day granularity: enough for "already prepared" checks and
                # avoids rewriting the file on every status refresh.
                "last_ready_at": time.strftime("%Y-%m-%d", time.gmtime()),
            }
        )

    result: dict[str, object] = {
        "ready": ready,
        "platform": host_platform(),
        "adb": loc.adb,
        "sdk": loc.sdk,
        "source": loc.source,
        "devices": devices,
        "pending_devices": pending_devices,
        "avds": avds,
        "error": error or (None if ready else "no_device"),
        "help": help_text,
        "fixes": fixes,
        "can_auto_install": False,
        "acceleration_ok": bool(capability.get("acceleration_ok")),
        "acceleration_detail": capability.get("acceleration_detail"),
        "stable": stable,
        "block_local_emulator": bool(capability.get("block_local_emulator")),
        "toolchain_stale": _sdk_prepared_by_another_build(loc.sdk),
        "recommendations": recommendations,
        "guide": _onboarding_guide(
            platform=host_platform(),
            adb_ok=True,
            device_ok=ready,
            adb_path=loc.adb,
            devices=usable_devices,
        ),
    }
    if install_attempt is not None:
        result["install_attempt"] = install_attempt

    # OS-selected second stack: on macOS merge Xcode/simctl/libimobiledevice.
    from navin.mobile.ios import merge_ios_into_readiness

    return merge_ios_into_readiness(result)


def _onboarding_guide(
    *,
    platform: HostPlatform,
    adb_ok: bool,
    device_ok: bool,
    adb_path: str | None = None,
    devices: list[str] | None = None,
) -> dict[str, Any]:
    """Simple 3-step guide for the Mobile empty state (user-facing, not technical dump)."""
    current = 1
    if adb_ok and not device_ok:
        current = 2
    elif adb_ok and device_ok:
        current = 3

    step1_action = "install_adb"
    step1_title = "Install the Android connection tool"
    step1_body = (
        "Navin needs a small tool called adb to talk to phones and emulators. "
        "Click the button below - Navin installs it for you when possible."
    )
    if platform == "wsl":
        step2_title = "Android Studio Windows + emulator"
        step2_body = (
            "Prepare asks this first:\n"
            "1) In WSL: sudo usermod -aG kvm $USER\n"
            "2) In Windows PowerShell (not Ubuntu): wsl --shutdown\n"
            "3) Reopen WSL\n"
            "OR easier: Android Studio Windows → Device Manager → Play an AVD\n"
            "4) Refresh here"
        )
        step2_link = "https://developer.android.com/studio"
        step2_link_label = "Download Android Studio (Windows)"
    elif platform == "windows":
        step2_title = "Android Studio + emulator"
        step2_body = (
            "Prepare asks this first:\n"
            "1) Install Android Studio if missing (winget)\n"
            "2) Device Manager → Play an AVD\n"
            "3) Refresh here\n"
            "Or plug a USB phone."
        )
        step2_link = "https://developer.android.com/studio"
        step2_link_label = "Download Android Studio"
    elif platform == "macos":
        step2_title = "Android Studio + emulator"
        step2_body = (
            "Prepare does this first:\n"
            "1) Install Android Studio if missing (brew cask)\n"
            "2) Device Manager → Create Device → Play\n"
            "3) Refresh here\n"
            "Or plug a USB phone. (iOS needs Xcode separately.)"
        )
        step2_link = "https://developer.android.com/studio"
        step2_link_label = "Download Android Studio (Mac)"
    else:
        step2_title = "KVM + emulator (or USB phone)"
        step2_body = (
            "Prepare does this first:\n"
            "1) sudo usermod -aG kvm $USER if /dev/kvm is denied, then re-login\n"
            "2) Bootstrap installs SDK/AVD under ~/.navin when KVM works\n"
            "3) Refresh here\n"
            "Or plug a USB phone with debugging."
        )
        step2_link = "https://developer.android.com/studio"
        step2_link_label = "Download Android Studio (optional)"

    device_name = None
    if devices:
        device_name = devices[0].split()[0]

    return {
        "current_step": current,
        "headline": "Get Mobile ready in 3 steps",
        "subtitle": (
            "Navin guides the setup. You only need a phone screen once "
            "(emulator or USB phone), then Start preview."
        ),
        "steps": [
            {
                "id": 1,
                "key": "adb",
                "title": step1_title,
                "body": step1_body if not adb_ok else f"Done. Connected via {adb_path or 'adb'}.",
                "status": "done" if adb_ok else "current" if current == 1 else "todo",
                "primary_action": None if adb_ok else step1_action,
                "primary_label": None if adb_ok else "Install for me",
            },
            {
                "id": 2,
                "key": "device",
                "title": step2_title,
                "body": (
                    f"Done. Device online: {device_name}."
                    if device_ok
                    else step2_body
                ),
                "status": "done" if device_ok else "current" if current == 2 else "todo",
                "link": None if device_ok else step2_link,
                "link_label": None if device_ok else step2_link_label,
                "primary_action": None if device_ok else "refresh",
                "primary_label": None if device_ok else "I started it - Refresh",
                "secondary_action": None if device_ok else "ask_agent_device",
                "secondary_label": None if device_ok else "Ask the agent to help",
            },
            {
                "id": 3,
                "key": "preview",
                "title": "Start the live preview",
                "body": (
                    "Your device is online. Click Start preview above to mirror "
                    "the screen here. Then use Back / Home / Recents."
                    if device_ok
                    else "Unlocks when a device appears in step 2."
                ),
                "status": "current" if current == 3 else "todo",
                "primary_action": "start_preview" if device_ok else None,
                "primary_label": "Start preview" if device_ok else None,
            },
        ],
    }


def _install_steps(plat: HostPlatform) -> list[dict[str, Any]]:
    if plat == "macos":
        return [
            {
                "id": "brew_platform_tools",
                "title": "Install platform-tools with Homebrew (fastest for adb):",
                "label": "Install adb (Homebrew)",
                "commands": ["brew install --cask android-platform-tools"],
                "detail": "If the cask fails: brew install android-platform-tools",
            },
            {
                "id": "android_studio_mac",
                "title": "Or install Android Studio, then open SDK Manager -> SDK Platform-Tools:",
                "label": "Android Studio (macOS) steps",
                "commands": [],
                "detail": (
                    "Download https://developer.android.com/studio - after setup, "
                    "adb is usually at ~/Library/Android/sdk/platform-tools/adb. "
                    "Create an AVD in Device Manager."
                ),
                "prompt": (
                    "Sur macOS, adb est manquant. Guide-moi pour installer "
                    "android-platform-tools via Homebrew (`brew install --cask "
                    "android-platform-tools`) ou Android Studio, exporter "
                    "ANDROID_HOME=$HOME/Library/Android/sdk, créer un AVD, "
                    "puis vérifier avec adb devices. Ne lance pas l'app tant "
                    "que adb devices n'est pas OK."
                ),
            },
            {
                "id": "export_mac",
                "title": "Point your shell at the SDK (add to ~/.zshrc):",
                "label": "Show ANDROID_HOME exports",
                "commands": [
                    'export ANDROID_HOME="$HOME/Library/Android/sdk"',
                    'export PATH="$PATH:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator"',
                ],
            },
        ]
    if plat == "wsl":
        return [
            {
                "id": "navin_auto_wsl",
                "title": "Let Navin download platform-tools into ~/.navin (no sudo):",
                "label": "Try Navin auto-install",
                "commands": [],
                "detail": (
                    "In the Mobile tab click Try auto-install, or run "
                    "mobile(action=setup). No apt/sudo required."
                ),
                "prompt": (
                    "Sous WSL, adb manque. Lance mobile(action=setup) pour "
                    "telecharger platform-tools dans ~/.navin sans sudo. "
                    "Ne lance pas l'app tant que adb devices n'est pas OK."
                ),
            },
            {
                "id": "apt_adb",
                "title": "Or WSL/Ubuntu apt install (needs your password):",
                "label": "Install adb (WSL/Ubuntu)",
                "commands": ["sudo apt update", "sudo apt install -y adb"],
            },
            {
                "id": "android_studio_windows",
                "title": "Or use Android Studio on Windows (recommended for emulator):",
                "label": "Android Studio (Windows) steps",
                "commands": [],
                "detail": (
                    "Install Android Studio on Windows, create an AVD, enable USB "
                    "debugging. Navin scans "
                    "/mnt/c/Users/<you>/AppData/Local/Android/Sdk/platform-tools/adb.exe."
                ),
                "prompt": (
                    "Sous WSL, adb/SDK manquent. Cherche le SDK Windows sous "
                    "/mnt/c/Users/*/AppData/Local/Android/Sdk, sinon guide "
                    "sudo apt install -y adb ou l'install Android Studio Windows + AVD. "
                    "Ne lance pas l'app tant que adb devices n'est pas OK."
                ),
            },
            {
                "id": "navin_adb_wsl",
                "title": "Or point Navin at a known binary:",
                "label": "Show NAVIN_ADB export",
                "commands": [
                    "export NAVIN_ADB=/mnt/c/Users/$USER/AppData/Local/Android/Sdk/platform-tools/adb.exe",
                    "export ANDROID_HOME=/mnt/c/Users/$USER/AppData/Local/Android/Sdk",
                ],
            },
        ]
    if plat == "windows":
        return [
            {
                "id": "navin_auto_win",
                "title": "Let Navin download platform-tools (no admin, user folder):",
                "label": "Try Navin auto-install",
                "commands": [],
                "detail": (
                    "In the Mobile tab click Try auto-install, or run "
                    "mobile(action=setup). Navin fetches Google platform-tools "
                    "into %USERPROFILE%\\.navin\\android-platform-tools."
                ),
                "prompt": (
                    "Sur Windows natif, adb est manquant. Lance mobile(action=setup) "
                    "pour telecharger platform-tools dans ~/.navin (sans admin), "
                    "sinon guide Android Studio + AVD. "
                    "Ne lance pas l'app tant que adb devices n'est pas OK."
                ),
            },
            {
                "id": "android_studio_win",
                "title": "Or install Android Studio (needed for emulator/AVD):",
                "label": "Android Studio (Windows) steps",
                "commands": [],
                "detail": (
                    "Download https://developer.android.com/studio - enable "
                    "Platform-Tools + Emulator, create an AVD. adb is usually at "
                    "%LOCALAPPDATA%\\Android\\Sdk\\platform-tools\\adb.exe."
                ),
                "prompt": (
                    "Sur Windows, j'ai besoin d'Android Studio pour l'emulateur. "
                    "Guide l'install (Platform-Tools + Emulator), la creation d'un AVD, "
                    "puis ANDROID_HOME=%LOCALAPPDATA%\\Android\\Sdk. "
                    "Ne lance pas l'app tant que adb devices n'est pas OK."
                ),
            },
            {
                "id": "choco_adb",
                "title": "Or install adb with Chocolatey / Scoop (if you use them):",
                "label": "choco / scoop adb",
                "commands": [
                    "choco install adb -y",
                    "scoop install adb",
                ],
                "detail": "Run one of these in an elevated PowerShell if the package manager is installed.",
            },
            {
                "id": "export_win",
                "title": "Point PowerShell / CMD at the SDK:",
                "label": "Show ANDROID_HOME (PowerShell)",
                "commands": [
                    '$env:ANDROID_HOME = "$env:LOCALAPPDATA\\Android\\Sdk"',
                    '$env:PATH = "$env:ANDROID_HOME\\platform-tools;$env:ANDROID_HOME\\emulator;$env:PATH"',
                    '$env:NAVIN_ADB = "$env:ANDROID_HOME\\platform-tools\\adb.exe"',
                ],
            },
        ]
    # Generic Linux
    return [
        {
            "id": "apt_adb",
            "title": "Debian/Ubuntu quick install:",
            "label": "Install adb (apt)",
            "commands": ["sudo apt update", "sudo apt install -y adb"],
        },
        {
            "id": "android_studio_linux",
            "title": "Or install Android Studio + create an AVD:",
            "label": "Android Studio (Linux) steps",
            "commands": [],
            "detail": (
                "Download https://developer.android.com/studio - SDK usually at "
                "~/Android/Sdk. Install platform-tools + emulator via SDK Manager."
            ),
            "prompt": (
                "Sur Linux, adb est manquant. Guide sudo apt install -y adb "
                "ou Android Studio + AVD, puis ANDROID_HOME. "
                "Ne lance pas l'app tant que adb devices n'est pas OK."
            ),
        },
        {
            "id": "export_linux",
            "title": "Point your shell at the SDK:",
            "label": "Show ANDROID_HOME exports",
            "commands": [
                'export ANDROID_HOME="$HOME/Android/Sdk"',
                'export PATH="$PATH:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator"',
            ],
        },
    ]


def _agent_install_prompt(plat: HostPlatform) -> str:
    del plat
    from navin.mobile.ios import os_stack_prepare_prompt

    return os_stack_prepare_prompt(android_ready=False)


def _no_device_help(loc: AdbLocation) -> str:
    plat = host_platform()
    lines = [
        f"adb found ({loc.source}): {loc.adb}",
        "No device/emulator online yet.",
        "",
    ]
    if plat == "macos":
        lines.extend(
            [
                "Next (macOS = Android + iOS stacks):",
                "  Android: Android Studio -> Device Manager -> start an AVD,",
                "           or plug a phone with USB debugging",
                "  iOS: open -a Simulator (boot a device), or plug an iPhone",
                "       (brew install libimobiledevice for USB mirroring)",
                "  Then Refresh in the Mobile tab",
            ]
        )
    elif plat == "windows":
        lines.extend(
            [
                "Next:",
                "  1) Open Android Studio -> Device Manager -> start an AVD",
                "  2) Or plug a phone with USB debugging + accept the RSA prompt",
                "  3) In PowerShell: adb devices   (must show 'device')",
                "  4) Click Refresh in the Mobile tab",
            ]
        )
    elif plat == "wsl":
        lines.extend(
            [
                "Next:",
                "  1) Start an AVD from Android Studio on Windows (easiest under WSL)",
                "  2) Or plug a phone into the PC with USB debugging enabled -",
                "     Navin also checks the Windows adb, so no usbipd is needed",
                "     when Android Studio is installed on Windows",
                "  3) Or start a Linux AVD if emulator is installed in the WSL SDK",
                "  4) Click Refresh in the Mobile tab",
            ]
        )
    else:
        lines.extend(
            [
                "Next:",
                "  1) Start an AVD in Android Studio Device Manager",
                "  2) Or plug a phone with USB debugging",
                "  3) Click Refresh in the Mobile tab",
            ]
        )
    return "\n".join(lines)


def _no_device_fixes(plat: HostPlatform) -> list[dict[str, str]]:
    del plat  # host is resolved inside os_stack_prepare_prompt
    from navin.mobile.ios import os_stack_prepare_prompt

    return [
        {
            "id": "ask_agent_stack",
            "label": "Prepare stack for this OS",
            "prompt": os_stack_prepare_prompt(android_ready=True),
        }
    ]


_PLATFORM_TOOLS_URLS = {
    "windows": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "wsl": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
    "macos": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
}


def _navin_platform_tools_dir() -> Path:
    """User-writable folder for downloaded Google platform-tools."""
    plat = host_platform()
    if plat == "windows":
        local = (os.environ.get("LOCALAPPDATA") or "").strip()
        if local:
            return Path(local) / "Navin" / "android-platform-tools"
    return Path.home() / ".navin" / "android-platform-tools"


def _try_install_windows() -> dict[str, Any]:
    """Install adb on native Windows without requiring admin when possible."""
    logs: list[str] = []

    choco = shutil.which("choco")
    if choco:
        _code, out = _run_install([choco, "install", "adb", "-y"], timeout_s=600)
        logs.append(f"$ choco install adb -y\n{out}")
        clear_adb_cache()
        loc = resolve_adb_location()
        if loc is not None:
            return {
                "ok": True,
                "already": False,
                "adb": loc.adb,
                "detail": f"Installed via Chocolatey -> {loc.adb}",
                "log": "\n\n".join(logs)[-4000:],
            }

    scoop = shutil.which("scoop")
    if scoop:
        _code, out = _run_install([scoop, "install", "adb"], timeout_s=600)
        logs.append(f"$ scoop install adb\n{out}")
        clear_adb_cache()
        loc = resolve_adb_location()
        if loc is not None:
            return {
                "ok": True,
                "already": False,
                "adb": loc.adb,
                "detail": f"Installed via Scoop -> {loc.adb}",
                "log": "\n\n".join(logs)[-4000:],
            }

    return _install_via_platform_tools_zip(logs)


def _try_install_macos() -> dict[str, Any]:
    logs: list[str] = []
    brew = shutil.which("brew")
    if brew:
        attempts = [
            [brew, "install", "--cask", "android-platform-tools"],
            [brew, "install", "android-platform-tools"],
        ]
        for argv in attempts:
            _code, out = _run_install(argv, timeout_s=600)
            logs.append(f"$ {' '.join(argv)}\n{out}")
            clear_adb_cache()
            loc = resolve_adb_location()
            if loc is not None:
                return {
                    "ok": True,
                    "already": False,
                    "adb": loc.adb,
                    "detail": f"Installed via Homebrew -> {loc.adb}",
                    "log": "\n\n".join(logs)[-4000:],
                }
    else:
        logs.append("Homebrew not found; falling back to Google platform-tools zip.")

    return _install_via_platform_tools_zip(logs)


def _try_install_debian_family() -> dict[str, Any]:
    """Prefer apt when passwordless; otherwise download Linux platform-tools zip."""
    logs: list[str] = []
    apt = shutil.which("apt-get")
    can_apt = False
    prefix: list[str] = []
    if apt:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            can_apt = True
        elif _sudo_nopasswd():
            can_apt = True
            prefix = ["sudo", "-n"]

    if can_apt and apt:
        env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
        apt_ok = True
        for argv in (
            [*prefix, apt, "update"],
            [*prefix, apt, "install", "-y", "adb"],
        ):
            code, out = _run_install(argv, timeout_s=600, env=env)
            logs.append(f"$ {' '.join(argv)}\n{out}")
            if code != 0 and "install" in argv:
                logs.append("apt failed; falling back to user-local platform-tools zip.")
                apt_ok = False
                break
        if apt_ok:
            clear_adb_cache()
            which = shutil.which("adb") or "/usr/bin/adb"
            if Path(which).is_file() or Path("/usr/bin/adb").is_file():
                adb_path = which if Path(which).is_file() else "/usr/bin/adb"
                return {
                    "ok": True,
                    "already": False,
                    "adb": adb_path,
                    "detail": f"Installed via apt -> {adb_path}",
                    "log": "\n\n".join(logs)[-4000:],
                }
            loc = resolve_adb_location()
            if loc is not None:
                return {
                    "ok": True,
                    "already": False,
                    "adb": loc.adb,
                    "detail": f"Installed via apt -> {loc.adb}",
                    "log": "\n\n".join(logs)[-4000:],
                }
    else:
        logs.append(
            "No passwordless apt rights; downloading Google platform-tools "
            "into ~/.navin (no sudo)."
        )

    return _install_via_platform_tools_zip(logs)


def _install_via_platform_tools_zip(logs: list[str] | None = None) -> dict[str, Any]:
    logs = list(logs or [])
    try:
        adb_path = _download_platform_tools()
    except Exception as exc:
        logs.append(f"platform-tools download failed: {exc}")
        return {
            "ok": False,
            "already": False,
            "detail": (
                "Could not auto-install adb via Google platform-tools zip. "
                "Follow the OS steps below (apt/brew/Android Studio), then Refresh."
            ),
            "help": adb_setup_help(),
            "fixes": adb_missing_fixes(),
            "log": "\n\n".join(logs)[-4000:],
        }
    clear_adb_cache()
    return {
        "ok": True,
        "already": False,
        "adb": adb_path,
        "detail": f"Downloaded Google platform-tools -> {adb_path}",
        "log": "\n\n".join(logs)[-4000:],
    }


def _download_platform_tools() -> str:
    """Fetch Google platform-tools zip into a user-writable directory."""
    import tempfile
    import urllib.request
    import zipfile

    plat = host_platform()
    url = _PLATFORM_TOOLS_URLS.get(plat) or _PLATFORM_TOOLS_URLS["linux"]
    target = _navin_platform_tools_dir()
    adb_name = "adb.exe" if plat == "windows" else "adb"
    adb = target / "platform-tools" / adb_name
    if adb.is_file() and _is_runnable(str(adb)):
        return str(adb)

    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="navin-platform-tools-") as tmp:
        zip_path = Path(tmp) / "platform-tools.zip"
        urllib.request.urlretrieve(url, zip_path)  # noqa: S310
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target)

    if not adb.is_file():
        raise FileNotFoundError(f"{adb_name} missing after extract under {target}")
    if plat != "windows":
        try:
            adb.chmod(adb.stat().st_mode | 0o111)
        except OSError:
            pass
    if not _is_runnable(str(adb)):
        raise PermissionError(f"{adb} is not executable")
    return str(adb)



def _run_install(
    argv: list[str],
    *,
    timeout_s: int,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            env=env,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    text = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    return int(completed.returncode), text


def _sudo_nopasswd() -> bool:
    sudo = shutil.which("sudo")
    if not sudo:
        return False
    try:
        completed = subprocess.run(  # noqa: S603
            [sudo, "-n", "true"],
            capture_output=True,
            timeout=5,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _env_flag(name: str, *, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return default


# Device states that mean "a phone is plugged in but not usable yet".
_PENDING_DEVICE_STATES = frozenset({"unauthorized", "offline", "authorizing", "connecting"})


def _list_devices_with(adb: str, *, timeout_s: int = 12) -> list[str]:
    online, _pending = _list_devices_detailed(adb, timeout_s=timeout_s)
    return online


def _list_devices_detailed(
    adb: str, *, timeout_s: int = 12
) -> tuple[list[str], list[str]]:
    """Return (online, pending) device lines from ``adb devices -l``.

    ``pending`` keeps phones that are plugged in but not usable yet
    (unauthorized RSA prompt, offline, authorizing) so the UI can tell the
    user exactly what to do instead of pretending nothing is connected.
    """
    try:
        completed = subprocess.run(  # noqa: S603
            [adb, "devices", "-l"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return [], []
    online: list[str] = []
    pending: list[str] = []
    for line in (completed.stdout or "").splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        # Modern adb devices -l uses spaces (not tabs) between columns, e.g.
        # "emulator-5554          device product:... device:emu64xa ..."
        # State is always the 2nd whitespace token - do not match "device:..." props.
        parts = line.split()
        if len(parts) < 2:
            continue
        state = parts[1]
        if state == "device":
            online.append(line)
        elif state in _PENDING_DEVICE_STATES:
            pending.append(line)
    return online, pending


def _alternate_adb_binaries(current: str, *, limit: int = 3) -> list[str]:
    """Other runnable adb binaries worth probing when the current one is empty.

    On WSL the Linux adb on PATH usually sees nothing while the Windows
    adb.exe (Android Studio) owns the USB phone and the AVDs. Probing the
    alternates lets readiness find the device wherever it actually lives.
    """
    try:
        current_key = str(Path(current).resolve())
    except OSError:
        current_key = current
    seen: set[str] = {current_key}
    out: list[str] = []

    candidates: list[Path] = list(_common_adb_binaries())
    for sdk in _sdk_candidates():
        for name in ("adb", "adb.exe"):
            candidates.append(sdk / "platform-tools" / name)

    for candidate in candidates:
        if len(out) >= limit:
            break
        try:
            if not candidate.is_file() or not _is_runnable(str(candidate)):
                continue
            key = str(candidate.resolve())
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(str(candidate))
    return out


def _emulator_binary(sdk: str | None) -> str | None:
    candidates: list[Path] = []
    if sdk:
        for name in ("emulator", "emulator.exe"):
            candidates.append(Path(sdk) / "emulator" / name)
    env_sdk = _sdk_from_env()
    if env_sdk is not None:
        for name in ("emulator", "emulator.exe"):
            candidates.append(env_sdk / "emulator" / name)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("emulator")


def start_avd(name: str) -> dict[str, Any]:
    """Launch an AVD detached so the panel can restart a closed emulator.

    Refuses when hardware acceleration is missing (a -accel off emulator
    freezes and breaks input) and when the AVD name is unknown.
    """
    clear_adb_cache()
    loc = resolve_adb_location()
    sdk = loc.sdk if loc else None
    emulator = _emulator_binary(sdk)
    if not emulator:
        return {
            "ok": False,
            "detail": (
                "Android emulator binary not found. Run mobile(action=bootstrap) "
                "or install the 'emulator' SDK package."
            ),
        }
    avds = _list_avds_with(loc.adb if loc else "", sdk)
    if name not in avds:
        return {
            "ok": False,
            "detail": f"Unknown AVD '{name}'. Available: {', '.join(avds) or 'none'}",
        }

    from navin.mobile.bootstrap import host_mobile_capability

    capability = host_mobile_capability()
    if capability.get("block_local_emulator"):
        return {
            "ok": False,
            "detail": str(
                capability.get("acceleration_detail")
                or "Hardware acceleration (KVM / Hypervisor) is missing - a "
                "software emulator would freeze. Use a USB phone or an AVD "
                "on the host OS."
            ),
        }
    try:
        subprocess.Popen(  # noqa: S603
            [
                emulator,
                "-avd",
                name,
                "-no-snapshot-save",
                "-no-audio",
                "-netdelay",
                "none",
                "-netspeed",
                "full",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "detail": f"Failed to start emulator: {exc}"}
    return {
        "ok": True,
        "detail": f"Emulator '{name}' is booting - it appears online shortly.",
    }


def _list_avds_with(adb: str, sdk: str | None) -> list[str]:
    del adb  # reserved for future avdmanager via sdk
    emulator = _emulator_binary(sdk)
    if not emulator:
        return []
    try:
        completed = subprocess.run(  # noqa: S603
            [emulator, "-list-avds"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]


def _is_runnable(path: str) -> bool:
    """True if we can invoke the binary (Unix exec bit, or Windows .exe under WSL)."""
    lower = path.lower()
    if lower.endswith(".exe") or lower.endswith(".bat") or lower.endswith(".cmd"):
        return True
    return os.access(path, os.X_OK)


def _sdk_from_env() -> Path | None:
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        raw = (os.environ.get(key) or "").strip()
        if raw:
            path = Path(raw).expanduser()
            if path.is_dir():
                return path
    return None


def _sdk_near(adb_path: str) -> str | None:
    parent = Path(adb_path).resolve().parent
    # .../Sdk/platform-tools/adb
    if parent.name.lower() == "platform-tools":
        sdk = parent.parent
        if sdk.is_dir():
            return str(sdk)
    return None


def _source_for_path(path: Path) -> str:
    text = str(path).replace("\\", "/").lower()
    plat = host_platform()
    if plat == "windows" or text.endswith(".exe"):
        if "mnt/c/" in text:
            return "windows-wsl"
        return "windows"
    if "mnt/c/" in text:
        return "windows-wsl"
    if "/library/android/" in text or plat == "macos":
        return "macos"
    return "linux"


def _common_adb_binaries() -> list[Path]:
    home = Path.home()
    candidates = [
        Path("/opt/homebrew/bin/adb"),
        Path("/usr/local/bin/adb"),
        home / "homebrew" / "bin" / "adb",
        Path("/opt/homebrew/Caskroom/android-platform-tools"),
        Path("/usr/bin/adb"),
        Path("/usr/lib/android-sdk/platform-tools/adb"),
    ]

    # Native Windows / Navin-managed platform-tools.
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    userprofile = (os.environ.get("USERPROFILE") or "").strip()
    if local:
        candidates.extend(
            [
                Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe",
                Path(local) / "Navin" / "android-platform-tools" / "platform-tools" / "adb.exe",
            ]
        )
    if userprofile:
        candidates.append(
            Path(userprofile)
            / "AppData"
            / "Local"
            / "Android"
            / "Sdk"
            / "platform-tools"
            / "adb.exe"
        )
    candidates.append(home / ".navin" / "android-platform-tools" / "platform-tools" / "adb.exe")
    candidates.append(home / ".navin" / "android-platform-tools" / "platform-tools" / "adb")

    # Scoop shims / apps
    if userprofile:
        candidates.append(Path(userprofile) / "scoop" / "shims" / "adb.exe")
        candidates.append(
            Path(userprofile) / "scoop" / "apps" / "adb" / "current" / "platform-tools" / "adb.exe"
        )

    # Expand caskroom versioned dirs -> platform-tools/adb
    expanded: list[Path] = []
    for path in candidates:
        if path.name == "android-platform-tools" and path.is_dir():
            try:
                for child in sorted(path.iterdir(), reverse=True):
                    adb = child / "platform-tools" / "adb"
                    if adb.is_file():
                        expanded.append(adb)
            except OSError:
                pass
            continue
        expanded.append(path)
    return expanded


def _sdk_candidates() -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            return
        if path.is_dir():
            seen.add(key)
            found.append(path)

    env = _sdk_from_env()
    if env is not None:
        add(env)

    home = Path.home()
    # macOS Android Studio default
    add(home / "Library" / "Android" / "sdk")
    # Linux / generic
    add(home / "Android" / "Sdk")
    add(Path("/usr/lib/android-sdk"))
    add(Path("/opt/android-sdk"))
    # Some Homebrew android-sdk formulas
    add(Path("/opt/homebrew/share/android-commandlinetools"))
    add(Path("/usr/local/share/android-commandlinetools"))

    # Windows native SDK locations
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        add(Path(local) / "Android" / "Sdk")
        add(Path(local) / "Navin" / "android-platform-tools")
    userprofile = (os.environ.get("USERPROFILE") or "").strip()
    if userprofile:
        add(Path(userprofile) / "AppData" / "Local" / "Android" / "Sdk")
        add(Path(userprofile) / "scoop" / "apps" / "android-sdk" / "current")
    add(home / ".navin" / "android-platform-tools")
    add(home / "AppData" / "Local" / "Android" / "Sdk")

    # WSL: scan Windows user profiles for Android Studio SDK.
    users = Path("/mnt/c/Users")
    if users.is_dir():
        skip = {"public", "default", "default user", "all users", "desktop.ini"}
        try:
            for entry in users.iterdir():
                if not entry.is_dir() or entry.name.lower() in skip:
                    continue
                add(entry / "AppData" / "Local" / "Android" / "Sdk")
        except OSError:
            pass

    return found
