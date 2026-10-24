#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Create a Navin plugin skeleton.

Usage:
    scaffold_plugin.py <target-dir> --name my-plugin \
        [--description "..."] [--author "Jane"] \
        [--components skills,agents,commands,hooks,mcp]

Existing files are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
KINDS = ("skills", "agents", "commands", "hooks", "mcp")


def _write(path: Path, content: str, created: list[str]) -> None:
    if path.exists():
        print(f"skip (exists): {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    created.append(str(path))


def scaffold(target: Path, name: str, description: str, author: str, kinds: list[str]) -> list[str]:
    created: list[str] = []
    manifest = {
        "name": name,
        "version": "0.1.0",
        "description": description or f"{name} plugin",
    }
    if author:
        manifest["author"] = {"name": author}
    _write(target / "plugin.json", json.dumps(manifest, indent=2) + "\n", created)
    _write(
        target / "README.md",
        f"# {name}\n\n{manifest['description']}\n\n## Install\n\n"
        f"```\n/pack install <path-or-git-url>\n```\n\n## Components\n\n"
        + "".join(f"- {kind}\n" for kind in kinds),
        created,
    )
    if "skills" in kinds:
        _write(
            target / "skills" / "example-skill" / "SKILL.md",
            "---\nname: example-skill\n"
            "description: Describe what this skill does and when to use it. Use when the user asks to "
            "\"<trigger phrase 1>\", \"<trigger phrase 2>\", or mentions <topic>.\n---\n\n"
            "# Example Skill\n\nState the goal in one paragraph.\n\n## Steps\n\n1. First step.\n2. Second step.\n\n"
            "## Resources\n\n- `references/details.md` - detailed rules, read when needed.\n",
            created,
        )
        _write(
            target / "skills" / "example-skill" / "references" / "details.md",
            "# Details\n\nLong-form reference material for the skill.\n",
            created,
        )
    if "agents" in kinds:
        _write(
            target / "agents" / "example-agent.md",
            "---\nname: example-agent\n"
            "description: Use this agent when <condition>. Examples:\n\n"
            "<example>\nuser: \"<request>\"\nassistant: \"I'll use the example-agent agent to <job>.\"\n"
            "<commentary>Why this agent fits.</commentary>\n</example>\n"
            "tools: [\"read_file\", \"grep\", \"find_files\"]\ncolor: blue\n---\n\n"
            "You are a specialist in <domain>.\n\n**Responsibilities:**\n1. ...\n\n"
            "**Process:**\n1. ...\n\n**Output format:**\n- ...\n",
            created,
        )
    if "commands" in kinds:
        _write(
            target / "commands" / "example.md",
            "---\ndescription: Short description shown in the command palette\n"
            "argument-hint: <target>\n---\n\n"
            "Do <task> for: $ARGUMENTS\n\nSteps:\n1. ...\n2. ...\n\nReport the result in a short summary.\n",
            created,
        )
    if "hooks" in kinds:
        hooks = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "write_file|edit_file|apply_patch",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "bash ${NAVIN_PLUGIN_ROOT}/hooks/scripts/check-write.sh",
                                "timeout": 10,
                            }
                        ],
                    }
                ]
            }
        }
        _write(target / "hooks" / "hooks.json", json.dumps(hooks, indent=2) + "\n", created)
        script = target / "hooks" / "scripts" / "check-write.sh"
        _write(
            script,
            "#!/usr/bin/env bash\n# PreToolUse guard: exit 2 blocks the tool call, stderr is the reason.\n"
            "set -euo pipefail\ninput=$(cat)\n"
            "path=$(printf '%s' \"$input\" | python3 -c 'import json,sys; d=json.load(sys.stdin).get(\"tool_input\", {}); "
            "print(d.get(\"path\") or d.get(\"file_path\") or \"\")')\n"
            "case \"$path\" in\n  *.env|*/.env|*secrets*) echo \"Refusing to write $path: secrets file\" >&2; exit 2 ;;\nesac\nexit 0\n",
            created,
        )
        if script.exists():
            script.chmod(0o755)
    if "mcp" in kinds:
        mcp = {
            "mcpServers": {
                "example": {
                    "command": "node",
                    "args": ["${NAVIN_PLUGIN_ROOT}/servers/example/index.js"],
                }
            }
        }
        _write(target / "mcp.json", json.dumps(mcp, indent=2) + "\n", created)
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a Navin plugin skeleton")
    parser.add_argument("target")
    parser.add_argument("--name", required=True)
    parser.add_argument("--description", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--components", default="skills,commands")
    args = parser.parse_args(argv)
    if not NAME_RE.match(args.name):
        print(f"invalid plugin name: {args.name!r} (lowercase, digits, . _ -)", file=sys.stderr)
        return 2
    kinds = [k.strip() for k in args.components.split(",") if k.strip()]
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        print(f"unknown components: {', '.join(unknown)} (choose from {', '.join(KINDS)})", file=sys.stderr)
        return 2
    created = scaffold(Path(args.target).expanduser(), args.name, args.description, args.author, kinds)
    for path in created:
        print(f"created: {path}")
    print("Next: write the components, then run validate_plugin.py on the folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
