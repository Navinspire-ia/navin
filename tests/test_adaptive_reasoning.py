"""Adaptive reasoning effort: Plan thinks, Agent executes.

Agent caps to none so a config of high cannot turn GLM/Grok thinking on.
Plan still floors at high. An explicit UI override beats both.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from navin.agent.adaptive_reasoning import adaptive_reasoning_effort
from navin.quality.verification_log import record_verification


class PolicyTest(unittest.TestCase):
    def test_plan_mode_floors_at_high(self) -> None:
        for base in (None, "minimal", "low", "medium"):
            self.assertEqual(
                "high",
                adaptive_reasoning_effort(base, composer_mode="plan"),
                f"base={base!r}",
            )

    def test_plan_mode_never_downgrades(self) -> None:
        self.assertEqual(
            "xhigh", adaptive_reasoning_effort("xhigh", composer_mode="plan")
        )

    def test_agent_mode_turns_configured_high_off(self) -> None:
        self.assertEqual("none", adaptive_reasoning_effort(None, composer_mode="agent"))
        self.assertEqual("none", adaptive_reasoning_effort("low", composer_mode="agent"))
        self.assertEqual("none", adaptive_reasoning_effort("high", composer_mode="agent"))

    def test_explicit_think_hard_survives_on_agent(self) -> None:
        self.assertEqual(
            "xhigh", adaptive_reasoning_effort("xhigh", composer_mode="agent")
        )
        self.assertEqual(
            "adaptive", adaptive_reasoning_effort("adaptive", composer_mode="agent")
        )

    def test_verify_failure_does_not_turn_agent_thinking_on(self) -> None:
        self.assertEqual(
            "none",
            adaptive_reasoning_effort(
                "low", composer_mode="agent", after_verify_failure=True
            ),
        )
        self.assertEqual(
            "none",
            adaptive_reasoning_effort(
                "high", composer_mode="agent", after_verify_failure=True
            ),
        )

    def test_explicit_override_beats_everything(self) -> None:
        self.assertEqual(
            "minimal",
            adaptive_reasoning_effort(
                "high",
                composer_mode="plan",
                override="minimal",
                after_verify_failure=True,
            ),
        )
        self.assertEqual(
            "none",
            adaptive_reasoning_effort("high", composer_mode="plan", override="NONE"),
        )

    def test_garbage_override_is_ignored(self) -> None:
        self.assertEqual(
            "high",
            adaptive_reasoning_effort(
                "medium", composer_mode="plan", override="turbo-max"
            ),
        )


class LoopWiringTest(unittest.TestCase):
    """The loop helper reads composer mode; verify logs no longer bump Agent."""

    def _loop_stub(self):
        from navin.agent.loop import AgentLoop

        return AgentLoop.__new__(AgentLoop)

    def _runtime(self, effort: str | None):
        from navin.providers.base import GenerationSettings
        from navin.utils.llm_runtime import LLMRuntime

        return LLMRuntime(
            provider=object(),  # type: ignore[arg-type]
            model="m",
            generation=GenerationSettings(temperature=0.3, reasoning_effort=effort),
            context_window_tokens=10_000,
        )

    def test_recent_failed_verify_does_not_bump_agent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-ar-") as tmp:
            record_verification(
                Path(tmp), source="verify", ok=False, summary="2 failed"
            )
            adapted = self._loop_stub()._adapt_reasoning_effort(
                self._runtime("low"), {}, tmp
            )
            self.assertEqual("none", adapted.generation.reasoning_effort)

    def test_green_verify_stays_off_on_agent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-ar-") as tmp:
            record_verification(Path(tmp), source="verify", ok=True)
            adapted = self._loop_stub()._adapt_reasoning_effort(
                self._runtime("low"), {}, tmp
            )
            self.assertEqual("none", adapted.generation.reasoning_effort)

    def test_stale_failure_stays_off_on_agent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="navin-ar-") as tmp:
            record_verification(Path(tmp), source="verify", ok=False)
            log = Path(tmp) / ".navin" / "quality" / "verification-log.json"
            import json

            entries = json.loads(log.read_text(encoding="utf-8"))
            entries[-1]["ts"] = time.time() - 3600
            log.write_text(json.dumps(entries), encoding="utf-8")
            adapted = self._loop_stub()._adapt_reasoning_effort(
                self._runtime("low"), {}, tmp
            )
            self.assertEqual("none", adapted.generation.reasoning_effort)

    def test_plan_metadata_floors_at_high(self) -> None:
        adapted = self._loop_stub()._adapt_reasoning_effort(
            self._runtime(None), {"composer_mode": "plan"}, None
        )
        self.assertEqual("high", adapted.generation.reasoning_effort)

    def test_agent_high_is_rewritten_to_none(self) -> None:
        runtime = self._runtime("high")
        adapted = self._loop_stub()._adapt_reasoning_effort(runtime, {}, None)
        self.assertEqual("none", adapted.generation.reasoning_effort)
        self.assertIsNot(runtime, adapted)


if __name__ == "__main__":
    unittest.main()
