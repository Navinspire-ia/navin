# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

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

from navin.agent.memory import (
    CARRIED_USER_MESSAGES_HEADING,
    Consolidator,
    MemoryStore,
    carried_user_message,
    compose_carried_brief,
    split_carried_brief,
)
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

    def test_nothing_worth_carrying_keeps_only_the_user_messages(self) -> None:
        class Empty(_FakeProvider):
            async def chat_with_retry(self, *, messages, **_kwargs):
                return LLMResponse(content="(nothing)")

        self.provider = Empty()
        brief = _run(
            self.consolidator.handoff_brief(self._conversation(1), runtime=self._runtime())
        )
        self.assertEqual(brief, f"{CARRIED_USER_MESSAGES_HEADING}\n- step 0: please continue")
        only_tools = [{"role": "assistant", "content": "ran ls"}]
        self.assertIsNone(
            _run(self.consolidator.handoff_brief(only_tools, runtime=self._runtime()))
        )

    def test_a_provider_failure_does_not_raise(self) -> None:
        """A lost brief costs quality; an exception here would cost the turn."""
        self.provider = _FakeProvider(fail_handoff=True)
        brief = _run(
            self.consolidator.handoff_brief(self._conversation(1), runtime=self._runtime())
        )
        self.assertNotIn(_HANDOFF_MARK, brief or "")
        self.assertIn("step 0: please continue", brief or "")


