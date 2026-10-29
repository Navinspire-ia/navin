#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Easing curves for frame-by-frame animation.

Every function maps a progress value t in [0, 1] to an eased value with
f(0) == 0 and f(1) == 1. Values of t outside [0, 1] are clamped first, so
callers can feed raw frame ratios without guarding the edges.
"""

from __future__ import annotations

import math
from collections.abc import Callable


def _clamp(t: float) -> float:
    return 0.0 if t < 0.0 else 1.0 if t > 1.0 else float(t)


def linear(t: float) -> float:
    """Constant speed."""
    return _clamp(t)


def ease_in_quad(t: float) -> float:
    """Slow start, quadratic."""
    t = _clamp(t)
    return t * t


def ease_out_quad(t: float) -> float:
    """Fast start, quadratic slow-down."""
    t = _clamp(t)
    return 1.0 - (1.0 - t) * (1.0 - t)


def ease_in_out_quad(t: float) -> float:
    """Slow at both ends, quadratic."""
    t = _clamp(t)
    if t < 0.5:
        return 2.0 * t * t
    return 1.0 - ((-2.0 * t + 2.0) ** 2) / 2.0


def ease_in_cubic(t: float) -> float:
    """Slow start, cubic."""
    t = _clamp(t)
    return t ** 3


def ease_out_cubic(t: float) -> float:
    """Fast start, cubic slow-down."""
    t = _clamp(t)
    return 1.0 - (1.0 - t) ** 3


def ease_in_out_cubic(t: float) -> float:
    """Slow at both ends, cubic."""
    t = _clamp(t)
    if t < 0.5:
        return 4.0 * t ** 3
    return 1.0 - ((-2.0 * t + 2.0) ** 3) / 2.0


def ease_out_elastic(t: float) -> float:
    """Overshoots the target and springs back, settling at 1."""
    t = _clamp(t)
    if t == 0.0 or t == 1.0:
        return t
    period = (2.0 * math.pi) / 3.0
    return math.pow(2.0, -10.0 * t) * math.sin((t * 10.0 - 0.75) * period) + 1.0


def ease_out_bounce(t: float) -> float:
    """Drops onto the target and bounces a few times before resting."""
    t = _clamp(t)
    n1 = 7.5625
    d1 = 2.75
    if t < 1.0 / d1:
        return n1 * t * t
    if t < 2.0 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


EASINGS: dict[str, Callable[[float], float]] = {
    "linear": linear,
    "ease_in_quad": ease_in_quad,
    "ease_out_quad": ease_out_quad,
    "ease_in_out_quad": ease_in_out_quad,
    "ease_in_cubic": ease_in_cubic,
    "ease_out_cubic": ease_out_cubic,
    "ease_in_out_cubic": ease_in_out_cubic,
    "ease_out_elastic": ease_out_elastic,
    "ease_out_bounce": ease_out_bounce,
}


def ease(name: str, t: float) -> float:
    """Apply the easing called ``name`` to ``t``. Unknown names raise KeyError."""
    try:
        fn = EASINGS[name]
    except KeyError:
        raise KeyError(f"unknown easing {name!r}; choose from {sorted(EASINGS)}") from None
    return fn(t)


def lerp(start: float, end: float, t: float, easing: str = "linear") -> float:
    """Interpolate between two values with an easing curve."""
    return start + (end - start) * ease(easing, t)


if __name__ == "__main__":
    for label, fn in EASINGS.items():
        samples = " ".join(f"{fn(i / 8):.3f}" for i in range(9))
        print(f"{label:<20} {samples}")
