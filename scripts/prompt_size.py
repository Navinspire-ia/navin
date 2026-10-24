"""Measure the core prompt Navin sends: identity, tool contract, /forge brief.

Run from the repo root: .venv/bin/python scripts/prompt_size.py
Token estimate is chars / 4 (good enough to compare before and after).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from navin.utils.prompt_templates import render_template  # noqa: E402


def forge_brief(focus: str) -> str:
    from navin.command.builtin import _workflow_handler

    msg = SimpleNamespace(content="", metadata={})
    ctx = SimpleNamespace(args=focus, raw=f"/forge {focus}", msg=msg)
    asyncio.run(_workflow_handler("/forge")(ctx))
    return msg.content


def main() -> None:
    sizes = {
        "identity(cli)": len(render_template("agent/identity.md", channel="cli", workspace_path="/w",
                                             runtime="Linux", platform_policy="")),
        "tool_contract": len(render_template("agent/tool_contract.md")),
        "tool_contract_slim": len(render_template("agent/tool_contract_slim.md")),
    }
    for focus in ("ajoute un dashboard au module ventes avec back et front", "corrige la typo dans README"):
        try:
            sizes[f"forge_brief[{focus[:20]}]"] = len(forge_brief(focus))
        except Exception as exc:  # measurement helper only
            sizes[f"forge_brief[{focus[:20]}]"] = f"error: {exc}"
    print(json.dumps({k: {"chars": v, "tokens~": v // 4} if isinstance(v, int) else v
                      for k, v in sizes.items()}, indent=1))


if __name__ == "__main__":
    main()
