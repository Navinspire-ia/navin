"""Offline evaluation harness for Navin agent responses."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navin.evals.runner import (
        EvalCase,
        EvalResult,
        GateResult,
        MockModel,
        Scoreboard,
        release_gate,
        run_dataset,
        scoreboard,
    )

__all__ = [
    "EvalCase",
    "EvalResult",
    "GateResult",
    "MockModel",
    "Scoreboard",
    "release_gate",
    "run_dataset",
    "scoreboard",
]


def __getattr__(name: str):
    if name in __all__:
        from navin.evals import runner as _runner

        return getattr(_runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
