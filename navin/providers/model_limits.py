# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Known context window and output ceiling of the large model families.

A preset that leaves ``max_tokens`` / ``context_window_tokens`` at their
defaults gets the real limits of its model from here, so a 1M-context model
that can write 128k tokens is not held to the generic 200k / 32k. Unknown
models keep the generic defaults; the OpenAI-compatible provider then learns
a lower output ceiling from the provider's own refusal.

Sources (2026-10-08):
- Claude Opus 4.6+, Sonnet 4.6+, Haiku 5.5, Fable 5/5.1, Mythos: Anthropic
  docs, 1M context and 128k output.
- GPT-6 (Sol, Astra, Luna, 6.1): ~1.05M context, 128k output (aggregators).
- Kimi K3 (Bedrock global): ~1M context, 128k output (aggregators; live
  calls through the gateway succeed).
"""

from __future__ import annotations

import re
from typing import NamedTuple


class ModelLimits(NamedTuple):
    context_window_tokens: int
    max_output_tokens: int


_LARGE = ModelLimits(context_window_tokens=1_000_000, max_output_tokens=128_000)

_CLAUDE_VERSIONED = re.compile(r"\b(opus|sonnet|haiku)[-_.]?(\d+)(?:[-_.](\d{1,2}))?(?!\d)")
_CLAUDE_ALWAYS_LARGE = re.compile(r"\b(fable|mythos)\b")
_GPT_6 = re.compile(r"\bgpt[-_.]?6(?:[-_.]\d+)?(?![\d.])")
_KIMI_K3 = re.compile(r"\bkimi[-_.]?k3\b")

# Lowest version of each Claude line with 1M context and 128k output.
_CLAUDE_FLOOR = {"opus": (4, 6), "sonnet": (4, 6), "haiku": (5, 5)}


def _claude_limits(name: str) -> ModelLimits | None:
    if _CLAUDE_ALWAYS_LARGE.search(name) and "claude" in name:
        return _LARGE
    match = _CLAUDE_VERSIONED.search(name)
    if not match:
        return None
    family, major, minor = match.group(1), int(match.group(2)), int(match.group(3) or 0)
    return _LARGE if (major, minor) >= _CLAUDE_FLOOR[family] else None


def known_model_limits(model: str | None) -> ModelLimits | None:
    """Limits of *model* when its family is known, else None.

    Accepts provider prefixes and spellings seen in presets:
    ``anthropic/claude-sonnet-5.5``, ``us.anthropic.claude-opus-5-5``,
    ``openai/gpt-6-luna``, ``moonshotai/kimi-k3``.
    """
    if not model or not isinstance(model, str):
        return None
    name = model.strip().lower()
    if "claude" in name:
        return _claude_limits(name)
    if _GPT_6.search(name):
        return _LARGE
    if _KIMI_K3.search(name):
        return _LARGE
    return None
