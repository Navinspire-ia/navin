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
_FAMILY_VERSION_RE = re.compile(
    r"(?:sonnet|opus|haiku)[-.](\d+)(?:[-.](\d+))?"
)
# Legacy naming: version first, then family (claude-3-5-sonnet).
_VERSION_FAMILY_RE = re.compile(
    r"claude[-.](\d+)(?:[-.](\d+))?[-.](?:sonnet|opus|haiku)"
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
