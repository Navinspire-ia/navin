# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Version-aware capabilities for Anthropic Claude models.

Anthropic deprecated the ``temperature`` parameter starting with the
Claude 4.7 generation (opus-4.7/4.8, sonnet-5, fable): their API answers
400 when it is present, and OpenRouter routing with ``require_parameters``
answers 404 because no endpoint declares support.

A hardcoded deny-list goes stale with every release, so the rule here is
version-based and fails safe: temperature is only sent to generations
known to accept it (< 4.7). Any newer, unknown or unparseable Claude
model does NOT get it - omitting temperature is always harmless (the
model uses its default sampling), sending it can break the call.

Handles every spelling in the wild:
- native Anthropic:  claude-sonnet-4-6, claude-opus-4-8-20251101
- OpenRouter style:  anthropic/claude-opus-4.8, anthropic/claude-sonnet-5
- Bedrock IDs:       us.anthropic.claude-opus-4-8-20251101-v1:0
- legacy naming:     claude-3-5-sonnet-20241022 (version before family)
"""

from __future__ import annotations

import re

# Last generation that still accepts temperature.
_MAX_TEMPERATURE_VERSION = (4, 6)
# First generation with the ``thinking`` request field (Claude 3.7).
_MIN_THINKING_VERSION = (3, 7)
# First generation accepting ``thinking: {"type": "adaptive"}``.
_MIN_ADAPTIVE_THINKING_VERSION = (4, 6)

# New naming: family first, then version (claude-sonnet-4-6, opus-4.8...).
# The minor is one or two digits not followed by another: in
# ``claude-opus-4-20250514`` the date is not a minor version (4.20250514 read
# as newer than 4.6 sent adaptive thinking to Claude 4.0, a 400).
_FAMILY_VERSION_RE = re.compile(
    r"(?:sonnet|opus|haiku)[-.](\d+)(?:[-.](\d{1,2})(?!\d))?"
)
# Legacy naming: version first, then family (claude-3-5-sonnet).
_VERSION_FAMILY_RE = re.compile(
    r"claude[-.](\d+)(?:[-.](\d{1,2})(?!\d))?[-.](?:sonnet|opus|haiku)"
)


def is_claude_model(model: str) -> bool:
    return "claude" in model.lower()


def claude_supports_temperature(model: str) -> bool:
    """True when this Claude model still accepts the temperature parameter.

    Non-Claude models return True (callers gate on :func:`is_claude_model`
    or on their own rules first).
    """
    name = model.lower()
    if "claude" not in name:
        return True
    # Endpoint variants observed without temperature support, whatever
    # the base model says (OpenRouter data).
    if any(token in name for token in ("fable", "-fast", ":batch", "-latest")):
        return False
    match = _VERSION_FAMILY_RE.search(name) or _FAMILY_VERSION_RE.search(name)
    if match is None:
        # Unknown shape = future model: fail safe, do not send it.
        return False
    version = (int(match.group(1)), int(match.group(2) or 0))
    return version <= _MAX_TEMPERATURE_VERSION


def claude_version(model: str) -> tuple[int, int] | None:
    """(major, minor) parsed from any Claude spelling, None when unparseable."""
    name = model.lower()
    if "claude" not in name:
        return None
    match = _VERSION_FAMILY_RE.search(name) or _FAMILY_VERSION_RE.search(name)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2) or 0))


def claude_accepts_thinking_param(model: str) -> bool:
    """True when the ``thinking`` field may be sent to this Claude model.

    Introduced with Claude 3.7; every later generation keeps it, so an
    unknown or future Claude name (fable, mythos, ...) is treated as new.
    Older models (3.5 and before) answer 400 to the field. Non-Claude models
    return False: the field is Anthropic's, not the wire's.
    """
    name = model.lower()
    if "claude" not in name:
        return False
    version = claude_version(name)
    if version is None:
        return True
    return version >= _MIN_THINKING_VERSION


def claude_supports_adaptive_thinking(model: str) -> bool:
    """True from Claude 4.6 on (and for unknown future names)."""
    name = model.lower()
    if "claude" not in name:
        return False
    version = claude_version(name)
    if version is None:
        return True
    return version >= _MIN_ADAPTIVE_THINKING_VERSION


# --- Reasoning controls ------------------------------------------------------
#
# Measured on Bedrock 2026-10-07 (the same Messages API Anthropic serves):
#
#   model         thinking off                      low..max
#   sonnet-5.5    {"type": "between_tools"} only    adaptive + output_config.effort
#   opus-5.5      no off: adaptive + effort "low"   adaptive + output_config.effort
#   sonnet/opus-5 {"type": "disabled"}              adaptive + output_config.effort
#
# ``thinking: {"type": "adaptive", "effort": ...}`` is rejected everywhere
# ("Extra inputs are not permitted"): the effort lives in output_config.
# A model nobody has met yet is learned from its refusal (see
# learn_claude_thinking_off), so a new release costs one retried call, not a
# broken turn.

_OFF_BETWEEN_TOOLS = "between_tools"
_OFF_ADAPTIVE_LOW = "adaptive_low"
_OFF_DISABLED = "disabled"

_KNOWN_OFF_STYLES: tuple[tuple[str, str], ...] = (
    ("sonnet-5-5", _OFF_BETWEEN_TOOLS),
    ("sonnet-5.5", _OFF_BETWEEN_TOOLS),
    ("opus-5-5", _OFF_ADAPTIVE_LOW),
    ("opus-5.5", _OFF_ADAPTIVE_LOW),
    # Fable / Mythos always think: {"type": "disabled"} is a 400, adaptive
    # with the lowest effort is the closest thing to "off".
    ("fable", _OFF_ADAPTIVE_LOW),
    ("mythos", _OFF_ADAPTIVE_LOW),
)
_learned_off_styles: dict[str, str] = {}

_EFFORT_LADDER = ("low", "medium", "high", "xhigh", "max")


def _off_style(model: str) -> str:
    name = model.lower()
    for key, style in _learned_off_styles.items():
        if key in name:
            return style
    for marker, style in _KNOWN_OFF_STYLES:
        if marker in name:
            return style
    return _OFF_DISABLED


def claude_cannot_disable_thinking(model: str) -> bool:
    """True when ``thinking: {"type": "disabled"}`` is a 400 for this model."""
    return _off_style(model) != _OFF_DISABLED


def _model_key(model: str) -> str:
    """The part of a model id that names the model, whatever the wrapper."""
    name = model.lower().rsplit("/", 1)[-1]
    return re.sub(r"^(?:[a-z]{2,6}\.)?anthropic\.", "", name).split(":", 1)[0]


def learn_claude_thinking_off(model: str, error: object) -> bool:
    """Remember how this model turns thinking off, from the error it returned.

    Returns True when the refusal named a usable alternative, so the caller
    can retry the request once with :func:`claude_thinking_fields`.
    """
    text = str(error or "")
    lowered = text.lower()
    style: str | None = None
    if "between_tools" in lowered and "disabled" in lowered and "instead" in lowered:
        style = _OFF_BETWEEN_TOOLS
    elif "thinking.type.disabled" in lowered and "not supported" in lowered and "adaptive" in lowered:
        style = _OFF_ADAPTIVE_LOW
    elif "thinking.type.between_tools" in lowered and "not supported" in lowered:
        style = _OFF_DISABLED
    if style is None or _off_style(model) == style:
        return False
    key = _model_key(model)
    if key in _learned_off_styles:
        # Learned once already: a second, contradicting refusal must not send
        # the request back and forth between two shapes.
        return False
    _learned_off_styles[key] = style
    return True


# Models that answer 400 to a forced ``tool_choice`` (``any`` / ``tool``)
# whatever the thinking setting.
_NO_FORCED_TOOL_CHOICE = (
    "opus-5-5", "opus-5.5", "sonnet-5-5", "sonnet-5.5",
    "fable-5-1", "fable-5.1", "mythos-5-1", "mythos-5.1",
)


def claude_rejects_forced_tool_choice(model: str) -> bool:
    """True when ``tool_choice`` ``any``/``tool`` is refused by this model."""
    name = model.lower()
    return any(marker in name for marker in _NO_FORCED_TOOL_CHOICE)


def claude_max_output_tokens(model: str) -> int | None:
    """Documented output ceiling (max_tokens) for a known Claude model.

    None when the model is unknown or future: nothing is clamped then.
    """
    name = model.lower()
    version = claude_version(name)
    if version is None:
        return None
    if version >= (4, 6):
        return 128_000
    if version >= (4, 0):
        if "opus" in name and version < (4, 5):
            return 32_000  # Opus 4 / 4.1
        return 64_000  # Sonnet 4 / 4.5, Haiku 4.5, Opus 4.5
    if version >= (3, 7):
        return 64_000
    if version >= (3, 5):
        return 8_192
    return 4_096


def clamp_claude_max_tokens(model: str, max_tokens: int) -> int:
    """``max_tokens`` within the model's output ceiling (when known)."""
    ceiling = claude_max_output_tokens(model)
    max_tokens = max(1, int(max_tokens))
    return min(max_tokens, ceiling) if ceiling else max_tokens


