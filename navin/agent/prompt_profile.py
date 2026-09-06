"""Per-section accounting of what a request actually sends.

A total says a request was 64K. It does not say whether that was tool
schemas, a replayed file dump, or the conversation itself, and those three
have opposite fixes. This splits a finished request into buckets that each
map to one decision.

It reads the request on its way out instead of being threaded through the
builders, so every path that reaches a provider is covered - main loop,
subagents, ephemeral turns - and the totals stay exact even when a section
heading cannot be matched to a known bucket.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from loguru import logger

from navin.utils.helpers import (
    estimate_message_tokens,
    estimate_prompt_tokens,
    estimate_text_tokens,
)

#: How :meth:`ContextBuilder.build_system_prompt` joins its blocks.
SECTION_SEPARATOR = "\n\n---\n\n"

#: Leading heading of a system block, lowercased, mapped to its bucket.
#: Blocks whose heading is missing or unknown land in ``system.other`` so the
#: total never silently loses tokens when a new section is introduced.
_SYSTEM_BUCKETS: Mapping[str, str] = {
    "who you are": "system.identity",
    "agents.md": "system.bootstrap",
    "claude.md": "system.bootstrap",
    "soul.md": "system.bootstrap",
    "user.md": "system.bootstrap",
    "project rules": "system.rules",
    "tool usage notes": "system.tool_contract",
    "memory": "system.memory",
    "active skills": "system.skills",
    "skills": "system.skills_index",
    "project subagents": "system.subagents",
    "recent history": "system.history",
    "where this work stood": "system.summary",
}

#: Buckets that are identical on every turn of a session. Their sum is the
#: floor a request cannot go below, which is the number worth driving down.
FIXED_BUCKETS = frozenset(
    {
        "system.identity",
        "system.bootstrap",
        "system.rules",
        "system.tool_contract",
        "system.skills",
        "system.skills_index",
        "system.subagents",
        "tools",
        "tools.mcp",
    }
)


def _heading_of(block: str) -> str:
    """Return the leading markdown heading of a system block, lowercased."""
    for line in block.lstrip().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip().lower()
        return ""
    return ""


def _system_sections(text: str) -> dict[str, int]:
    """Split a built system prompt into labelled token counts."""
    sections: dict[str, int] = {}
    for block in text.split(SECTION_SEPARATOR):
        if not block.strip():
            continue
        bucket = _SYSTEM_BUCKETS.get(_heading_of(block), "system.other")
        sections[bucket] = sections.get(bucket, 0) + estimate_text_tokens(block)
    return sections


def _tool_schema_name(tool: Mapping[str, Any]) -> str:
    fn = tool.get("function")
    if isinstance(fn, Mapping):
        name = fn.get("name")
        if isinstance(name, str):
            return name
    name = tool.get("name")
    return name if isinstance(name, str) else ""


def _tools_tokens(tools: Sequence[Mapping[str, Any]] | None) -> int:
    """Estimate what the tool schemas cost as serialized JSON."""
    builtin, mcp = _tools_tokens_split(tools)
    return builtin + mcp


def _tools_tokens_split(tools: Sequence[Mapping[str, Any]] | None) -> tuple[int, int]:
    """Builtin schemas vs MCP schemas (``mcp_*`` names)."""
    if not tools:
        return 0, 0
    builtin = 0
    mcp = 0
    for tool in tools:
        try:
            tokens = estimate_text_tokens(json.dumps(tool, ensure_ascii=False))
        except (TypeError, ValueError):
            tokens = estimate_text_tokens(str(tool))
        if _tool_schema_name(tool).startswith("mcp_"):
            mcp += tokens
        else:
            builtin += tokens
    return builtin, mcp


@dataclass(frozen=True, slots=True)
class PromptProfile:
    """Token breakdown of one outgoing request."""

    sections: Mapping[str, int]
    total: int
    tool_count: int = 0
    message_count: int = 0

    @property
    def fixed(self) -> int:
        """Tokens that repeat identically on every turn of the session."""
        return sum(v for k, v in self.sections.items() if k in FIXED_BUCKETS)

    @property
    def fixed_ratio(self) -> float:
        """Share of the request that carries no turn-specific information."""
        return self.fixed / self.total if self.total else 0.0

    def groups(self) -> dict[str, int]:
        """Section totals rolled up to the groups a budget is declared in."""
        rolled: dict[str, int] = {}
        for group, buckets in BUDGET_GROUPS.items():
            total = sum(self.sections.get(bucket, 0) for bucket in buckets)
            if total:
                rolled[group] = total
        return rolled

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "fixed": self.fixed,
            "fixed_ratio": round(self.fixed_ratio, 4),
            "tool_count": self.tool_count,
            "message_count": self.message_count,
            "sections": dict(self.sections),
            "groups": self.groups(),
        }


#: Profile section -> Context Usage legend id. Order is the legend order.
DISPLAY_SECTION_TO_BUCKET: tuple[tuple[str, str], ...] = (
    ("system.identity", "system"),
    ("system.bootstrap", "bootstrap"),
    ("system.rules", "rules"),
    ("system.memory", "memory"),
    ("system.tool_contract", "tool_contract"),
    ("system.skills", "skills"),
    ("system.skills_index", "skills"),
    ("system.subagents", "subagents"),
    ("tools", "tools"),
    ("tools.mcp", "mcp"),
    ("system.history", "history"),
    ("system.summary", "summary"),
    ("conversation", "conversation"),
    ("tool_results", "tool_results"),
    ("system.other", "other"),
    ("framing", "other"),
    ("system.mode", "other"),
)

DISPLAY_BUCKET_LABELS: Mapping[str, str] = {
    "system": "System prompt",
    "bootstrap": "Bootstrap",
    "rules": "Project rules",
    "memory": "Memory",
    "tool_contract": "Tool contract",
    "skills": "Skills",
    "subagents": "Subagent definitions",
    "tools": "Tool definitions",
    "mcp": "MCP tools",
    "history": "Recent history",
    "summary": "Summarized conversation",
    "conversation": "Conversation",
    "tool_results": "Tool results",
    "other": "Other",
}


def display_buckets(sections: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Roll profile sections into the Context Usage legend.

    Unknown keys stay in ``other`` so a new heading never vanishes. Zero
    rows are omitted. The returned token column re-sums to the attributed
    total; nothing is invented to match a provider peak.
    """
    if not isinstance(sections, Mapping) or not sections:
        return []
    totals: dict[str, int] = {}
    known = {source for source, _dest in DISPLAY_SECTION_TO_BUCKET}
    for source, dest in DISPLAY_SECTION_TO_BUCKET:
        raw = sections.get(source)
        try:
            tokens = int(raw or 0)
        except (TypeError, ValueError):
            continue
        if tokens > 0:
            totals[dest] = totals.get(dest, 0) + tokens
    leftover = 0
    for key, raw in sections.items():
        if key in known:
            continue
        try:
            leftover += max(0, int(raw or 0))
        except (TypeError, ValueError):
            continue
    if leftover:
        totals["other"] = totals.get("other", 0) + leftover
    order = [dest for _source, dest in DISPLAY_SECTION_TO_BUCKET]
    seen: set[str] = set()
    buckets: list[dict[str, Any]] = []
    for dest in order:
        if dest in seen:
            continue
        seen.add(dest)
        tokens = totals.get(dest, 0)
        if tokens > 0:
            buckets.append(
                {
                    "id": dest,
                    "label": DISPLAY_BUCKET_LABELS.get(dest, dest),
                    "tokens": tokens,
                }
            )
    return buckets


