# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Glue between the ``forge`` tool and the triage engine.

``issue_triage_plan`` is read-only and always safe. ``issue_triage_apply``
rebuilds a fresh plan, asks for approval of the whole batch, and refuses an
unattended run (cron, ``/loop``, heartbeat, subagent, no approver) unless the
user opted in with ``"unattended_apply": true`` in ``.navin/triage.json``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from navin.github.forge_issues import ForgeIssueClient
from navin.github.triage import (
    CLOSING_KINDS,
    TriagePlan,
    TriageRules,
    apply_plan,
    build_plan,
    load_rules,
    render_plan,
)
from navin.webui import forge_api
from navin.webui.forge_api import ForgeError

_UNATTENDED_META = ("_cron_trigger", "_cron_turn", "heartbeat", "subagent")
_UNATTENDED_SENDERS = frozenset({"cron", "subagent", "heartbeat"})


def _load_config() -> Any | None:
    try:
        from navin.config.loader import load_config

        return load_config()
    except Exception:  # noqa: BLE001 - env / gh tokens still work
        return None


def resolve_repo(root: Path, repo: str = "", *, config: Any | None = None) -> tuple[Any, str]:
    """``(remote, token)`` for ``repo`` (``owner/name``, URL) or ``origin``."""
    remote = forge_api.resolve_repo_remote(root, repo or None, config=config)
    if remote is None:
        raise ForgeError(
            "no repository to triage: pass repo=owner/name or add an origin remote",
            status=409,
            code="forge_no_remote",
        )
    if remote.kind == forge_api.UNKNOWN:
        raise ForgeError(
            f"cannot tell which forge {remote.host} runs. Set its type in Settings > Git & GitHub.",
            status=409,
            code="forge_unknown_kind",
            host=remote.host,
        )
    token, _source = forge_api.resolve_token(remote, config=config, root=root)
    if not token:
        raise forge_api.token_missing_error(remote)
    return remote, token


def is_unattended() -> bool:
    """A scheduled, background or approver-less run: nobody can say yes."""
    from navin.agent.approval import current_approval_gate
    from navin.agent.tools.context import current_request_context

    ctx = current_request_context()
    if ctx is not None:
        meta = ctx.metadata or {}
        if any(meta.get(key) for key in _UNATTENDED_META):
            return True
        if str(ctx.sender_id or "").strip().lower() in _UNATTENDED_SENDERS:
            return True
        if str(ctx.session_key or "").strip().lower() == "heartbeat":
            return True
    return current_approval_gate() is None


def _embedder() -> Any | None:
    """Sync embed function from ``tools.semanticSearch`` when configured, else None."""
    try:
        from navin.notes.memory import _resolve_client

        client = _resolve_client()
    except Exception:  # noqa: BLE001 - token similarity is always available
        return None
    if client is None:
        return None

    def embed(texts: list[str]) -> list[list[float]]:
        # Runs inside asyncio.to_thread, so a private event loop is fine here.
        return asyncio.run(client.embed(texts))

    return embed


def _plan_sync(root: Path, repo: str, config: Any | None) -> tuple[ForgeIssueClient, TriageRules, TriagePlan]:
    remote, token = resolve_repo(root, repo, config=config)
    rules = load_rules(root, remote.slug)
    client = ForgeIssueClient(remote, token, bot_logins=rules.bot_logins)
    plan = build_plan(client, rules, embed=_embedder())
    return client, rules, plan


def _summary(plan: TriagePlan) -> str:
    lines = [f"#{a.number} {a.kind}: {a.reason}" for a in plan.actions[:25]]
    if len(plan.actions) > 25:
        lines.append(f"... and {len(plan.actions) - 25} more")
    return "\n".join(lines)


async def triage_plan(root: Path, *, repo: str = "", config: Any | None = None) -> dict[str, Any]:
    cfg = config if config is not None else _load_config()
    _client, _rules, plan = await asyncio.to_thread(_plan_sync, root, repo, cfg)
    return {"plan": plan.to_dict(), "render": render_plan(plan)}