_MIN_THINKING_BUDGET = 1024
# Output kept for the answer itself once the thinking budget is spent.
_ANSWER_ROOM = 4096


def claude_thinking_budget(model: str, effort: str, max_tokens: int) -> tuple[int, int]:
    """``(budget_tokens, max_tokens)`` for budget-style extended thinking.

    The API needs ``1024 <= budget_tokens < max_tokens`` and ``max_tokens``
    within the model's output ceiling: a high budget on a 64k model must
    not push max_tokens to 68k (a 400).
    """
    if effort == "low":
        budget = _MIN_THINKING_BUDGET
    elif effort == "medium":
        budget = 4096
    else:  # high, xhigh, max, adaptive: the deepest budget the request holds
        budget = max(8192, max_tokens)
    total = max(max_tokens, budget + _ANSWER_ROOM)
    ceiling = claude_max_output_tokens(model)
    if ceiling:
        total = min(total, ceiling)
    budget = max(_MIN_THINKING_BUDGET, min(budget, total - _ANSWER_ROOM))
    if budget >= total:
        total = budget + 1
    return budget, total


# Effort values a model refused, learned from the error that named the valid
# ones (a release the tables predate). Keyed by the bare model name.
_learned_claude_efforts: dict[str, frozenset[str]] = {}


