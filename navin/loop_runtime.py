"""Wall-clock guards shared by Career, Trading and Marketing loops / heartbeat."""

from __future__ import annotations

from threading import Event, Thread
from typing import Any, Callable

from navin.loop_schedule import next_due_after

ERROR_BACKOFF_S = (120.0, 300.0, 900.0, 1800.0)


class LoopDeadlineError(Exception):
    """A collect, cycle or watch exceeded its wall clock."""


def call_with_deadline(
    fn: Callable[[], Any],
    *,
    timeout_s: float,
    label: str,
    thread_prefix: str = "navin-desk",
) -> Any:
    """Run fn, or raise LoopDeadlineError when it exceeds timeout_s.

    The worker is a daemon thread so a hung network call cannot freeze the
    gateway or block interpreter shutdown. It is not joined on timeout.
    """
    if timeout_s <= 0:
        return fn()
    box: list[Any] = []
    err: list[BaseException] = []
    done = Event()

    def _run() -> None:
        try:
            box.append(fn())
        except BaseException as exc:
            err.append(exc)
        finally:
            done.set()

    Thread(target=_run, name=f"{thread_prefix}-{label}", daemon=True).start()
    if not done.wait(timeout_s):
        raise LoopDeadlineError(f"{label} exceeded {int(timeout_s)}s")
    if err:
        raise err[0]
    return box[0]


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def retry_due_after(
    state: dict[str, Any],
    *,
    clock: float,
    fallback_s: float,
) -> float:
    """Soon retry after a failed cycle, without jumping past the next slot."""
    try:
        streak = int(state.get("error_streak") or 0)
    except (TypeError, ValueError):
        streak = 1
    delay = ERROR_BACKOFF_S[min(max(streak - 1, 0), len(ERROR_BACKOFF_S) - 1)]
    scheduled = next_due_after(state, now=clock, fallback_s=fallback_s)
    retry = clock + delay
    if scheduled > clock:
        return min(retry, scheduled)
    return retry