async def triage_apply(root: Path, *, repo: str = "", config: Any | None = None) -> dict[str, Any]:
    """Fresh plan, batch approval, then apply. Refuses unattended unless opted in."""
    from navin.agent.approval import ApprovalRequest, request_approval

    cfg = config if config is not None else _load_config()
    client, rules, plan = await asyncio.to_thread(_plan_sync, root, repo, cfg)
    if not plan.actions:
        return {"applied": 0, "plan": plan.to_dict(), "render": render_plan(plan)}
    if is_unattended() and not rules.unattended_apply:
        return {
            "applied": 0,
            "refused": (
                "unattended apply is off: this run has nobody to approve it. Review the "
                "plan below, run /triage --apply from a chat, or set "
                '"unattended_apply": true in .navin/triage.json to allow scheduled runs.'
            ),
            "plan": plan.to_dict(),
            "render": render_plan(plan),
        }
    closes = sum(1 for a in plan.actions if a.kind in CLOSING_KINDS)
    decision = await request_approval(
        ApprovalRequest(
            tool="forge",
            action=f"Apply issue triage on {plan.repo}: {len(plan.actions)} action(s), {closes} close(s)",
            reason="issue triage autopilot",
            detail=_summary(plan),
            consequence="Posts comments, changes labels and closes issues visible to everyone watching them.",
            scope=f"forge:issue_triage_apply:{plan.repo}",
            allow_when_unattended=rules.unattended_apply,
        )
    )
    if not decision.allowed:
        return {
            "applied": 0,
            "refused": decision.reason or "refused",
            "plan": plan.to_dict(),
            "render": render_plan(plan),
        }
    applied = await asyncio.to_thread(apply_plan, client, plan)
    return {**applied, "plan": plan.to_dict(), "render": render_plan(plan, applied=applied)}


async def issue_labels(
    root: Path,
    *,
    number: int,
    add: list[str],
    remove: list[str],
    repo: str = "",
    config: Any | None = None,
) -> dict[str, Any]:
    """Add / remove labels one by one; existing labels are never replaced."""
    from navin.agent.approval import ApprovalRequest, request_approval

    if not number:
        raise ValueError("issue_labels needs number=<issue number>")
    if not add and not remove:
        raise ValueError("issue_labels needs labels=[...] to add and/or remove_labels=[...]")
    cfg = config if config is not None else _load_config()
    remote, token = await asyncio.to_thread(resolve_repo, root, repo, config=cfg)
    decision = await request_approval(
        ApprovalRequest(
            tool="forge",
            action=f"Change labels on {remote.slug}#{number}",
            reason=f"forge issue_labels on {remote.host}",
            detail=f"add={','.join(add) or '-'} remove={','.join(remove) or '-'}",
            consequence="Labels are visible to everyone watching the issue.",
            scope="forge:issue_labels",
            # Additive metadata, like issue_comment: fine in unattended runs.
            allow_when_unattended=True,
        )
    )
    if not decision.allowed:
        raise ValueError(f"refused. {decision.reason}")
    client = ForgeIssueClient(remote, token)

    def run() -> dict[str, Any]:
        added = client.add_labels(number, add)["added"] if add else []
        removed = [client.remove_label(number, label)["removed"] for label in remove]
        return {"number": int(number), "added": added, "removed": [r for r in removed if r]}

    result = await asyncio.to_thread(run)
    return {"forge": remote.kind, "repo": remote.slug, "host": remote.host, **result}


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()]


async def forge_tool_action(tool: Any, act: str, args: dict[str, Any]) -> Any:
    """``forge`` tool entry for issue_triage_plan / issue_triage_apply / issue_labels."""
    import json

    from navin.agent.tools.base import ToolResult

    root, error = tool._root_or_error()
    if root is None:
        return error
    repo = str(args.get("repo") or "").strip()
    try:
        if act == "issue_triage_plan":
            result = await triage_plan(root, repo=repo)
        elif act == "issue_triage_apply":
            result = await triage_apply(root, repo=repo)
        else:
            result = await issue_labels(
                root,
                number=int(args.get("number") or 0),
                add=_str_list(args.get("labels")),
                remove=_str_list(args.get("remove_labels")),
                repo=repo,
            )
    except ForgeError as exc:
        return ToolResult.error(f"Error: {exc.message}")
    except (TypeError, ValueError) as exc:
        return ToolResult.error(f"Error: {exc}")
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)
