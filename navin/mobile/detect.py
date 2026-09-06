"""Detect Expo, React Native, and Flutter projects from workspace files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

MobileKind = Literal["expo", "react-native", "flutter", "none"]
PackageManager = Literal["npm", "yarn", "pnpm", "bun", "unknown"]

_APP_CONFIG_NAMES = (
    "app.json",
    "app.config.js",
    "app.config.ts",
    "app.config.cjs",
    "app.config.mjs",
)


@dataclass(slots=True)
class MobileProject:
    """Structured view of a mobile workspace root."""

    kind: MobileKind
    root: Path
    name: str = ""
    package_manager: PackageManager = "unknown"
    has_android: bool = False
    has_ios: bool = False
    entry: str = ""
    scripts: dict[str, str] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)

    @property
    def is_mobile(self) -> bool:
        return self.kind != "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "root": str(self.root),
            "name": self.name,
            "package_manager": self.package_manager,
            "has_android": self.has_android,
            "has_ios": self.has_ios,
            "entry": self.entry,
            "scripts": dict(self.scripts),
            "evidence": list(self.evidence),
            "dependencies": list(self.dependencies),
        }

    def render(self) -> str:
        if self.kind == "none":
            return (
                "No mobile project detected in this workspace.\n"
                "Looked for Expo (expo + app config), React Native "
                "(react-native dependency), and Flutter (pubspec.yaml)."
            )
        lines = [
            f"Mobile project: {self.kind}",
            f"Root: {self.root}",
        ]
        if self.name:
            lines.append(f"Name: {self.name}")
        if self.package_manager != "unknown":
            lines.append(f"Package manager: {self.package_manager}")
        platforms = []
        if self.has_android:
            platforms.append("android")
        if self.has_ios:
            platforms.append("ios")
        lines.append(
            "Native folders: " + (", ".join(platforms) if platforms else "none yet")
        )
        if self.entry:
            lines.append(f"Entry: {self.entry}")
        if self.dependencies:
            lines.append("Key deps: " + ", ".join(self.dependencies))
        if self.scripts:
            interesting = [
                f"{k}={v}"
                for k, v in self.scripts.items()
                if k in {"start", "android", "ios", "web", "expo", "dev"}
            ]
            if interesting:
                lines.append("Scripts: " + "; ".join(interesting))
        if self.evidence:
            lines.append("Evidence:")
            for item in self.evidence:
                lines.append(f"  - {item}")
        return "\n".join(lines)


def detect_mobile_project(root: Path) -> MobileProject:
    """Inspect ``root`` and return the best-matching mobile project kind."""
    root = root.expanduser().resolve(strict=False)
    flutter = _detect_flutter(root)
    if flutter is not None:
        return flutter

    package = _read_package_json(root)
    if package is None:
        return MobileProject(kind="none", root=root)

    deps = {
        **(package.get("dependencies") or {}),
        **(package.get("devDependencies") or {}),
        **(package.get("peerDependencies") or {}),
    }
    dep_names = sorted(str(name) for name in deps)
    scripts = {
        str(k): str(v)
        for k, v in (package.get("scripts") or {}).items()
        if isinstance(v, str)
    }
    name = str(package.get("name") or root.name)
    pm = _detect_package_manager(root)
    has_android = (root / "android").is_dir()
    has_ios = (root / "ios").is_dir()
    entry = _guess_entry(root, package)

    evidence: list[str] = ["package.json"]
    key_deps: list[str] = []
    has_expo = "expo" in deps
    has_rn = "react-native" in deps
    if has_expo:
        key_deps.append(f"expo@{deps['expo']}")
        evidence.append("dependency: expo")
    if has_rn:
        key_deps.append(f"react-native@{deps['react-native']}")
        evidence.append("dependency: react-native")

    app_config = _find_app_config(root)
    expo_from_config = False
    if app_config is not None:
        evidence.append(app_config.name)
        expo_from_config = _app_config_looks_like_expo(app_config)

    if has_expo or expo_from_config:
        if expo_from_config and not has_expo:
            evidence.append("app config references Expo")
        return MobileProject(
            kind="expo",
            root=root,
            name=name,
            package_manager=pm,
            has_android=has_android,
            has_ios=has_ios,
            entry=entry,
            scripts=scripts,
            evidence=evidence,
            dependencies=key_deps or ["expo"],
        )

    if has_rn:
        return MobileProject(
            kind="react-native",
            root=root,
            name=name,
            package_manager=pm,
            has_android=has_android,
            has_ios=has_ios,
            entry=entry,
            scripts=scripts,
            evidence=evidence,
            dependencies=key_deps,
        )

    # package.json exists but no mobile markers
    _ = dep_names
    return MobileProject(
        kind="none",
        root=root,
        name=name,
        package_manager=pm,
        has_android=has_android,
        has_ios=has_ios,
        entry=entry,
        scripts=scripts,
        evidence=["package.json without expo/react-native"],
    )


def _detect_flutter(root: Path) -> MobileProject | None:
    pubspec = root / "pubspec.yaml"
    if not pubspec.is_file():
        return None
    try:
        text = pubspec.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not re.search(r"(?m)^\s*flutter\s*:", text) and "sdk: flutter" not in text:
        return None
    name_match = re.search(r"(?m)^name:\s*([^\s#]+)", text)
    name = name_match.group(1) if name_match else root.name
    return MobileProject(
        kind="flutter",
        root=root,
        name=name,
        package_manager="unknown",
        has_android=(root / "android").is_dir(),
        has_ios=(root / "ios").is_dir(),
        entry="lib/main.dart" if (root / "lib" / "main.dart").is_file() else "",
        evidence=["pubspec.yaml (flutter sdk)"],
        dependencies=["flutter"],
    )


def _read_package_json(root: Path) -> dict[str, Any] | None:
    path = root / "package.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _detect_package_manager(root: Path) -> PackageManager:
    if (root / "bun.lockb").is_file() or (root / "bun.lock").is_file():
        return "bun"
    if (root / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (root / "yarn.lock").is_file():
        return "yarn"
    if (root / "package-lock.json").is_file():
        return "npm"
    return "npm" if (root / "package.json").is_file() else "unknown"


def _find_app_config(root: Path) -> Path | None:
    for name in _APP_CONFIG_NAMES:
        path = root / name
        if path.is_file():
            return path
    return None


def _app_config_looks_like_expo(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if path.name == "app.json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return "expo" in text.lower()
        if isinstance(data, dict) and isinstance(data.get("expo"), dict):
            return True
        return False
    return "expo" in text.lower()


def _guess_entry(root: Path, package: dict[str, Any]) -> str:
    main = package.get("main")
    if isinstance(main, str) and main.strip():
        return main.strip()
    for candidate in (
        "index.js",
        "index.ts",
        "index.tsx",
        "App.tsx",
        "App.ts",
        "App.jsx",
        "App.js",
        "src/App.tsx",
        "src/App.ts",
        "app/_layout.tsx",
        "app/index.tsx",
    ):
        if (root / candidate).is_file():
            return candidate
    return ""
