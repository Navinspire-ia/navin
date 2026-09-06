"""Shared desk deadline and error backoff must stay non-blocking."""

from __future__ import annotations

import time
import unittest

from navin.loop_runtime import (
    ERROR_BACKOFF_S,
    LoopDeadlineError,
    as_float,
    call_with_deadline,
    retry_due_after,
)


class LoopRuntimeDeadlineTest(unittest.TestCase):
    def test_returns_the_function_result(self) -> None:
        self.assertEqual(call_with_deadline(lambda: 7, timeout_s=1, label="ok"), 7)

    def test_timeout_zero_runs_inline(self) -> None:
        self.assertEqual(call_with_deadline(lambda: "inline", timeout_s=0, label="inline"), "inline")

    def test_timeout_raises_quickly(self) -> None:
        def hang() -> None:
            time.sleep(2)

        started = time.monotonic()
        with self.assertRaises(LoopDeadlineError) as ctx:
            call_with_deadline(hang, timeout_s=0.05, label="watch")
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIn("watch exceeded", str(ctx.exception))

    def test_propagates_the_worker_error(self) -> None:
        def boom() -> None:
            raise RuntimeError("bus down")

        with self.assertRaises(RuntimeError) as ctx:
            call_with_deadline(boom, timeout_s=1, label="watch")
        self.assertEqual(str(ctx.exception), "bus down")


class LoopRuntimeBackoffTest(unittest.TestCase):
    def test_as_float_defaults(self) -> None:
        self.assertEqual(as_float(None), 0.0)
        self.assertEqual(as_float("x", 3.5), 3.5)
        self.assertEqual(as_float("12.5"), 12.5)

    def test_first_error_retries_in_two_minutes(self) -> None:
        clock = 1_700_000_000.0
        due = retry_due_after({"error_streak": 1, "enabled": True}, clock=clock, fallback_s=900)
        self.assertEqual(due, clock + ERROR_BACKOFF_S[0])

    def test_backoff_caps_at_the_last_slot(self) -> None:
        clock = 1_700_000_000.0
        due = retry_due_after({"error_streak": 99, "enabled": True}, clock=clock, fallback_s=86400)
        self.assertEqual(due, clock + ERROR_BACKOFF_S[-1])

    def test_does_not_jump_past_the_next_schedule(self) -> None:
        clock = 1_700_000_000.0
        due = retry_due_after({"error_streak": 1, "enabled": True}, clock=clock, fallback_s=30)
        self.assertEqual(due, clock + 30)


if __name__ == "__main__":
    unittest.main()
