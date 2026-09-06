"""Compaction must preserve the task, not only the memories.

When the context window fills mid-task, the text that survives is re-injected
into the next prompt. Extracting durable memory facts is the right job for
history.jsonl and the wrong one here: those instructions deliberately drop
anything derivable from the repository, which is precisely the plan, the files
already edited and the tests already run. These tests hold the two apart.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from navin.agent.memory import Consolidator, MemoryStore
from navin.providers.base import GenerationSettings, LLMResponse
from navin.session.manager import SessionManager
from navin.utils.llm_runtime import LLMRuntime

_ARCHIVE_MARK = "memory-facts"
_HANDOFF_MARK = "handoff-brief"


def _run(coro):
    return asyncio.run(coro)


class _FakeProvider:
    """Answers differently depending on which system prompt it was given.

    The point of the change under test is that two distinct prompts are used, so
    the fake identifies them by their instructions rather than by call order.
    """

    def __init__(self, *, fail_handoff: bool = False) -> None:
        self.generation = GenerationSettings()
        self.prompts: list[str] = []
        self.fail_handoff = fail_handoff

    async def chat_with_retry(self, *, messages, **_kwargs):
        system = messages[0]["content"]
        self.prompts.append(system)
        if "handover note" in system:
            if self.fail_handoff:
                return LLMResponse(content="boom", finish_reason="error")
            return LLMResponse(content=f"{_HANDOFF_MARK}: next step is to wire the API")
        return LLMResponse(content=f"- [durable] {_ARCHIVE_MARK}: user prefers tabs")

    def count_tokens(self, text: str) -> int:  # pragma: no cover - unused path
        return max(1, len(text) // 4)


class _HandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.store = MemoryStore(root / "memory")
        self.sessions = SessionManager(root / "sessions")
        self.provider = _FakeProvider()
        self.consolidator = Consolidator(
            store=self.store,
            sessions=self.sessions,
            # Estimation runs through this, so it has to return the real turns:
            # an empty list reads as an empty prompt and nothing consolidates.
            build_messages=self._build_messages,
            get_tool_definitions=lambda: [],
        )

    @staticmethod
    def _build_messages(*, history, session_summary=None, **_kwargs) -> list[dict]:
        messages = list(history)
        if session_summary:
            messages.insert(0, {"role": "system", "content": session_summary})
        return messages

    def _runtime(self, *, window: int = 4000) -> LLMRuntime:
        return LLMRuntime(
            provider=self.provider,  # type: ignore[arg-type]
            model="fake",
            generation=GenerationSettings(),
            context_window_tokens=window,
        )

    @staticmethod
    def _conversation(turns: int = 6) -> list[dict]:
        messages: list[dict] = []
        for i in range(turns):
            messages.append({"role": "user", "content": f"step {i}: please continue"})
            messages.append(
                {"role": "assistant", "content": f"edited file_{i}.py and ran the tests"}
            )
        return messages


class HandoffBriefTest(_HandoffTest):
    def test_the_brief_uses_its_own_prompt(self) -> None:
        brief = _run(
            self.consolidator.handoff_brief(
                self._conversation(2), runtime=self._runtime()
            )
        )
        self.assertIn(_HANDOFF_MARK, brief or "")
        self.assertIn("handover note", self.provider.prompts[0])

    def test_an_empty_chunk_asks_the_model_nothing(self) -> None:
        self.assertIsNone(
            _run(self.consolidator.handoff_brief([], runtime=self._runtime()))
        )
        self.assertEqual(self.provider.prompts, [])

    def test_nothing_worth_carrying_is_reported_as_nothing(self) -> None:
        class Empty(_FakeProvider):
            async def chat_with_retry(self, *, messages, **_kwargs):
                return LLMResponse(content="(nothing)")

        self.provider = Empty()
        self.assertIsNone(
            _run(
                self.consolidator.handoff_brief(
                    self._conversation(1), runtime=self._runtime()
                )
            )
        )

    def test_a_provider_failure_does_not_raise(self) -> None:
        """A lost brief costs quality; an exception here would cost the turn."""
        self.provider = _FakeProvider(fail_handoff=True)
        self.assertIsNone(
            _run(
                self.consolidator.handoff_brief(
                    self._conversation(1), runtime=self._runtime()
                )
            )
        )


class ConsolidationCarriesTheTaskTest(_HandoffTest):
    def _consolidate(self) -> str:
        session = self.sessions.get_or_create("s1")
        session.messages = self._conversation(40)
        self.sessions.save(session)
        _run(
            self.consolidator.maybe_consolidate_by_tokens(
                session, runtime=self._runtime(window=600)
            )
        )
        fresh = self.sessions.get_or_create("s1")
        return (fresh.metadata.get("_last_summary") or {}).get("text", "")

    def test_the_reinjected_text_is_the_handoff_not_the_memory_facts(self) -> None:
        carried = self._consolidate()
        self.assertIn(_HANDOFF_MARK, carried)
        self.assertNotIn(_ARCHIVE_MARK, carried)

    def test_the_memory_facts_still_reach_history(self) -> None:
        """The brief is additional, not a replacement: long-term memory is fed too."""
        self._consolidate()
        self.assertIn(
            _ARCHIVE_MARK, self.store.history_file.read_text(encoding="utf-8")
        )

    def test_the_brief_is_written_once_per_call(self) -> None:
        """Per-round briefs would multiply cost for no gain."""
        self._consolidate()
        briefs = [p for p in self.provider.prompts if "handover note" in p]
        self.assertEqual(len(briefs), 1)

    def test_a_failed_brief_falls_back_to_the_archive(self) -> None:
        """Something must survive compaction, even a note in the wrong shape."""
        self.provider = _FakeProvider(fail_handoff=True)
        self.assertIn(_ARCHIVE_MARK, self._consolidate())


class ResumeLabelTest(unittest.TestCase):
    """The carried notes must be framed as settled work, not as background.

    Labelled only as an archived summary, a model re-derives what it describes
    and sometimes redoes it, which is the expensive failure after a compaction.
    """

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from navin.agent.context import ContextBuilder

        self.builder = ContextBuilder(Path(self._tmp.name))

    def _prompt(self, summary: str | None) -> str:
        return self.builder.build_system_prompt(
            session_summary=summary,
            include_memory_recent_history=False,
        )

    def test_the_notes_are_included_and_framed_as_done(self) -> None:
        prompt = self._prompt("Rewired the exporter; tests green.")
        self.assertIn("Rewired the exporter", prompt)
        self.assertIn("do not redo", prompt.lower())

    def test_nothing_is_injected_without_a_summary(self) -> None:
        self.assertNotIn("do not redo", self._prompt(None).lower())


class ResumeMirrorTest(_HandoffTest):
    """The handoff brief must also land in the project's RESUME.md.

    Session metadata dies with /new; the continuity file is injected into
    every turn, so resuming must not depend on the agent having updated
    RESUME.md manually before the window filled.
    """

    def _project_session(self, key: str = "s2"):
        project = Path(self._tmp.name) / "project"
        (project / ".navin" / "continuity").mkdir(parents=True, exist_ok=True)
        session = self.sessions.get_or_create(key)
        session.messages = self._conversation(40)
        session.metadata["workspace_scope"] = {"project_path": str(project)}
        self.sessions.save(session)
        return project, session

    def _consolidate(self, session) -> None:
        _run(
            self.consolidator.maybe_consolidate_by_tokens(
                session, runtime=self._runtime(window=600)
            )
        )

    def test_the_brief_is_mirrored_into_the_project_resume(self) -> None:
        project, session = self._project_session()
        self._consolidate(session)
        resume = (project / ".navin" / "continuity" / "RESUME.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(_HANDOFF_MARK, resume)
        self.assertIn("navin:auto-handoff:start", resume)

    def test_a_second_compaction_replaces_the_block_instead_of_stacking(self) -> None:
        project, session = self._project_session()
        self._consolidate(session)
        session.messages.extend(self._conversation(40))
        self.sessions.save(session)
        self._consolidate(session)
        resume = (project / ".navin" / "continuity" / "RESUME.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(resume.count("navin:auto-handoff:start"), 1)

    def test_without_a_project_scope_nothing_is_written(self) -> None:
        session = self.sessions.get_or_create("s3")
        session.messages = self._conversation(40)
        self.sessions.save(session)
        self._consolidate(session)
        self.assertFalse(
            (Path(self._tmp.name) / ".navin" / "continuity" / "RESUME.md").exists()
        )


class IdleTailGuardTest(unittest.TestCase):
    """Idle auto-compact must not truncate small sessions for no gain."""

    def test_small_idle_tails_are_left_alone(self) -> None:
        from navin.agent.autocompact import AutoCompact

        tail = [{"role": "user", "content": "hi"}] * 10
        self.assertFalse(AutoCompact._tail_is_heavy(tail))

    def test_many_messages_make_the_tail_heavy(self) -> None:
        from navin.agent.autocompact import AutoCompact

        tail = [{"role": "user", "content": "hi"}] * 24
        self.assertTrue(AutoCompact._tail_is_heavy(tail))

    def test_char_heavy_tails_are_heavy(self) -> None:
        from navin.agent.autocompact import AutoCompact

        tail = [{"role": "tool", "content": "x" * 20_000}]
        self.assertTrue(AutoCompact._tail_is_heavy(tail))


class LazyEstimateTest(_HandoffTest):
    """A healthy session must not pay a full token estimate on every BUILD.

    The first call estimates for real and seeds a per-session cache; later
    calls skip the probe-prompt rebuild while the conservative projection
    stays clearly under budget, and re-estimate as soon as it does not.
    """

    _WINDOW = 200_000

    def _session(self, turns: int = 4):
        session = self.sessions.get_or_create("lazy")
        session.messages = self._conversation(turns)
        self.sessions.save(session)
        return session

    def _consolidate(self, session) -> None:
        _run(
            self.consolidator.maybe_consolidate_by_tokens(
                session, runtime=self._runtime(window=self._WINDOW)
            )
        )

    def test_first_call_seeds_the_estimate_cache(self) -> None:
        session = self._session()
        self._consolidate(session)
        self.assertIn(session.key, self.consolidator._estimate_cache)

    def test_second_call_skips_the_full_estimate(self) -> None:
        from unittest import mock

        session = self._session()
        self._consolidate(session)
        with mock.patch.object(
            self.consolidator,
            "estimate_session_prompt_tokens",
            side_effect=AssertionError("full estimate should have been skipped"),
        ):
            self._consolidate(session)

    def test_significant_growth_forces_a_real_estimate(self) -> None:
        from unittest import mock

        session = self._session()
        self._consolidate(session)
        # Grow the tail enough that the conservative projection crosses the
        # headroom threshold (chars/3 >= 70% of the budget).
        session.messages.append(
            {"role": "user", "content": "x" * (self._WINDOW * 3)}
        )
        with mock.patch.object(
            self.consolidator,
            "estimate_session_prompt_tokens",
            wraps=self.consolidator.estimate_session_prompt_tokens,
        ) as spy:
            self._consolidate(session)
        self.assertGreater(spy.call_count, 0)

    def test_model_change_invalidates_the_cache(self) -> None:
        from unittest import mock

        session = self._session()
        self._consolidate(session)
        other = LLMRuntime(
            provider=self.provider,  # type: ignore[arg-type]
            model="other-model",
            generation=GenerationSettings(),
            context_window_tokens=self._WINDOW,
        )
        with mock.patch.object(
            self.consolidator,
            "estimate_session_prompt_tokens",
            wraps=self.consolidator.estimate_session_prompt_tokens,
        ) as spy:
            _run(self.consolidator.maybe_consolidate_by_tokens(session, runtime=other))
        self.assertGreater(spy.call_count, 0)


if __name__ == "__main__":
    unittest.main()