class SecondCompactionTest(_HandoffTest):
    """Each compaction used to rewrite the brief from its own chunk only."""

    def test_the_previous_note_is_given_to_the_model_and_survives_a_failure(self) -> None:
        previous = compose_carried_brief("Goal: port the plugins", ["porte les plugins"])
        _run(self.consolidator.handoff_brief(
            self._conversation(1), runtime=self._runtime(), previous=previous,
        ))
        self.provider = _FakeProvider(fail_handoff=True)
        failed = _run(self.consolidator.handoff_brief(
            self._conversation(1), runtime=self._runtime(), previous=previous,
        ))
        self.assertTrue(failed.startswith("Goal: port the plugins"))
        self.assertIn("- porte les plugins\n- step 0: please continue", failed)

    def test_the_model_sees_the_previous_note(self) -> None:
        seen: list[str] = []

        class Spy(_FakeProvider):
            async def chat_with_retry(self, *, messages, **kwargs):
                seen.append(messages[1]["content"])
                return await super().chat_with_retry(messages=messages, **kwargs)

        self.provider = Spy()
        _run(self.consolidator.handoff_brief(
            self._conversation(1), runtime=self._runtime(),
            previous=compose_carried_brief("Goal: port the plugins", []),
        ))
        self.assertIn("Previous handover note", seen[0])
        self.assertIn("Goal: port the plugins", seen[0])

    def test_two_compactions_keep_the_first_user_message(self) -> None:
        session = self.sessions.get_or_create("twice")
        session.messages = [{"role": "user", "content": "le but: porter les plugins"}]
        session.messages += self._conversation(40)
        self.sessions.save(session)
        runtime = self._runtime(window=600)
        _run(self.consolidator.maybe_consolidate_by_tokens(session, runtime=runtime))
        session.messages += self._conversation(40)
        self.sessions.save(session)
        _run(self.consolidator.maybe_consolidate_by_tokens(session, runtime=runtime))
        carried = self.sessions.get_or_create("twice").metadata["_last_summary"]["text"]
        body, users = split_carried_brief(carried)
        self.assertIn(_HANDOFF_MARK, body)
        self.assertEqual(users[0], "le but: porter les plugins")
        self.assertLessEqual(len(users), 10)

    def test_edited_files_and_last_request_survive_two_failed_handoffs(self) -> None:
        """The edited files came only from the model's note: a failed handoff lost them."""
        import json as _json

        chunk = [
            {"role": "user", "content": "corrige le build"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "edit_file", "arguments": _json.dumps({"path": "src/a.py"})},
            }]},
            {"role": "user", "content": "non, utilise npm run build"},
        ]
        self.provider = _FakeProvider(fail_handoff=True)
        first = _run(self.consolidator.handoff_brief(chunk, runtime=self._runtime()))
        later = [{"role": "assistant", "content": "", "tool_calls": [{
            "id": "c2", "type": "function",
            "function": {"name": "write_file", "arguments": {"files": [{"path": "b.md"}]}},
        }]}]
        second = _run(self.consolidator.handoff_brief(later, runtime=self._runtime(), previous=first))
        body, users = split_carried_brief(second)
        self.assertIn("- src/a.py\n- b.md", body)
        self.assertEqual(users[-1], "non, utilise npm run build")

    def test_runtime_reminders_are_not_carried_as_user_words(self) -> None:
        from navin.agent.scope_anchor import exploration_budget_message
        from navin.utils.runtime import build_ignored_nudge_reminder_message

        nudges = [exploration_budget_message(8), build_ignored_nudge_reminder_message(1)]
        for nudge in nudges:
            self.assertEqual(carried_user_message(nudge), "")
        self.assertEqual(
            carried_user_message({"role": "user", "content": "dis ok\n\n[Runtime Context - x]\ny"}),
            "dis ok",
        )
        self.assertEqual(
            carried_user_message({"role": "user", "content": "x", "injected_event": "exec_finished"}), ""
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


class UrgentOnlyConsolidationTest(_HandoffTest):
    """The turn's BUILD consolidates only what the prompt cannot fit without."""

    def _session(self):
        session = self.sessions.get_or_create("s-urgent")
        session.messages = self._conversation(40)
        self.sessions.save(session)
        return session

    def test_an_overflowing_prompt_is_still_consolidated_inline(self) -> None:
        session = self._session()
        _run(
            self.consolidator.maybe_consolidate_by_tokens(
                session, runtime=self._runtime(window=600), urgent_only=True
            )
        )
        self.assertTrue(self.provider.prompts)
        self.assertGreater(self.sessions.get_or_create("s-urgent").last_consolidated, 0)

    def test_the_turn_does_not_wait_behind_a_background_consolidation(self) -> None:
        """Waiting here held the first token for every archive round."""
        session = self._session()
        self.consolidator._token_consolidating.add(session.key)
        _run(
            self.consolidator.maybe_consolidate_by_tokens(
                session, runtime=self._runtime(window=600), urgent_only=True
            )
        )
        self.assertEqual(self.provider.prompts, [])

    def test_the_running_marker_is_cleared_afterwards(self) -> None:
        session = self._session()
        _run(
            self.consolidator.maybe_consolidate_by_tokens(
                session, runtime=self._runtime(window=600)
            )
        )
        self.assertEqual(self.consolidator._token_consolidating, set())

    def test_the_start_notice_fires_once_when_compacting(self) -> None:
        session = self._session()
        calls: list[int] = []

        async def notice() -> None:
            calls.append(1)

        _run(
            self.consolidator.maybe_consolidate_by_tokens(
                session, runtime=self._runtime(window=600), urgent_only=True, on_start=notice
            )
        )
        self.assertEqual(calls, [1])

    def test_no_start_notice_when_nothing_needs_compacting(self) -> None:
        session = self.sessions.get_or_create("s-small")
        calls: list[int] = []

        async def notice() -> None:
            calls.append(1)

        _run(
            self.consolidator.maybe_consolidate_by_tokens(
                session, runtime=self._runtime(window=600), urgent_only=True, on_start=notice
            )
        )
        self.assertEqual(calls, [])

    def test_a_failing_start_notice_does_not_stop_compaction(self) -> None:
        session = self._session()

        async def notice() -> None:
            raise RuntimeError("ui gone")

        _run(
            self.consolidator.maybe_consolidate_by_tokens(
                session, runtime=self._runtime(window=600), urgent_only=True, on_start=notice
            )
        )
        self.assertGreater(self.sessions.get_or_create("s-urgent").last_consolidated, 0)


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


class RecentHistoryStaleStateTest(unittest.TestCase):
    """Old task state in history.jsonl must not steer every later turn."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        from navin.agent.context import ContextBuilder

        self.builder = ContextBuilder(Path(self._tmp.name))

    def test_only_the_newest_entries_keep_their_ephemeral_lines(self) -> None:
        memory = self.builder.memory
        memory.append_history(
            "- [durable] Repo builds with make build\n"
            "- [ephemeral] Active task: port the skills\n"
            "  - 3 of 7 written\n"
            "- [permanent] Replies in French",
            session_key="s",
        )
        memory.append_history("- [ephemeral] Board task t-1 is open", session_key="s")
        memory.append_history("- [durable] Tests take 20 min", session_key="s")
        memory.append_history("- [ephemeral] Current step: hooks runtime", session_key="s")
        prompt = self.builder.build_system_prompt(session_key="s")
        for kept in ("make build", "Replies in French", "Tests take 20 min", "Current step: hooks runtime"):
            self.assertIn(kept, prompt)
        for dropped in ("port the skills", "3 of 7 written", "Board task t-1"):
            self.assertNotIn(dropped, prompt)

    def test_an_entry_made_only_of_stale_state_leaves_no_empty_bullet(self) -> None:
        from navin.agent.context import ContextBuilder

        self.assertEqual(
            ContextBuilder._history_entry_for_prompt("- [ephemeral] Active task: x", recent=False), ""
        )
        self.assertEqual(
            ContextBuilder._history_entry_for_prompt("plain archive text", recent=False),
            "plain archive text",
        )


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
            "estimate_session_prompt_tokens_async",
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
            "estimate_session_prompt_tokens_async",
            wraps=self.consolidator.estimate_session_prompt_tokens_async,
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
            "estimate_session_prompt_tokens_async",
            wraps=self.consolidator.estimate_session_prompt_tokens_async,
        ) as spy:
            _run(self.consolidator.maybe_consolidate_by_tokens(session, runtime=other))
        self.assertGreater(spy.call_count, 0)

    def test_async_estimate_matches_the_sync_one(self) -> None:
        session = self._session(turns=6)
        runtime = self._runtime(window=self._WINDOW)
        sync = self.consolidator.estimate_session_prompt_tokens(session, runtime=runtime)
        threaded = _run(
            self.consolidator.estimate_session_prompt_tokens_async(session, runtime=runtime)
        )
        self.assertEqual(sync, threaded)
        self.assertGreater(sync[0], 0)

    def test_async_estimate_prefers_the_provider_peak_like_the_sync_one(self) -> None:
        from navin.session.context_usage_meta import LAST_CONTEXT_USAGE_KEY

        session = self._session(turns=2)
        session.metadata[LAST_CONTEXT_USAGE_KEY] = {"peak_prompt_tokens": 150_000}
        runtime = self._runtime(window=self._WINDOW)
        sync = self.consolidator.estimate_session_prompt_tokens(session, runtime=runtime)
        threaded = _run(
            self.consolidator.estimate_session_prompt_tokens_async(session, runtime=runtime)
        )
        self.assertEqual(sync, threaded)
        self.assertEqual(sync[0], 150_000)
        self.assertTrue(sync[1].endswith("+last_peak"))


class ManualCompactTest(_HandoffTest):
    """``/compact [focus]`` summarizes now and steers the note with the focus."""

    def _loop(self):
        from types import SimpleNamespace

        runtime = self._runtime()
        return SimpleNamespace(
            _active_tasks={}, sessions=self.sessions, consolidator=self.consolidator,
            llm_runtime=lambda: runtime,
        )

    def _dispatch(self, loop, raw: str):
        from types import SimpleNamespace

        from navin.command.builtin import build_help_text, register_builtin_commands
        from navin.command.router import CommandContext, CommandRouter

        self.assertIn("/compact [focus]", build_help_text())
        router = CommandRouter()
        register_builtin_commands(router)
        msg = SimpleNamespace(channel="cli", chat_id="c", metadata={}, content=raw)
        ctx = CommandContext(msg=msg, session=None, key="cli:c", raw=raw, loop=loop)
        return _run(router.dispatch(ctx))

    def test_compact_with_focus_keeps_the_latest_turns(self) -> None:
        session = self.sessions.get_or_create("cli:c")
        session.messages = self._conversation(6)
        self.sessions.save(session)
        out = self._dispatch(self._loop(), "/compact the API wiring")
        self.assertIn("older messages summarized", out.content)
        self.assertIn("Summary focus: the API wiring", out.content)
        kept = self.sessions.get_or_create("cli:c").messages
        self.assertLess(len(kept), 12)
        self.assertEqual(kept[-1]["content"], "edited file_5.py and ran the tests")
        handoff = [p for p in self.provider.prompts if "handover note" in p]
        self.assertTrue(handoff and "focus on: the API wiring" in handoff[-1])

    def test_short_conversation_and_running_turn_are_refused(self) -> None:
        session = self.sessions.get_or_create("cli:c")
        session.messages = self._conversation(1)
        self.sessions.save(session)
        self.assertIn("still short", self._dispatch(self._loop(), "/compact").content)
        self.assertEqual(self.provider.prompts, [])

        class _Busy:
            def done(self) -> bool:
                return False

        loop = self._loop()
        loop._active_tasks = {"cli:c": [_Busy()]}
        self.assertIn("still running", self._dispatch(loop, "/compact").content)


class CheckpointSummarizeTest(ManualCompactTest):
    """``/checkpoint summarize <name> [upto|from] [focus]``."""

    def _setup(self):
        from navin.agent.checkpoints import CheckpointStore

        loop = self._loop()
        loop.checkpoints = CheckpointStore(Path(self._tmp.name) / "ws")
        session = self.sessions.get_or_create("cli:c")
        session.messages = self._conversation(3)
        self.sessions.save(session)
        name = loop.checkpoints.save(session)["name"]
        session.messages = [*session.messages, *self._conversation(2)]
        self.sessions.save(session)
        return loop, name

    def test_upto_replaces_the_messages_before_the_checkpoint(self) -> None:
        loop, name = self._setup()
        before = len(self.sessions.get_or_create("cli:c").messages)
        out = self._dispatch(loop, f"/checkpoint summarize {name} upto the parser")
        self.assertIn("Summarized 6 messages up to", out.content)
        kept = self.sessions.get_or_create("cli:c").messages
        self.assertEqual(len(kept), before - 6 + 1)
        self.assertTrue(kept[0]["content"].startswith("[Summary of 6 earlier messages"))
        self.assertTrue(any("focus on: the parser" in p for p in self.provider.prompts))

    def test_from_keeps_the_conversation_up_to_the_checkpoint(self) -> None:
        loop, name = self._setup()
        out = self._dispatch(loop, f"/checkpoint summarize {name} from")
        self.assertIn("after", out.content)
        kept = self.sessions.get_or_create("cli:c").messages
        self.assertEqual(len(kept), 7)
        self.assertIn("after checkpoint", kept[-1]["content"])
        usage = self._dispatch(loop, "/checkpoint summarize")
        self.assertIn("Usage", usage.content)


if __name__ == "__main__":
    unittest.main()
