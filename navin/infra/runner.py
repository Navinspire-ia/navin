# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Run one program without a shell, with a timeout, and say what happened."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from navin.utils.proc import no_window_kwargs


@dataclass
class RunResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        return (self.stdout or "") + (("\n" + self.stderr) if self.stderr else "")


Runner = Callable[..., RunResult]


def default_runner(argv: list[str], *, timeout: float = 120, env: dict[str, str] | None = None,
                   cwd: str | None = None, input_text: str | None = None) -> RunResult:
    """``subprocess.run`` without a shell; a missing program is exit 127, a timeout 124."""
    started = time.monotonic()
    merged = {**os.environ, **(env or {})}
    # Never wait on an interactive prompt: pagers off, no terminal prompts.
    merged.setdefault("AWS_PAGER", "")
    merged.setdefault("PAGER", "cat")
    merged.setdefault("GIT_TERMINAL_PROMPT", "0")
    merged.setdefault("CLOUDSDK_CORE_DISABLE_PROMPTS", "1")
    try:
        completed = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=merged, cwd=cwd,
            input=input_text if input_text is not None else "", errors="replace",
            **no_window_kwargs(),
        )
        return RunResult(argv, completed.returncode, completed.stdout or "", completed.stderr or "",
                         int((time.monotonic() - started) * 1000))
    except FileNotFoundError:
        return RunResult(argv, 127, "", f"{argv[0]}: command not found", int((time.monotonic() - started) * 1000))
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return RunResult(argv, 124, out, err + f"\ntimed out after {timeout:.0f}s", int((time.monotonic() - started) * 1000), True)
    except OSError as exc:
        return RunResult(argv, 126, "", f"{argv[0]}: {exc}", int((time.monotonic() - started) * 1000))
