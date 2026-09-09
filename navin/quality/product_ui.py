# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Product-quality gates for UI source the agent ships.

Catches the junk that slips past ordinary linters:
- Unicode em/en dashes in user-facing copy (forbidden project-wide)
- Placeholder / fake UI ("Coming soon", dead buttons, lorem)
- Missing framer-motion on greenfield React package.json
- Missing Three.js stack (three + R3F + drei) on a new React UI
- Missing official design system (MUI / Fluent / Carbon) on a new React UI

These diagnostics feed ``verify`` so the agent cannot claim done on a
cardboard CRM.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from navin.quality.linters import Diagnostic, LinterResult

_EM_DASH = "\u2014"  # U+2014
_EN_DASH = "\u2013"  # U+2013

_UI_SUFFIXES = {
    ".tsx",
    ".jsx",
    ".ts",
    ".js",
    ".vue",
    ".svelte",
    ".html",
    ".css",
    ".scss",
    ".mdx",
    ".json",
}

_SKIP_PARTS = {
    "node_modules",
    "dist",
    "build",
    ".next",
    "coverage",
    "vendor",
    ".git",
    "__pycache__",
}

# Fake / unfinished UI the model loves to ship as "done".
_STUB_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ui-stub-coming-soon",
        re.compile(r"coming\s+soon", re.IGNORECASE),
    ),
    (
        "ui-stub-lorem",
        re.compile(r"lorem\s+ipsum", re.IGNORECASE),
    ),
    (
        "ui-stub-placeholder-click",
        re.compile(
            r"onClick\s*=\s*\{\s*\(\s*\)\s*=>\s*\{\s*\}\s*\}|"
            r"onClick\s*=\s*\{\s*\(\s*\)\s*=>\s*undefined\s*\}|"
            r"onClick\s*=\s*\{\s*\(\s*\)\s*=>\s*null\s*\}|"
            r"onClick\s*=\s*\{\s*noop\s*\}",
            re.IGNORECASE,
        ),
    ),
    (
        "ui-stub-todo-handler",
        re.compile(
            r"onClick\s*=\s*\{\s*\(\s*\)\s*=>\s*(?:alert|console\.(?:log|warn|info))\s*\(",
            re.IGNORECASE,
        ),
    ),
    (
        "ui-stub-not-implemented",
        re.compile(r"not\s+implemented|TODO:\s*wire|FIXME:\s*ui", re.IGNORECASE),
    ),
    (
        "ui-stub-dummy-data-label",
        re.compile(r"\b(dummy|fake|mock)\s+(data|user|client|dashboard)\b", re.IGNORECASE),
    ),
)

_MAX_FILE_BYTES = 400_000
_MAX_FINDINGS = 80


def _should_scan(rel: str) -> bool:
    path = Path(rel)
    if any(part in _SKIP_PARTS for part in path.parts):
        return False
    suffix = path.suffix.lower()
    if suffix not in _UI_SUFFIXES:
        return False
    # Skip pure backend / config-ish names unless they look like UI i18n.
    name = path.name.lower()
    if name in {"package-lock.json", "tsconfig.json", "tsconfig.app.json"}:
        return False
    return True


def scan_file_for_product_issues(root: Path, rel: str) -> list[Diagnostic]:
    """Return product-quality diagnostics for one relative path."""
    if not _should_scan(rel):
        return []
    full = root / rel
    try:
        raw = full.read_bytes()
    except OSError:
        return []
    if len(raw) > _MAX_FILE_BYTES:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return []

    findings: list[Diagnostic] = []
    lines = text.splitlines()

    for idx, line in enumerate(lines, start=1):
        if _EM_DASH in line or _EN_DASH in line:
            col = line.find(_EM_DASH)
            if col < 0:
                col = line.find(_EN_DASH)
            findings.append(
                Diagnostic(
                    path=rel,
                    line=idx,
                    col=max(1, col + 1),
                    end_line=idx,
                    end_col=max(2, col + 2),
                    severity="error",
                    code="no-em-dash",
                    message=(
                        "Forbidden em/en dash (U+2014 / U+2013). "
                        "Use a plain hyphen '-' or rephrase."
                    ),
                    tool="product-ui",
                )
            )
        for code, pattern in _STUB_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            # package.json / lock metadata can mention "coming soon" rarely; still flag UI.
            findings.append(
                Diagnostic(
                    path=rel,
                    line=idx,
                    col=max(1, match.start() + 1),
                    end_line=idx,
                    end_col=max(2, match.end() + 1),
                    severity="error",
                    code=code,
                    message=(
                        "Looks like unfinished / fake UI. Wire a real action, "
                        "real data, or remove the control before calling the app done."
                    ),
                    tool="product-ui",
                )
            )
        if len(findings) >= _MAX_FINDINGS:
            break

    if rel.endswith("package.json") or rel.replace("\\", "/").endswith("/package.json"):
        findings.extend(_framer_motion_diags(rel, text))
        findings.extend(_three_stack_diags(rel, text))
        findings.extend(_official_ds_diags(rel, text))

    return findings[:_MAX_FINDINGS]


