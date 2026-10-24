# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""How much of the context window a turn may fill before it is trimmed.

A model's output ceiling (128k for Kimi K3 or GPT) is a limit, not what a step
produces: a tool call or an edit is a few hundred tokens. Reserving the whole
ceiling out of a 200k window left 71k for the input, so every ordinary request
(26k to 60k tokens) sat above the point where old tool results are blanked and
the history is compressed, which rewrites the start of the conversation, drops
the provider's prompt cache and makes the model read the same files again.

The input budget is 90 % of the window for every model (180k of a 200k window,
450k of 500k, 900k of 1M), and never more than the window minus a realistic
output. Nothing is trimmed before the prompt reaches it (see ``soft_clear_limit``
for the optional earlier threshold), and the requested output is lowered when
the prompt gets close to the window (``room_for_output``).
"""

from __future__ import annotations

# Output tokens kept free when sizing the input budget (smaller on tiny windows).
OUTPUT_RESERVE = 10_000

# Share of the window a prompt may fill before it is trimmed, whatever the model.
WINDOW_SHARE = 0.9

# Headroom for tokenizer drift.
SAFETY_BUFFER = 1_024

# A configured soft-clear share never blanks before this many tokens when the
# budget is larger than it (0 means: only when the budget is exceeded).
SOFT_CLEAR_FLOOR = 80_000

# Tokens a step may add (tool results) between two requests.
_GROWTH_MARGIN = 8_000
_WINDOW_MARGIN = 1_024
_MIN_OUTPUT = 4_096


def output_reserve(max_output: object, window: int, default: int = 4_096) -> int:
    """Output tokens to keep free when sizing the input budget."""
    try:
        wanted = int(max_output)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        wanted = default
    return min(max(1, wanted), max(_MIN_OUTPUT, min(OUTPUT_RESERVE, window // 4)))


def input_budget_for(window: int, max_output: object) -> int:
    """Prompt tokens a request may hold: 90 % of the window.

    Never more than the window minus a realistic output and a safety margin, so a
    small window keeps room for the answer.
    """
    if window <= 0:
        return 0
    room = window - output_reserve(max_output, window) - SAFETY_BUFFER
    budget = min(room, int(window * WINDOW_SHARE))
    return budget if budget > 0 else 0


def soft_clear_limit(budget: int, ratio: float) -> int:
    """Prompt size from which stale tool results start to be blanked (0 = never)."""
    if budget <= 0 or ratio <= 0:
        return 0
    by_ratio = int(budget * ratio)
    # A small budget keeps the configured share: it has no room for a floor.
    return by_ratio if budget <= SOFT_CLEAR_FLOOR else max(by_ratio, SOFT_CLEAR_FLOOR)


def room_for_output(max_tokens: object, window: int, last_prompt_tokens: int) -> object:
    """The output to request: ``max_tokens``, lowered when the prompt nears the window.

    Providers refuse a request whose prompt plus requested output exceeds the
    window, and one refused request per step is a wasted round trip. The last
    prompt size comes from the previous reply; a step may add some tool results.
    """
    if not isinstance(max_tokens, int) or window <= 0 or last_prompt_tokens <= 0:
        return max_tokens
    room = window - last_prompt_tokens - _GROWTH_MARGIN - _WINDOW_MARGIN
    if room >= max_tokens:
        return max_tokens
    return max(_MIN_OUTPUT, room)
