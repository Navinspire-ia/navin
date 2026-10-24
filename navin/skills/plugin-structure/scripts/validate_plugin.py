#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Validate a Navin plugin folder (or a workspace .navin folder).

Usage:
    validate_plugin.py <plugin-dir> [--json]

Checks the manifest, skills, agents, commands, hooks and MCP config. Exit code
1 when a critical issue is found, 0 otherwise.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - PyYAML ships with Navin
    yaml = None

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}[a-z0-9]$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
FRONTMATTER_RE = re.compile(r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?", re.DOTALL)
HOOK_EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop"}
TOOL_EVENTS = {"PreToolUse", "PostToolUse"}
MCP_TYPES = {"stdio", "sse", "streamableHttp"}
FORBIDDEN_DASHES = ("\u2014", "\u2013")
# This script ships at navin/skills/plugin-structure/scripts/: the Navin
# package is three levels up. A standalone copy simply has no builtins.
_NAVIN_PKG = Path(__file__).resolve().parents[3]
# A manifest entry without a slash or extension names a component Navin ships.
BUILTIN_DIRS = {
    "agents": ("builtin_agents", "{name}.md"),
    "commands": ("builtin_commands", "{name}.md"),
    "skills": ("skills", "{name}/SKILL.md"),
}


def _is_bare_name(item: str) -> bool:
    return "/" not in item and "\\" not in item and "." not in item


class Report:
    def __init__(self) -> None:
        self.critical: list[str] = []
        self.warnings: list[str] = []
        self.ok: list[str] = []
        self.counts: dict[str, int] = {}

    def crit(self, msg: str) -> None:
        self.critical.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def good(self, msg: str) -> None:
        self.ok.append(msg)

    def as_dict(self) -> dict[str, Any]:
        return {
            "critical": self.critical,
            "warnings": self.warnings,
            "ok": self.ok,
            "components": self.counts,
            "passed": not self.critical,
        }


def _lenient_yaml(raw: str) -> dict[str, Any]:
    """Fallback for plain multi-line values holding ``user:`` example lines."""
    meta: dict[str, list[str]] = {}
    current: str | None = None
    in_example = False
    for line in raw.splitlines():
        lowered = line.lower()
        if "<example>" in lowered:
            in_example = True
        match = None if (in_example or line[:1].isspace()) else re.match(r"^([A-Za-z][\w-]*):(?:\s+(.*)|\s*)$", line)
        if match and match.group(1).lower() not in {"user", "assistant", "context", "commentary", "human"}:
            current = match.group(1)
            meta[current] = [match.group(2) or ""]
        elif current is not None:
            meta[current].append(line)
        if "</example>" in lowered:
            in_example = False
    result: dict[str, Any] = {}
    for key, lines in meta.items():
        text = "\n".join(lines).strip()
        if len(lines) == 1 and yaml is not None and text[:1] in "[{\"'":
            try:
                result[key] = yaml.safe_load(text)
                continue
            except yaml.YAMLError:
                pass
        result[key] = text
    return result


def parse_frontmatter(text: str) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return (meta, body, error). meta is None when there is no frontmatter.

    Strict YAML first; descriptions with unquoted ``<example>`` dialogue fall
    back to the same line-based reading Navin uses at runtime.
    """
    if not text.startswith("---"):
        return None, text, None
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None, text, "frontmatter opened with --- but never closed"
    raw = match.group(1)
    body = text[match.end():]
    if yaml is None:
        return _lenient_yaml(raw), body, None
    try:
        meta = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        meta = _lenient_yaml(raw)
    if not isinstance(meta, dict):
        return None, body, "frontmatter is not a mapping"
    return meta, body, None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _check_dashes(path: Path, text: str, root: Path, report: Report) -> None:
    if any(ch in text for ch in FORBIDDEN_DASHES):
        report.warn(f"{_rel(path, root)}: contains a long dash; use '-' instead")


def _builtin_target(root: Path, key: str, name: str) -> Path | None:
    """The plugin's own component of that name, else the one Navin ships."""
    folder, pattern = BUILTIN_DIRS[key]
    own = root / key / pattern.format(name=name)
    if own.is_file():
        return own
    shipped = _NAVIN_PKG / folder / pattern.format(name=name)
    return shipped if shipped.is_file() else None


def _custom_paths(root: Path, manifest: dict[str, Any], key: str, report: Report) -> list[Path]:
    value = manifest.get(key)
    if value is None or isinstance(value, dict):
        return []
    values = value if isinstance(value, list) else [value]
    paths: list[Path] = []
    for item in values:
        if not isinstance(item, str) or not item.strip():
            report.crit(f"plugin.json: '{key}' entries must be non-empty strings")
            continue
        cleaned = item.strip().replace("\\", "/")
        if key in BUILTIN_DIRS and _is_bare_name(cleaned) and not (root / cleaned).exists():
            target = _builtin_target(root, key, cleaned)
            if target is None:
                report.crit(f"plugin.json: '{key}' names {item!r}, which neither the plugin nor Navin ships")
            else:
                paths.append(target)
            continue
        if cleaned.startswith("/") or ".." in Path(cleaned).parts:
            report.crit(f"plugin.json: '{key}' path {item!r} must stay inside the plugin")
            continue
        target = (root / cleaned).resolve()
        if not target.exists():
            report.crit(f"plugin.json: '{key}' path {item!r} does not exist")
            continue
        paths.append(target)
    return paths


def check_manifest(root: Path, report: Report) -> dict[str, Any]:
    path = root / "plugin.json"
    if not path.is_file():
        report.warn("plugin.json missing: the folder name will be used as the plugin name")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.crit(f"plugin.json: invalid JSON ({exc})")
        return {}
    if not isinstance(data, dict):
        report.crit("plugin.json: must contain a JSON object")
        return {}
    name = data.get("name")
    if name is None:
        report.warn("plugin.json: no 'name'; the folder name will be used")
    elif not isinstance(name, str) or not NAME_RE.match(name):
        report.crit(f"plugin.json: invalid name {name!r} (lowercase letters, digits, '.', '_', '-')")
    else:
        report.good(f"manifest name '{name}'")
    version = data.get("version")
    if version is None:
        report.warn("plugin.json: no 'version' (use semantic versioning, e.g. 0.1.0)")
    elif not isinstance(version, str) or not SEMVER_RE.match(version):
        report.warn(f"plugin.json: version {version!r} is not semantic versioning")
    if not data.get("description"):
        report.warn("plugin.json: add a 'description'")
    check_steps(data, report)
    return data


STEP_USE_RE = re.compile(r"^(/[\w.:-]+|agent:[\w-]+|skill:[\w-]+)")


def check_steps(manifest: dict[str, Any], report: Report) -> None:
    """Each workflow step must point at a command, agent or skill the manifest lists."""
    steps = manifest.get("steps")
    if steps is None:
        return
    if not isinstance(steps, list):
        report.crit("plugin.json: 'steps' must be a list")
        return

    def listed(key: str) -> set[str]:
        value = manifest.get(key)
        items = value if isinstance(value, list) else [value] if isinstance(value, str) else []
        return {Path(str(i)).stem if not _is_bare_name(str(i)) else str(i) for i in items}

    known = {f"/{n}" for n in listed("commands")} | {f"agent:{n}" for n in listed("agents")}
    known |= {f"skill:{n}" for n in listed("skills")}
    for step in steps:
        use = str(step.get("use", "")) if isinstance(step, dict) else ""
        match = STEP_USE_RE.match(use)
        if not isinstance(step, dict) or not step.get("step") or not match:
            report.crit(f"plugin.json: step {step!r} needs 'step' and 'use' (/command, agent:name or skill:name)")
        elif match.group(1) not in known:
            report.crit(f"plugin.json: step {step.get('step')!r} uses {use!r}, which the manifest does not list")


def check_skills(root: Path, report: Report, manifest: dict[str, Any] | None = None) -> int:
    skills_dir = root / "skills"
    listed = _custom_paths(root, manifest or {}, "skills", report)
    folders = sorted(p for p in skills_dir.iterdir() if p.is_dir()) if skills_dir.is_dir() else []
    known = {p.resolve() for p in folders}
    folders += [p.parent for p in listed if p.name == "SKILL.md" and p.parent.resolve() not in known]
    folders += [p for p in listed if p.is_dir() and p.resolve() not in known]
    if not folders:
        return 0
    count = 0
    for folder in folders:
        skill_md = folder / "SKILL.md"
        if not skill_md.is_file():
            report.crit(f"skills/{folder.name}: missing SKILL.md")
            continue
        count += 1
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        _check_dashes(skill_md, text, root, report)
        meta, body, error = parse_frontmatter(text)
        where = f"skills/{folder.name}/SKILL.md"
        if error:
            report.crit(f"{where}: {error}")
            continue
        if meta is None:
            report.crit(f"{where}: missing YAML frontmatter with name and description")
            continue
        name = meta.get("name")
        if not isinstance(name, str) or not name.strip():
            report.crit(f"{where}: frontmatter 'name' is required")
        else:
            if not SKILL_NAME_RE.match(name.strip()):
                report.warn(f"{where}: name {name!r} should be kebab-case")
            if name.strip() != folder.name:
                report.warn(f"{where}: name {name!r} differs from folder {folder.name!r}")
        desc = meta.get("description")
        if not isinstance(desc, str) or not desc.strip():
            report.crit(f"{where}: frontmatter 'description' is required")
        else:
            if len(desc) < 40:
                report.warn(f"{where}: description is short; add concrete trigger phrases")
            if len(desc) > 1024:
                report.warn(f"{where}: description over 1024 characters; move detail to the body")
        words = len(body.split())
        if words > 5000:
            report.warn(f"{where}: body has {words} words; move detail to references/")
        for ref in sorted(set(re.findall(r"`((?:references|examples|scripts|assets)/[\w./-]+\.\w+)`", body))):
            if not (folder / ref).exists():
                report.crit(f"{where}: references missing file {ref}")
    return count


def _md_files(*bases: Path) -> list[Path]:
    files: list[Path] = []
    for base in bases:
        if base.is_file() and base.suffix == ".md":
            files.append(base)
        elif base.is_dir():
            files.extend(p for p in sorted(base.rglob("*.md")) if p.stem.lower() != "readme")
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in files:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def check_agents(root: Path, manifest: dict[str, Any], report: Report) -> int:
    files = _md_files(root / "agents", *_custom_paths(root, manifest, "agents", report))
    seen: set[str] = set()
    for path in files:
        where = _rel(path, root)
        text = path.read_text(encoding="utf-8", errors="replace")
        _check_dashes(path, text, root, report)
        meta, body, error = parse_frontmatter(text)
        if error:
            report.crit(f"{where}: {error}")
            continue
        if meta is None:
            report.crit(f"{where}: missing frontmatter (name, description)")
            continue
        name = str(meta.get("name") or path.stem).strip()
        if not AGENT_NAME_RE.match(name):
            report.crit(f"{where}: agent name {name!r} must be 3-50 chars, lowercase letters, digits and hyphens")
        if name in seen:
            report.crit(f"{where}: duplicate agent name {name!r}")
        seen.add(name)
        desc = meta.get("description")
        if not isinstance(desc, str) or len(desc.strip()) < 20:
            report.crit(f"{where}: description is required and must say when to use the agent")
        elif "<example>" not in desc:
            report.warn(f"{where}: add 2-4 <example> blocks to the description")
        tools = meta.get("tools")
        if tools is not None and not isinstance(tools, (list, str)):
            report.crit(f"{where}: 'tools' must be a list or a comma-separated string")
        if len(body.strip()) < 20:
            report.crit(f"{where}: the body (system prompt) is empty or too short")
        elif "you " not in body.lower():
            report.warn(f"{where}: write the system prompt in second person (\"You are ...\")")
    return len(files)


def check_commands(root: Path, manifest: dict[str, Any], report: Report) -> int:
    files = _md_files(root / "commands", *_custom_paths(root, manifest, "commands", report))
    for path in files:
        where = _rel(path, root)
        text = path.read_text(encoding="utf-8", errors="replace")
        _check_dashes(path, text, root, report)
        meta, body, error = parse_frontmatter(text)
        if error:
            report.crit(f"{where}: {error}")
            continue
        if not re.match(r"^[A-Za-z0-9][\w.-]*$", path.stem):
            report.crit(f"{where}: command file name must be letters, digits, '.', '_' or '-'")
        if not body.strip():
            report.crit(f"{where}: the command body is empty")
        if meta is None or not meta.get("description"):
            report.warn(f"{where}: add a 'description' so the palette and the prompt can show it")
        if meta and meta.get("allowed-tools") is not None and not isinstance(meta["allowed-tools"], (list, str)):
            report.crit(f"{where}: 'allowed-tools' must be a list or a comma-separated string")
        if "!`" in body and meta is not None:
            allowed = str(meta.get("allowed-tools") or "")
            if not re.search(r"\b(exec|bash)\b", allowed, re.IGNORECASE):
                report.warn(f"{where}: uses !`...` shell expansion but allowed-tools does not grant exec")
    return len(files)


def validate_hooks_config(data: Any, where: str, report: Report) -> int:
    if isinstance(data, dict) and isinstance(data.get("hooks"), dict):
        data = data["hooks"]
    if not isinstance(data, dict):
        report.crit(f"{where}: must be an object mapping events to hook lists")
        return 0
    total = 0
    for event, entries in data.items():
        if event in ("description",):
            continue
        if event not in HOOK_EVENTS:
            report.crit(f"{where}: unknown event {event!r} (expected one of {', '.join(sorted(HOOK_EVENTS))})")
            continue
        if not isinstance(entries, list):
            report.crit(f"{where}: {event} must be a list")
            continue
        for index, entry in enumerate(entries):
            label = f"{where}: {event}[{index}]"
            if not isinstance(entry, dict):
                report.crit(f"{label} must be an object")
                continue
            matcher = entry.get("matcher")
            if matcher is not None:
                if not isinstance(matcher, str):
                    report.crit(f"{label}: matcher must be a string")
                else:
                    if event not in TOOL_EVENTS and matcher not in ("", "*"):
                        report.warn(f"{label}: matcher is ignored for {event}")
                    try:
                        re.compile(matcher if matcher not in ("", "*") else ".*")
                    except re.error as exc:
                        report.crit(f"{label}: matcher is not a valid regex ({exc})")
            hooks = entry.get("hooks")
            if not isinstance(hooks, list) or not hooks:
                report.crit(f"{label}: 'hooks' must be a non-empty list")
                continue
            for h_index, hook in enumerate(hooks):
                h_label = f"{label}.hooks[{h_index}]"
                if not isinstance(hook, dict):
                    report.crit(f"{h_label} must be an object")
                    continue
                if hook.get("type", "command") != "command":
                    report.crit(f"{h_label}: only type 'command' is supported")
                command = hook.get("command")
                if not isinstance(command, str) or not command.strip():
                    report.crit(f"{h_label}: 'command' is required")
                    continue
                timeout = hook.get("timeout")
                if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0 or timeout > 600):
                    report.crit(f"{h_label}: timeout must be a number of seconds between 1 and 600")
                if re.search(r"(^|\s)/(home|Users|tmp)/", command):
                    report.warn(f"{h_label}: absolute path in command; use ${{NAVIN_PLUGIN_ROOT}}")
                total += 1
    return total


