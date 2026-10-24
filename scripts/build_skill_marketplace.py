#!/usr/bin/env python3
"""Build navin/marketplace.json, the catalog of bundled plugins and skills.

The catalog lets the agent pick the right command, agent or skill for each
step of a workflow. Plugins come from navin/builtin_plugins/*/plugin.json
(their commands live in navin/builtin_commands, their agents in
navin/builtin_agents); skills come from navin/skills/*/SKILL.md. Every path
in the catalog is relative to the navin package folder.

Run: python scripts/build_skill_marketplace.py          (write)
     python scripts/build_skill_marketplace.py --check  (exit 1 when stale)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "navin"
SKILLS = PKG / "skills"
OUT = PKG / "marketplace.json"
USES = re.compile(r"^(/[\w.-]+(?: .*)?|agent:[\w.-]+|skill:[\w.-]+)$")


def frontmatter(text: str) -> dict:
    """Top-level ``key: value`` pairs of a frontmatter block (``metadata`` as JSON)."""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    data: dict = {}
    if not m:
        return data
    for line in m.group(1).splitlines():
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, val = line.split(":", 1)
        val = val.strip()
        if key.strip() == "metadata":
            try:
                data["metadata"] = json.loads(val)
            except ValueError:
                pass
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        data[key.strip()] = val
    return data


def _rich_frontmatter(path: Path) -> dict:
    """Frontmatter of an agent or command (block scalars, unquoted examples)."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from navin.agent.frontmatter import split_frontmatter

    meta, _body, _error = split_frontmatter(path.read_text(encoding="utf-8"))
    return meta if isinstance(meta, dict) else {}


def _short(text: str, limit: int = 300) -> str:
    text = re.sub(r"<example>.*?</example>", " ", str(text or ""), flags=re.S)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def _skill_entries(skills: Path) -> list[dict]:
    entries = []
    for md in sorted(skills.glob("*/SKILL.md")):
        fm = frontmatter(md.read_text(encoding="utf-8"))
        meta = (fm.get("metadata") or {}).get("navin") or {}
        entry = {
            "name": fm.get("name") or md.parent.name,
            "description": fm.get("description", ""),
            "skill": f"skills/{md.parent.name}/SKILL.md",
            "category": meta.get("category", "general"),
        }
        bins = (meta.get("requires") or {}).get("bins")
        if bins:
            entry["requires"] = {"bins": bins}
        entries.append(entry)
    return entries


def _plugin_entry(manifest_path: Path, pkg: Path, skill_names: set[str]) -> dict:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    name = data["name"]
    errors: list[str] = []
    commands = []
    for command in data.get("commands", []):
        path = pkg / "builtin_commands" / f"{command}.md"
        if not path.is_file():
            errors.append(f"command /{command} has no {path.relative_to(pkg)}")
            continue
        meta = _rich_frontmatter(path)
        row = {
            "name": f"/{command}",
            "description": _short(meta.get("description", "")),
            "file": f"builtin_commands/{command}.md",
        }
        if meta.get("argument-hint"):
            row["argument_hint"] = str(meta["argument-hint"])
        commands.append(row)
    agents = []
    for agent in data.get("agents", []):
        path = pkg / "builtin_agents" / f"{agent}.md"
        if not path.is_file():
            errors.append(f"agent {agent} has no {path.relative_to(pkg)}")
            continue
        meta = _rich_frontmatter(path)
        agents.append({
            "name": agent,
            "description": _short(meta.get("description", "")),
            "file": f"builtin_agents/{agent}.md",
        })
    skills = []
    for skill in data.get("skills", []):
        if skill not in skill_names:
            errors.append(f"skill {skill} is not in skills/")
            continue
        skills.append({"name": skill, "skill": f"skills/{skill}/SKILL.md"})
    known = {c["name"] for c in commands} | {f"agent:{a['name']}" for a in agents}
    known |= {f"skill:{s['name']}" for s in skills}
    for step in data.get("steps", []):
        use = str(step.get("use", ""))
        target = use.split(" ", 1)[0]
        if not USES.match(use) or target not in known:
            errors.append(f"step {step.get('step')!r} uses {use!r}, which the plugin does not ship")
    if errors:
        raise SystemExit(f"{manifest_path}: " + "; ".join(errors))
    return {
        "name": name,
        "description": data.get("description", ""),
        "version": data.get("version", "1.0.0"),
        "category": data.get("category", "general"),
        "source": f"builtin_plugins/{manifest_path.parent.name}",
        "commands": commands,
        "agents": agents,
        "skills": skills,
        "steps": data.get("steps", []),
    }


def build_manifest(pkg: Path = PKG) -> dict:
    skills = _skill_entries(pkg / "skills")
    names = {entry["name"] for entry in skills} | {p.parent.name for p in (pkg / "skills").glob("*/SKILL.md")}
    plugins = [
        _plugin_entry(path, pkg, names)
        for path in sorted((pkg / "builtin_plugins").glob("*/plugin.json"))
    ]
    return {
        "name": "navin-marketplace",
        "version": "1.1.0",
        "description": (
            "Navin bundled plugins and skills. Each plugin maps workflow steps to the slash "
            "command, agent or skill to use; each skill says when to use it. Paths are "
            "relative to the navin package folder."
        ),
        "owner": {"name": "Navinspire", "email": "contact@navinspire.com"},
        "plugins": plugins,
        "skills": skills,
    }


def render(manifest: dict) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    text = render(build_manifest(PKG))
    if "--check" in args:
        current = OUT.read_text(encoding="utf-8") if OUT.is_file() else ""
        if current != text:
            print(f"{OUT} is stale: run python scripts/build_skill_marketplace.py", file=sys.stderr)
            return 1
        print(f"{OUT} is up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    data = json.loads(text)
    print(f"{len(data['plugins'])} plugins, {len(data['skills'])} skills -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
