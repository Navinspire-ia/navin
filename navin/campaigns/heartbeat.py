# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Heartbeat for Campaigns: the agent handles new replies like a campaign manager.

The gateway already polled the mailbox and classified replies by rules before
the heartbeat turn. When replies are waiting, the note below tells the agent
exactly what to do with each one, following the auto-reply mode the user chose
(off, draft, send). The task block in HEARTBEAT.md is managed here so the turn
runs only while Campaigns needs it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.campaigns import store

TASK_START = "<!-- navin:campaigns-task:start -->"
TASK_END = "<!-- navin:campaigns-task:end -->"
TASK_BLOCK = f"""{TASK_START}
### Campaign replies (silent unless actionable)

If the prompt includes a "Campaign replies waiting" note, handle each reply with the
`campaigns` tool exactly as that note says. Otherwise reply `HEARTBEAT_OK`.
{TASK_END}
"""


def tick_watch() -> dict[str, Any] | None:
    """New replies waiting for the agent, or None when there is nothing to do."""
    settings = store.get_settings()
    if settings.get("auto_reply_mode", "draft") == "off" or not settings.get("loop_enabled", True):
        return None
    waiting = store.list_replies(status="new", limit=20)
    if not waiting["total"]:
        return None
    return {"count": waiting["total"], "mode": settings.get("auto_reply_mode", "draft"),
            "replies": [{"id": r["id"], "from": r["from_addr"], "class": r["classification"],
                         "channel": r.get("channel", "email"),
                         "campaign": r["campaign_name"], "summary": r["summary"]} for r in waiting["items"]]}


def heartbeat_prompt_note(payload: dict[str, Any] | None) -> str:
    if not payload or not payload.get("count"):
        return ""
    mode = payload.get("mode", "draft")
    lines = [
        "",
        "",
        f"Campaign replies waiting: {payload['count']} (auto-reply mode: {mode}).",
        "For each reply (`campaigns action=replies status=new`, then `campaigns action=reply_get id=...`):",
        "`reply_get` gives you everything: the reply, the campaign you sent, the contact (CRM facts in attrs) "
        "and the whole thread on email and WhatsApp. Answer on the channel they used.",
        "1. Read it and its campaign. Correct the classification if the rules got it wrong "
        "(`campaigns action=reply_update id=... classification=...`): interested, meeting, question, "
        "not_interested, unsubscribe, out_of_office, bounce, other.",
        "2. Write a short `summary` and, for interested / meeting / question, a `handoff_brief` a colleague can act "
        "on without reading the thread: who they are (name, company, role), what they asked, what they need, the "
        "next step, and the deadline if any.",
        "3. Hand real leads (interested, meeting) to the right owner: `assigned_to` = the Sales teammate of the "
        "chat's team (Collaboration Mode) or \"human\"; if Collaboration Mode is on, assign that teammate with "
        "`collab action=assign` and the brief as the task. Never invent prices, dates or promises.",
    ]
    if mode == "send":
        lines.append(
            "4. Answer questions and meeting requests yourself with `campaigns action=reply_send` only when the "
            "facts are in the campaign or the brief; otherwise save a draft (`reply_update draft_reply=...`) for a "
            "human. Thank not_interested replies briefly only if they asked something. Never answer unsubscribe, "
            "out_of_office or bounce."
        )
    else:
        lines.append(
            "4. Draft the answer (`campaigns action=reply_update id=... draft_reply=...`) for a human to send; do not "
            "send it yourself."
        )
    lines.append("5. Mark not_interested replies `ignored` once handled. Report only what changed, briefly.")
    for item in payload.get("replies", [])[:10]:
        lines.append(f"- {item['id']} [{item['class']}] {item.get('channel', 'email')} {item['from']} "
                     f"({item['campaign']}): {item['summary']}")
    return "\n".join(lines) + "\n"


def ensure_heartbeat_task(enabled: bool, workspace: Path | None = None) -> bool:
    """Add or remove the managed Campaigns task in HEARTBEAT.md; True when changed."""
    from navin import workspace_layout

    if workspace is None:
        from navin.config.loader import load_config

        workspace = load_config().workspace_path
    path = workspace_layout.read_with_root_fallback(workspace, "HEARTBEAT.md")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        if not enabled:
            return False
        content = "# Heartbeat Tasks\n\n## Active Tasks\n\n"
    has_block = TASK_START in content and TASK_END in content
    if enabled and not has_block:
        if "## Active Tasks" in content:
            head, _, tail = content.partition("## Active Tasks")
            first_line, _, rest = tail.partition("\n")
            content = f"{head}## Active Tasks{first_line}\n\n{TASK_BLOCK}{rest}"
        else:
            content = content.rstrip() + "\n\n## Active Tasks\n\n" + TASK_BLOCK
    elif not enabled and has_block:
        start = content.index(TASK_START)
        end = content.index(TASK_END) + len(TASK_END)
        content = (content[:start].rstrip("\n") + "\n" + content[end:].lstrip("\n"))
    else:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
