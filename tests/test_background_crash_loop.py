# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A background server that keeps crashing stops waking the agent up."""

from __future__ import annotations

from navin.agent.tools import shell


def test_crash_count_and_reset_on_success() -> None:
    shell._crash_history.clear()
    assert [shell._record_background_exit("s", "npm run dev", 1) for _ in range(4)] == [1, 2, 3, 4]
    # Same command with other spacing is the same command.
    assert shell._record_background_exit("s", "npm  run  dev", 1) == 5
    # Another chat or another command has its own count.
    assert shell._record_background_exit("t", "npm run dev", 1) == 1
    assert shell._record_background_exit("s", "npm run build", 1) == 1
    # A success clears it.
    assert shell._record_background_exit("s", "npm run dev", 0) == 0
    assert shell._record_background_exit("s", "npm run dev", 1) == 1