#: Budget group -> the profile buckets it accounts for. Every bucket belongs
#: to exactly one group, so the groups always re-sum to the request total.
BUDGET_GROUPS: Mapping[str, tuple[str, ...]] = {
    "core_system": (
        "system.identity",
        "system.bootstrap",
        "system.rules",
        "system.tool_contract",
        "system.memory",
        "system.mode",
        "system.subagents",
        "system.other",
    ),
    "skills": ("system.skills", "system.skills_index"),
    "tools": ("tools", "tools.mcp"),
    "history": ("conversation", "system.history", "system.summary"),
    "tool_results": ("tool_results",),
}


@dataclass(frozen=True, slots=True)
class Overrun:
    """One budget group that exceeded its declared ceiling."""

    group: str
    used: int
    limit: int

    @property
    def ratio(self) -> float:
        return self.used / self.limit if self.limit else 0.0

    def __str__(self) -> str:
        return f"{self.group}={self.used}/{self.limit} ({self.ratio:.0%})"


@dataclass(frozen=True, slots=True)
class PromptBudget:
    """Declared ceilings for what a request may spend, per group.

    A ceiling here is a target to report against, not a gate. Refusing to
    send an over-budget request would turn a cost problem into a failed task,
    and the section that overflows is usually the one carrying the work.
    """

    limits: Mapping[str, int]
    request_max: int

    def overruns(self, profile: PromptProfile) -> list[Overrun]:
        """Groups above their ceiling, worst offender first."""
        groups = profile.groups()
        found = [
            Overrun(group=name, used=groups.get(name, 0), limit=limit)
            for name, limit in self.limits.items()
            if groups.get(name, 0) > limit
        ]
        if profile.total > self.request_max:
            found.append(
                Overrun(group="request", used=profile.total, limit=self.request_max)
            )
        return sorted(found, key=lambda o: -o.ratio)