def _framer_motion_diags(rel: str, text: str) -> list[Diagnostic]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    deps = {}
    for key in ("dependencies", "devDependencies"):
        block = payload.get(key)
        if isinstance(block, dict):
            deps.update(block)
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return []
    script_blob = " ".join(str(v) for v in scripts.values()).lower()
    is_web_ui = any(
        token in script_blob or token in deps
        for token in ("vite", "next", "react-scripts", "react", "next")
    )
    if not is_web_ui:
        return []
    if "framer-motion" in deps or "motion" in deps:
        return []
    # Only error when React is clearly a UI dependency.
    if "react" not in deps and "next" not in deps:
        return []
    return [
        Diagnostic(
            path=rel,
            line=1,
            col=1,
            end_line=1,
            end_col=2,
            severity="error",
            code="missing-framer-motion",
            message=(
                "Web UI package.json is missing framer-motion (or motion). "
                "Install it and use real page/section motion before delivery."
            ),
            tool="product-ui",
        )
    ]


_THREE_STACK = (
    "three",
    "@react-three/fiber",
    "@react-three/drei",
)


def _three_stack_diags(rel: str, text: str) -> list[Diagnostic]:
    """Dev web UIs must ship three + R3F + drei. Navin editor UI is exempt."""
    norm = rel.replace("\\", "/")
    if any(norm == prefix.rstrip("/") or norm.startswith(prefix) for prefix in _SKIP_DS_PREFIXES):
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    deps: dict[str, object] = {}
    for key in ("dependencies", "devDependencies"):
        block = payload.get(key)
        if isinstance(block, dict):
            deps.update(block)
    scripts = payload.get("scripts")
    if not isinstance(scripts, dict):
        return []
    script_blob = " ".join(str(v) for v in scripts.values()).lower()
    is_web_ui = any(
        token in script_blob or token in deps
        for token in ("vite", "next", "react-scripts", "react", "next")
    )
    if not is_web_ui:
        return []
    if "react" not in deps and "next" not in deps:
        return []
    missing = [name for name in _THREE_STACK if name not in deps]
    if not missing:
        return []
    return [
        Diagnostic(
            path=rel,
            line=1,
            col=1,
            end_line=1,
            end_col=2,
            severity="error",
            code="missing-three-stack",
            message=(
                "Web UI package.json is missing "
                + ", ".join(missing)
                + ". Install three + @react-three/fiber + @react-three/drei "
                "and ship a designed 3D layer (not wallpaper) before delivery."
            ),
            tool="product-ui",
        )
    ]


_OFFICIAL_DS = (
    "@mui/material",
    "@fluentui/react",
    "@fluentui/react-components",
    "@carbon/react",
    "carbon-components-react",
)
_EXISTING_KIT = (
    "shadcn",
    "@radix-ui/react-slot",
    "antd",
    "@chakra-ui/react",
    "@mantine/core",
)
_SKIP_DS_PREFIXES = (
    "webui/",
    "site/front/",
    "templates_apps/",
    "navin/webui/",
)


def _official_ds_diags(rel: str, text: str) -> list[Diagnostic]:
    """New React UIs must lock MUI, Fluent, or Carbon. Existing kits stay put."""
    norm = rel.replace("\\", "/")
    if any(norm == prefix.rstrip("/") or norm.startswith(prefix) for prefix in _SKIP_DS_PREFIXES):
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    deps: dict[str, object] = {}
    for key in ("dependencies", "devDependencies"):
        block = payload.get(key)
        if isinstance(block, dict):
            deps.update(block)
    if "react" not in deps and "next" not in deps:
        return []
    if any(name in deps for name in _OFFICIAL_DS):
        return []
    if any(name in deps for name in _EXISTING_KIT):
        return []
    scripts = payload.get("scripts")
    script_blob = " ".join(str(v) for v in scripts.values()).lower() if isinstance(scripts, dict) else ""
    is_web_ui = any(
        token in script_blob or token in deps
        for token in ("vite", "next", "react-scripts", "react")
    )
    if not is_web_ui:
        return []
    return [
        Diagnostic(
            path=rel,
            line=1,
            col=1,
            end_line=1,
            end_col=2,
            severity="error",
            code="missing-official-ds",
            message=(
                "Web UI has no official design system. Lock Google (@mui/material), "
                "Microsoft (@fluentui/react), or IBM (@carbon/react) before delivery. "
                "Do not default to Tailwind or a homemade kit."
            ),
            tool="product-ui",
        )
    ]


def lint_product_ui(root: Path, paths: list[str]) -> LinterResult:
    """Scan changed UI paths for product-quality failures."""
    diagnostics: list[Diagnostic] = []
    for rel in paths:
        diagnostics.extend(scan_file_for_product_issues(root, rel))
        if len(diagnostics) >= _MAX_FINDINGS:
            break
    return LinterResult(
        linter="product-ui",
        ran=True,
        diagnostics=diagnostics[:_MAX_FINDINGS],
    )
