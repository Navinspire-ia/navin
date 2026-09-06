"""Workspace token usage telemetry (per-model monthly quota)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from navin.agent.hook import AgentHook
from navin.agent.turn_hooks import AgentTurnHookSpec, build_agent_turn_hook
from navin.webui.token_usage import (
    TokenUsageHook,
    record_token_usage,
    token_usage_payload,
    token_usage_state_path,
)


class EveryPaidCallIsCountedTest(unittest.TestCase):
    """Two whole classes of LLM call reached the provider unrecorded.

    Ephemeral turns (titles, summaries, compaction) drop the extra hook chain
    to stay cheap, and subagents drive their own runner, so neither was ever
    seen by ``TokenUsageHook``. Both are billed like any other call, which is
    how the usage table sat still through an afternoon of work.
    """

    @staticmethod
    def _spec(*, ephemeral: bool, hooks: list[AgentHook]) -> AgentTurnHookSpec:
        return AgentTurnHookSpec(ephemeral=ephemeral, registered_hooks=list(hooks))

    @staticmethod
    def _chain(hook: AgentHook) -> list[AgentHook]:
        return list(getattr(hook, "_hooks", [hook]))

    def test_only_accounting_hooks_declare_themselves(self) -> None:
        self.assertTrue(TokenUsageHook.accounts_for_usage)
        self.assertFalse(AgentHook.accounts_for_usage)

    def test_budget_hooks_stay_out_so_ephemeral_turns_are_not_debited(self) -> None:
        from navin.cron.spend import CronSpendHook
        from navin.license_client import ManagedUsageHook

        for hook_cls in (CronSpendHook, ManagedUsageHook):
            with self.subTest(hook=hook_cls.__name__):
                self.assertFalse(hook_cls.accounts_for_usage)

    def test_an_ephemeral_turn_still_counts_its_tokens(self) -> None:
        usage = TokenUsageHook()
        built = build_agent_turn_hook(self._spec(ephemeral=True, hooks=[usage]))
        self.assertIn(usage, self._chain(built))

    def test_an_ephemeral_turn_still_skips_everything_else(self) -> None:
        usage, other = TokenUsageHook(), AgentHook()
        built = build_agent_turn_hook(self._spec(ephemeral=True, hooks=[usage, other]))
        chain = self._chain(built)
        self.assertIn(usage, chain)
        self.assertNotIn(other, chain)

    def test_an_ephemeral_turn_with_nothing_to_account_is_unchanged(self) -> None:
        built = build_agent_turn_hook(self._spec(ephemeral=True, hooks=[AgentHook()]))
        self.assertEqual(self._chain(built), [built])

    def test_an_ordinary_turn_still_runs_every_hook(self) -> None:
        usage, other = TokenUsageHook(), AgentHook()
        chain = self._chain(build_agent_turn_hook(self._spec(ephemeral=False, hooks=[usage, other])))
        self.assertIn(usage, chain)
        self.assertIn(other, chain)

    def test_a_subagent_carries_the_accounting_hook(self) -> None:
        from unittest import mock

        from navin.agent.subagent import SubagentManager

        usage = TokenUsageHook()
        with TemporaryDirectory() as tmp:
            manager = SubagentManager(
                workspace=Path(tmp),
                bus=mock.Mock(),
                max_tool_result_chars=4000,
                usage_hooks=[usage],
            )
            self.assertIn(usage, self._chain(manager._build_hook("t-1", None, None)))

    def test_a_subagent_without_accounting_keeps_its_own_hook_alone(self) -> None:
        from unittest import mock

        from navin.agent.subagent import SubagentManager

        with TemporaryDirectory() as tmp:
            manager = SubagentManager(
                workspace=Path(tmp),
                bus=mock.Mock(),
                max_tool_result_chars=4000,
            )
            built = manager._build_hook("t-1", None, None)
            self.assertEqual(self._chain(built), [built])


class TokenUsageModelsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.webui = Path(self._tmp.name)
        self.webui.mkdir(parents=True, exist_ok=True)
        self._patcher = patch(
            "navin.webui.token_usage.get_webui_dir",
            return_value=self.webui,
        )
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_records_per_model_and_month_aggregate(self) -> None:
        now = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)
        record_token_usage(
            {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140},
            source="user",
            model="deepseek/deepseek-chat",
            timezone_name="UTC",
            now=now,
        )
        record_token_usage(
            {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
            source="user",
            model="google/gemini-2.5-flash",
            timezone_name="UTC",
            now=now,
        )
        record_token_usage(
            {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            source="cron",
            model="deepseek/deepseek-chat",
            timezone_name="UTC",
            now=now,
        )

        path = token_usage_state_path()
        self.assertTrue(path.is_file())

        payload = token_usage_payload(timezone_name="UTC", now=now)
        self.assertEqual(payload["month"], "2026-08")
        self.assertEqual(payload["month_total"]["requests"], 3)
        self.assertEqual(payload["month_total"]["prompt_tokens"], 170)
        self.assertEqual(payload["month_total"]["completion_tokens"], 55)
        self.assertEqual(payload["month_total"]["total_tokens"], 225)

        models = {row["model"]: row for row in payload["models_month"]}
        self.assertEqual(models["deepseek/deepseek-chat"]["requests"], 2)
        self.assertEqual(models["deepseek/deepseek-chat"]["total_tokens"], 165)
        self.assertEqual(models["google/gemini-2.5-flash"]["requests"], 1)
        self.assertEqual(models["google/gemini-2.5-flash"]["prompt_tokens"], 50)
        # Sorted by total tokens desc
        self.assertEqual(payload["models_month"][0]["model"], "deepseek/deepseek-chat")

    def test_missing_model_counts_month_but_not_model_rows(self) -> None:
        now = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
        record_token_usage(
            {"prompt_tokens": 10, "completion_tokens": 2},
            timezone_name="UTC",
            now=now,
        )
        payload = token_usage_payload(timezone_name="UTC", now=now)
        self.assertEqual(payload["models_month"], [])
        self.assertEqual(payload["month_total"]["requests"], 1)
        self.assertEqual(payload["month_total"]["total_tokens"], 12)


if __name__ == "__main__":
    unittest.main()