def check_hooks(root: Path, manifest: dict[str, Any], report: Report) -> int:
    total = 0
    sources: list[tuple[str, Any]] = []
    default = root / "hooks" / "hooks.json"
    if default.is_file():
        sources.append(("hooks/hooks.json", default))
    custom = manifest.get("hooks")
    if isinstance(custom, dict):
        sources.append(("plugin.json:hooks", custom))
    elif isinstance(custom, str):
        for path in _custom_paths(root, manifest, "hooks", report):
            sources.append((_rel(path, root), path))
    for where, source in sources:
        if isinstance(source, Path):
            try:
                data = json.loads(source.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                report.crit(f"{where}: invalid JSON ({exc})")
                continue
        else:
            data = source
        total += validate_hooks_config(data, where, report)
    scripts = root / "hooks" / "scripts"
    if scripts.is_dir():
        for script in sorted(scripts.glob("*.sh")):
            if not script.stat().st_mode & 0o111:
                report.warn(f"{_rel(script, root)}: not executable (chmod +x)")
    return total


def check_mcp(root: Path, report: Report) -> int:
    path = root / "mcp.json"
    if not path.is_file():
        return 0
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.crit(f"mcp.json: invalid JSON ({exc})")
        return 0
    servers = data.get("mcpServers", data) if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        report.crit("mcp.json: must contain an 'mcpServers' object")
        return 0
    for name, conf in servers.items():
        where = f"mcp.json: server {name!r}"
        if not isinstance(conf, dict):
            report.crit(f"{where} must be an object")
            continue
        kind = conf.get("type")
        if kind is not None and kind not in MCP_TYPES:
            report.crit(f"{where}: type must be one of {', '.join(sorted(MCP_TYPES))}")
        if not conf.get("command") and not conf.get("url"):
            report.crit(f"{where}: needs 'command' (stdio) or 'url' (sse / streamableHttp)")
        url = str(conf.get("url") or "")
        if url.startswith("http://") and not re.match(r"^http://(localhost|127\.0\.0\.1)", url):
            report.warn(f"{where}: use https for remote servers")
        for key, value in (conf.get("headers") or {}).items():
            if isinstance(value, str) and re.search(r"(Bearer\s+[A-Za-z0-9._-]{16,}|sk-[A-Za-z0-9]{16,})", value):
                report.crit(f"{where}: header {key!r} looks like a hard-coded secret; use ${{ENV_VAR}}")
    return len(servers)


def validate(root: Path) -> Report:
    report = Report()
    if not root.is_dir():
        report.crit(f"not a directory: {root}")
        return report
    manifest = check_manifest(root, report)
    report.counts = {
        "skills": check_skills(root, report, manifest),
        "agents": check_agents(root, manifest, report),
        "commands": check_commands(root, manifest, report),
        "hooks": check_hooks(root, manifest, report),
        "mcp_servers": check_mcp(root, report),
    }
    if not any(report.counts.values()):
        report.crit("no components found (skills/, agents/, commands/, hooks/hooks.json or mcp.json)")
    if not (root / "README.md").is_file():
        report.warn("README.md missing: explain what the plugin does and how to use it")
    return report


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if len(args) != 1:
        print(__doc__.strip())
        return 2
    root = Path(args[0]).expanduser().resolve()
    report = validate(root)
    if as_json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(f"Plugin validation: {root}")
        print("Components: " + ", ".join(f"{k}={v}" for k, v in report.counts.items()))
        for label, items in (("CRITICAL", report.critical), ("WARNING", report.warnings), ("OK", report.ok)):
            for item in items:
                print(f"[{label}] {item}")
        print("Result: " + ("PASS" if not report.critical else f"FAIL ({len(report.critical)} critical)"))
    return 0 if not report.critical else 1


if __name__ == "__main__":
    sys.exit(main())
