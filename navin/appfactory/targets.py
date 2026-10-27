# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The seven App Factory targets and the technology imposed on each.

Desktop is always Tauri 2 + Rust, mobile is always Capacitor 8: other engines
(React Native/Expo, Flutter, Tauri Mobile, Electron, WinUI 3) are known but
never substituted automatically.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

ENGINES = {
    "capacitor": {"name": "Capacitor", "major": 8, "kind": "mobile"},
    "tauri": {"name": "Tauri", "major": 2, "kind": "desktop"},
}

OTHER_ENGINES = {
    "mobile": ("React Native / Expo", "Flutter", "Tauri Mobile"),
    "desktop": ("Electron", "WinUI 3"),
}

# Step ids of a conversion, in order. The report and the journal use them.
STEPS = ("analyze", "compat", "toolchain", "transform", "build", "test", "deliver")


@dataclass(frozen=True)
class Target:
    id: str
    kind: str  # mobile | desktop
    engine: str  # capacitor | tauri
    label: str
    deliverables: tuple[str, ...]  # file extensions, without the dot
    hosts: tuple[str, ...]  # platform.system() values that can build it
    bundles: tuple[str, ...]  # tauri --bundles values, or the capacitor platform
    ci_runner: str  # GitHub Actions runner able to build it
    priority: int  # 1 first: Windows and Android lead their kind

    @property
    def folder(self) -> str:
        return f"os/{self.id}"

    @property
    def platform(self) -> str:
        """Capacitor platform (android / ios) or Tauri OS family."""
        return self.bundles[0] if self.engine == "capacitor" else self.id

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["folder"] = self.folder
        data["engine_name"] = f"{ENGINES[self.engine]['name']} {ENGINES[self.engine]['major']}"
        return data


TARGETS: dict[str, Target] = {
    target.id: target
    for target in (
        Target("android", "mobile", "capacitor", "Android", ("apk", "aab"), ("Linux", "Windows", "Darwin"), ("android",), "ubuntu-latest", 1),
        Target("ios", "mobile", "capacitor", "iOS", ("ipa",), ("Darwin",), ("ios",), "macos-latest", 2),
        Target("windows", "desktop", "tauri", "Windows", ("exe", "msi"), ("Windows",), ("nsis", "msi"), "windows-latest", 1),
        Target("macos", "desktop", "tauri", "macOS", ("dmg", "app"), ("Darwin",), ("dmg", "app"), "macos-latest", 2),
        Target("ubuntu", "desktop", "tauri", "Ubuntu / Debian", ("deb",), ("Linux",), ("deb",), "ubuntu-22.04", 3),
        Target("centos", "desktop", "tauri", "CentOS / RHEL / Fedora", ("rpm",), ("Linux",), ("rpm",), "ubuntu-22.04", 3),
        Target("appimage", "desktop", "tauri", "Linux AppImage", ("AppImage",), ("Linux",), ("appimage",), "ubuntu-22.04", 3),
    )
}

# Words a user or the Desktop menu may use for a target.
ALIASES = {
    "mobile": "android",
    "apk": "android",
    "aab": "android",
    "iphone": "ios",
    "ipad": "ios",
    "desktop": "windows",
    "win": "windows",
    "exe": "windows",
    "msi": "windows",
    "mac": "macos",
    "osx": "macos",
    "dmg": "macos",
    "linux": "ubuntu",
    "debian": "ubuntu",
    "deb": "ubuntu",
    "rhel": "centos",
    "fedora": "centos",
    "rpm": "centos",
}


def get_target(name: str) -> Target:
    """The target for an id or alias; ValueError names the valid ones."""
    key = (name or "").strip().lower()
    key = ALIASES.get(key, key)
    if key not in TARGETS:
        raise ValueError(f"target must be one of {', '.join(TARGETS)} (got {name!r})")
    return TARGETS[key]


def default_app_id(slug: str, target: Target) -> str:
    """Reverse-DNS id: letters and digits only (Android forbids '-', macOS a '.app' end)."""
    clean = re.sub(r"[^a-z0-9]", "", (slug or "").lower())
    if not clean or not clean[0].isalpha():
        clean = "app" + clean
    return f"com.{clean}.{'mobile' if target.kind == 'mobile' else 'desktop'}"


APP_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")


def targets_payload() -> dict[str, Any]:
    return {
        "targets": [target.to_dict() for target in TARGETS.values()],
        "engines": {"mobile": "Capacitor 8", "desktop": "Tauri 2 + Rust"},
        "other_engines_never_substituted": OTHER_ENGINES,
        "folder_rule": "everything is generated in os/<target> at the project root; the web project is never overwritten",
    }
