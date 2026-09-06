"""Build a project marketing kit from workspace signals."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navin.montage import WORKSPACE_MONTAGE_DIR

_README_NAMES = ("README.md", "README.MD", "Readme.md", "readme.md")
_MAX_README_CHARS = 8_000
_ASSET_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".mp4", ".mov", ".webm"}
_CAPTURE_HINTS = ("screenshot", "capture", "preview", "mockup", "hero")


@dataclass(slots=True)
class ProjectKit:
    name: str
    summary: str
    stack: list[str] = field(default_factory=list)
    entrypoints: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    captures: list[str] = field(default_factory=list)
    brand_hints: list[str] = field(default_factory=list)
    channels_suggested: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_text(path: Path, limit: int = _MAX_README_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > limit:
        return text[:limit] + "\n… (truncated)"
    return text


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _discover_readme(root: Path) -> Path | None:
    for name in _README_NAMES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def _stack_from_package(pkg: dict[str, Any]) -> list[str]:
    deps = {
        **(pkg.get("dependencies") or {}),
        **(pkg.get("devDependencies") or {}),
    }
    hints: list[str] = []
    mapping = (
        ("next", "Next.js"),
        ("react", "React"),
        ("vue", "Vue"),
        ("svelte", "Svelte"),
        ("@angular/core", "Angular"),
        ("expo", "Expo"),
        ("react-native", "React Native"),
        ("vite", "Vite"),
        ("tailwindcss", "Tailwind"),
        ("express", "Express"),
        ("fastapi", "FastAPI (JS client)"),
    )
    for key, label in mapping:
        if key in deps:
            hints.append(label)
    return hints


def _stack_from_pyproject(text: str) -> list[str]:
    hints: list[str] = []
    lower = text.lower()
    for needle, label in (
        ("fastapi", "FastAPI"),
        ("django", "Django"),
        ("flask", "Flask"),
        ("streamlit", "Streamlit"),
        ("pytorch", "PyTorch"),
        ("tensorflow", "TensorFlow"),
        ("navin", "Navin"),
    ):
        if needle in lower:
            hints.append(label)
    return hints


def _collect_assets(root: Path, *, limit: int = 40) -> tuple[list[str], list[str]]:
    assets: list[str] = []
    captures: list[str] = []
    search_roots = [
        root / "public",
        root / "static",
        root / "assets",
        root / "marketing",
        root / "docs" / "images",
        root / "webui" / "public",
    ]
    for base in search_roots:
        if not base.is_dir():
            continue
        try:
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in _ASSET_EXTS:
                    continue
                rel = str(path.relative_to(root)).replace("\\", "/")
                assets.append(rel)
                name_l = path.name.lower()
                if any(h in name_l or h in rel.lower() for h in _CAPTURE_HINTS):
                    captures.append(rel)
                if len(assets) >= limit:
                    return assets, captures
        except OSError:
            continue
    return assets, captures


def _entrypoints(root: Path) -> list[str]:
    candidates = [
        "package.json",
        "pyproject.toml",
        "src/App.tsx",
        "src/main.tsx",
        "src/app/page.tsx",
        "app/page.tsx",
        "webui/index.html",
        "index.html",
        "README.md",
    ]
    found: list[str] = []
    for rel in candidates:
        if (root / rel).exists():
            found.append(rel)
    return found


def _brand_hints(readme: str, pkg: dict[str, Any] | None) -> list[str]:
    hints: list[str] = []
    if pkg and isinstance(pkg.get("name"), str) and pkg["name"].strip():
        hints.append(f"package name: {pkg['name'].strip()}")
    # First markdown H1
    for line in readme.splitlines():
        if line.startswith("# "):
            hints.append(f"title: {line[2:].strip()}")
            break
    # Hex colors in readme / simple tokens
    colors = re.findall(r"#[0-9A-Fa-f]{6}\b", readme)
    for color in colors[:6]:
        hints.append(f"color: {color}")
    return hints


def _summary_from_readme(readme: str, name: str) -> str:
    lines = [ln.strip() for ln in readme.splitlines() if ln.strip()]
    # Skip pure headings for the first prose line
    prose = [ln for ln in lines if not ln.startswith("#") and not ln.startswith("![")]
    if prose:
        return prose[0][:280]
    if lines:
        return lines[0].lstrip("# ").strip()[:280]
    return f"{name} - project marketing kit (fill in positioning)."


def analyze_project(root: Path) -> ProjectKit:
    root = root.resolve()
    name = root.name
    stack: list[str] = []
    readme_path = _discover_readme(root)
    readme = _read_text(readme_path) if readme_path else ""

    pkg = _load_json(root / "package.json")
    if pkg:
        if isinstance(pkg.get("name"), str) and pkg["name"].strip():
            name = pkg["name"].strip()
        stack.extend(_stack_from_package(pkg))

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        stack.extend(_stack_from_pyproject(_read_text(pyproject, limit=4000)))

    # Dedupe stack preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for item in stack:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    stack = deduped

    assets, captures = _collect_assets(root)
    brand = _brand_hints(readme, pkg)
    channels = ["LinkedIn", "X", "Instagram", "TikTok", "Product Hunt"]
    notes = [
        "Prefer real project captures over invented UI screenshots.",
        "Do not publish to social networks automatically - propose and generate only.",
        "Validate calendar + concepts with the user before expensive video batches.",
    ]
    if not captures:
        notes.append(
            "No screenshot-like assets found; plan montage(action=screenshot) or browser captures."
        )

    return ProjectKit(
        name=name,
        summary=_summary_from_readme(readme, name),
        stack=stack,
        entrypoints=_entrypoints(root),
        assets=assets,
        captures=captures,
        brand_hints=brand,
        channels_suggested=channels,
        notes=notes,
    )


def write_project_kit(root: Path, kit: ProjectKit | None = None) -> dict[str, Path]:
    """Write kit markdown + JSON under marketing/montage/."""
    kit = kit or analyze_project(root)
    out_dir = root / WORKSPACE_MONTAGE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    md_path = out_dir / "project-kit.md"
    json_path = out_dir / "project-kit.json"

    md_lines = [
        f"# Project marketing kit - {kit.name}",
        "",
        f"_Generated {stamp} (UTC)_",
        "",
        "## Summary",
        "",
        kit.summary,
        "",
        "## Stack",
        "",
    ]
    if kit.stack:
        md_lines.extend(f"- {item}" for item in kit.stack)
    else:
        md_lines.append("- (undetected - fill manually)")
    md_lines.extend(["", "## Entrypoints", ""])
    if kit.entrypoints:
        md_lines.extend(f"- `{p}`" for p in kit.entrypoints)
    else:
        md_lines.append("- (none)")
    md_lines.extend(["", "## Brand hints", ""])
    if kit.brand_hints:
        md_lines.extend(f"- {h}" for h in kit.brand_hints)
    else:
        md_lines.append("- (none detected)")
    md_lines.extend(["", "## Existing assets", ""])
    if kit.assets:
        md_lines.extend(f"- `{a}`" for a in kit.assets[:30])
    else:
        md_lines.append("- (none found under public/static/assets/marketing)")
    md_lines.extend(["", "## Capture candidates", ""])
    if kit.captures:
        md_lines.extend(f"- `{c}`" for c in kit.captures)
    else:
        md_lines.append("- (none - capture UI before social creatives)")
    md_lines.extend(["", "## Suggested channels", ""])
    md_lines.extend(f"- {ch}" for ch in kit.channels_suggested)
    md_lines.extend(["", "## Notes", ""])
    md_lines.extend(f"- {n}" for n in kit.notes)
    md_lines.append("")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    json_path.write_text(json.dumps(kit.to_dict(), indent=2) + "\n", encoding="utf-8")
    return {"markdown": md_path, "json": json_path}


def render_analyze_result(root: Path, paths: dict[str, Path], kit: ProjectKit) -> str:
    lines = [
        f"Project kit for {kit.name}:",
        f"  summary: {kit.summary}",
        f"  stack: {', '.join(kit.stack) or '(none)'}",
        f"  assets: {len(kit.assets)} | captures: {len(kit.captures)}",
        f"  markdown: {paths['markdown'].relative_to(root)}",
        f"  json: {paths['json'].relative_to(root)}",
        "",
        "Next: propose a content calendar (montage action=calendar), then wait for",
        "user validation before generate_image / generate_video batches.",
    ]
    return "\n".join(lines)