def fit_claude_effort(model: str, effort: str) -> str:
    """The effort this model takes closest to ``effort`` (learned refusals)."""
    from navin.providers.reasoning_control import nearest_effort

    accepted = _learned_claude_efforts.get(_model_key(model))
    return nearest_effort(effort, accepted) if accepted else effort


def learn_claude_effort(model: str, effort: str, error: object) -> bool:
    """Remember the efforts a refusal lists; True when one retry can succeed."""
    from navin.providers.reasoning_control import EFFORT_LADDER, classify_rejection

    text = str(error or "").lower()
    if "effort" not in text or classify_rejection(text) is None:
        return False
    named = {
        value for value in EFFORT_LADDER
        if value not in ("none", "minimal")
        and re.search(rf"(?<![\w-]){value}(?![\w-])", text)
    }
    named.discard(effort)
    if not named:
        return False
    key = _model_key(model)
    accepted = frozenset(named)
    if _learned_claude_efforts.get(key) == accepted:
        return False
    _learned_claude_efforts[key] = accepted
    return True


def claude_thinking_fields(
    model: str,
    reasoning_effort: str | None,
    *,
    max_tokens: int,
) -> tuple[dict[str, object], int]:
    """Anthropic request fields for this effort, and the max_tokens they need.

    Returns ``{"thinking": ..., "output_config": ...}`` (either may be absent)
    in the Messages API shape; Bedrock passes it as additionalModelRequestFields.
    No effort means no field: the model keeps its default.
    """
    if not reasoning_effort or not claude_accepts_thinking_param(model):
        return {}, max_tokens
    effort = reasoning_effort.lower()
    if effort == "ultra":
        effort = "max"  # deepest tier; 4.6 Sonnet is clamped to high below
    if effort not in ("none", "minimal", "off", "adaptive"):
        effort = fit_claude_effort(model, effort)
    adaptive = claude_supports_adaptive_thinking(model)
    if effort in ("none", "minimal", "off"):
        style = _off_style(model)
        if style == _OFF_BETWEEN_TOOLS:
            return {"thinking": {"type": "between_tools"}}, max_tokens
        if style == _OFF_ADAPTIVE_LOW:
            return {"thinking": {"type": "adaptive"}, "output_config": {"effort": "low"}}, max_tokens
        return {"thinking": {"type": "disabled"}}, max_tokens
    if adaptive:
        # From Claude 4.7 the thinking text is omitted unless asked for: the
        # stream then stays silent for minutes at high effort, which the idle
        # watchdog reads as a stall. A summary keeps it alive (and readable).
        thinking_on = {"type": "adaptive", "display": "summarized"}
        if effort == "adaptive":
            return {"thinking": thinking_on}, max_tokens
        level = effort if effort in _EFFORT_LADDER else "medium"
        name = model.lower()
        if any(marker in name for marker in ("sonnet-4-6", "sonnet-4.6")) and level in ("xhigh", "max"):
            level = "high"  # 4.6 Sonnet stops at high
        elif any(marker in name for marker in ("opus-4-6", "opus-4.6")) and level == "xhigh":
            level = "max"  # 4.6 Opus has max but no xhigh
        return {"thinking": thinking_on, "output_config": {"effort": level}}, max_tokens
    budget, max_tokens = claude_thinking_budget(model, effort, max_tokens)
    return {"thinking": {"type": "enabled", "budget_tokens": budget}}, max_tokens