#: Target shape of a request. Two kinds of number live here.
#:
#: ``core_system``, ``skills`` and ``tools`` are structural: identical on every
#: request of a session, so they are engineering targets. Measured 2026-08-15:
#: core_system 10,017 and tools 19,193 are over, skills 1,255 already fits.
#:
#: ``history`` and ``tool_results`` grow with the session, and the real ceiling
#: on them is the model's context window, which ContextGovernor already
#: enforces. The values here mark a session heavy enough to be worth a look,
#: not a target: setting them at the structural scale would fire on ordinary
#: work and teach everyone to ignore the warning.
DEFAULT_BUDGET = PromptBudget(
    limits={
        "core_system": 7_000,
        "skills": 3_000,
        "tools": 7_000,
        "history": 20_000,
        "tool_results": 20_000,
    },
    # Runaway detection, not a target. The measured average request is 62,126
    # tokens, so anything past this is a session that grew in a way the
    # governor did not catch, or one enormous tool result.
    request_max=200_000,
)


#: Groups already reported this process. A structural overrun is identical on
#: every request, so warning once says everything 96 warnings would.
_WARNED_GROUPS: set[str] = set()


def reset_budget_warnings() -> None:
    """Forget which groups were already reported. For tests and long daemons."""
    _WARNED_GROUPS.clear()


def profile_request(
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None = None,
) -> PromptProfile:
    """Break an outgoing request into the buckets that map to a fix.

    ``framing`` absorbs the per-message envelope the provider adds on top of
    the content, so the buckets always sum to the same total the context
    budget is measured against.
    """
    sections: dict[str, int] = {}
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system" and isinstance(content, str):
            for bucket, count in _system_sections(content).items():
                sections[bucket] = sections.get(bucket, 0) + count
            continue
        bucket = "tool_results" if role == "tool" else "conversation"
        sections[bucket] = sections.get(bucket, 0) + estimate_message_tokens(dict(message))

    builtin_tools, mcp_tools = _tools_tokens_split(tools)
    if builtin_tools:
        sections["tools"] = builtin_tools
    if mcp_tools:
        sections["tools.mcp"] = mcp_tools

    total = estimate_prompt_tokens(
        [dict(m) for m in messages],
        [dict(t) for t in tools] if tools else None,
    )
    framing = total - sum(sections.values())
    if framing > 0:
        sections["framing"] = framing

    return PromptProfile(
        sections=sections,
        total=max(total, sum(sections.values())),
        tool_count=len(tools or ()),
        message_count=len(messages),
    )


def format_profile(profile: PromptProfile) -> str:
    """Render a profile as one sorted, greppable log line."""
    parts = ", ".join(
        f"{name}={count}"
        for name, count in sorted(profile.sections.items(), key=lambda kv: -kv[1])
        if count
    )
    return (
        f"prompt profile: total={profile.total} "
        f"fixed={profile.fixed} ({profile.fixed_ratio:.0%}) "
        f"tools={profile.tool_count} msgs={profile.message_count} | {parts}"
    )


def profiling_enabled() -> bool:
    """Whether to log a breakdown before each provider call.

    Off by default: the breakdown re-encodes the prompt, which is wasted work
    on a turn nobody is measuring.
    """
    return os.getenv("NAVIN_PROMPT_PROFILE", "").strip().lower() in {"1", "true", "yes", "on"}


def log_request_profile(
    messages: Sequence[Mapping[str, Any]] | None,
    tools: Sequence[Mapping[str, Any]] | None,
    *,
    model: str | None = None,
    session_key: str | None = None,
) -> PromptProfile | None:
    """Log the breakdown of an outgoing request, when profiling is enabled.

    Never raises: a measurement tool must not be able to break the turn it is
    measuring.
    """
    if not profiling_enabled() or not messages:
        return None
    try:
        profile = profile_request(messages, tools)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("prompt profile failed: {}", exc)
        return None
    who = f"[{session_key or 'default'}/{model or '?'}]"
    logger.info("{} {}", who, format_profile(profile))
    fresh = [o for o in DEFAULT_BUDGET.overruns(profile) if o.group not in _WARNED_GROUPS]
    if fresh:
        _WARNED_GROUPS.update(o.group for o in fresh)
        logger.warning(
            "{} over budget: {}",
            who,
            ", ".join(str(overrun) for overrun in fresh),
        )
    return profile
