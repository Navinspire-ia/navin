# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Scope anchor: a brief that names where to look is followed, not re-derived.

The case behind this file: "compare the prod and preprod branches of
forgejo/lynara, the celery of the crm service breaks after the merge". The
old agent listed the whole project to get oriented. The named targets are
now extracted, put in front of the model every turn, and a turn whose first
look-around batches touch none of them is told once where the request pointed.
"""

from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.scope_anchor import (
    MAX_TARGETS,
    calls_touch_targets,
    is_action_call,
    is_orientation_batch,
    is_readonly_shell_call,
    is_search_batch,
    named_targets,
    scope_anchor_context_provider,
    scope_anchor_lines,
    scope_drift_message,
)
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime

LYNARA_BRIEF = (
    "pour le porojet lynara sur la prod on a les ms ocr, ocr-rh et ms sont separés, "
    "et le celery fonctionne correctement, la prod est deployer avec la branche main, "
    "NB : le celery crm utlise l'image de ms crm, le celery ocr utilise l'image ocr, "
    "mais en faisant la fusion de tous les ms (ocr, ocr-rh et crm), le celery ne "
    "fonctionne plus, donc tu investigue, tu compare les deux version et tu trouve "
    "la solution, lynara sur la prod est deployer avec la branche main de "
    "forgejo/unified_deploy, et les images buildés sont buildés apartir de la "
    "branche (prod pour la prod) et (preprod sur l'infra test où les ms sont "
    "fusionner) du projet sous forgejo/lynara, tu as acces au terminal et au cluster"
)


class NamedTargetsTest(unittest.TestCase):
    def test_navin_product_words_are_not_project_targets(self) -> None:
        # The user's own phrases: they name Navin's app, not a folder.
        self.assertEqual(named_targets("le menu du module Code ne s'ouvre plus"), [])
        self.assertEqual(named_targets("ignorer les noms de modules de Navin"), [])
        self.assertEqual(named_targets("elle lance cargo ou pytest sur des fichiers d'interface"), [])
        self.assertEqual(named_targets("open the Marketing module and the files of code"), [])

    def test_real_places_after_the_same_nouns_are_kept(self) -> None:
        self.assertEqual(named_targets("le module billing casse"), ["billing"])
        self.assertEqual(named_targets("le fichier navin/tui/app.py"), ["navin/tui/app.py"])
        self.assertEqual(named_targets("regarde le dossier navin"), ["navin"])
        self.assertEqual(named_targets("le module `code` du backend"), ["code"])

    def test_the_lynara_brief_yields_its_repos_branches_and_services(self) -> None:
        targets = named_targets(LYNARA_BRIEF)
        for expected in ("forgejo/unified_deploy", "forgejo/lynara", "main", "prod", "preprod", "ocr", "crm"):
            self.assertIn(expected, targets, targets)
        # Prose that followed a scope noun is not a place.
        for junk in ("buildés", "sous", "les", "de"):
            self.assertNotIn(junk, targets, targets)

    def test_paths_and_files_are_found_in_english_too(self) -> None:
        targets = named_targets(
            "fix the bug in navin/agent/runner.py and the test in tests/test_runner.py, "
            "see https://example.com/some/path for context"
        )
        self.assertEqual(targets, ["navin/agent/runner.py", "tests/test_runner.py"])

    def test_scope_nouns_articles_and_backticks(self) -> None:
        targets = named_targets(
            "Le service `payments` plante, regarde le dossier k8s et le fichier "
            "docker-compose.prod.yml, branche release/2.4"
        )
        self.assertEqual(targets, ["payments", "k8s", "docker-compose.prod.yml", "release/2.4"])

    def test_two_articles_between_the_noun_and_the_place(self) -> None:
        self.assertIn("payments", named_targets("deploy the branch of the payments service"))

    def test_mentions_keep_their_path(self) -> None:
        self.assertEqual(named_targets("@src/api/ et @README.md doivent être relus"), ["src/api/", "README.md"])

    def test_small_talk_and_vague_requests_name_nothing(self) -> None:
        self.assertEqual(named_targets("bonjour, peux-tu m'expliquer ce que fait ce projet ?"), [])
        self.assertEqual(named_targets("rends le projet plus rapide, il est lent depuis 3 versions"), [])
        self.assertEqual(named_targets("the request took 250 ms and the image is 3 MB"), [])
        self.assertEqual(named_targets(""), [])
        self.assertEqual(named_targets(None), [])

    def test_verbs_after_a_scope_noun_are_not_places(self) -> None:
        targets = named_targets("the project uses Dockerfile; le service plante et le fichier contient du JSON")
        self.assertEqual(targets, ["Dockerfile"])

    def test_a_backticked_command_is_not_a_place_but_its_directory_is(self) -> None:
        self.assertEqual(named_targets("run `pytest -q tests/` then report"), ["tests/"])

    def test_order_of_appearance_and_dedup(self) -> None:
        targets = named_targets("regarde le dossier ocr, puis ocr/celery.py, puis le service OCR encore")
        self.assertEqual(targets, ["ocr", "ocr/celery.py"])

    def test_the_list_is_capped(self) -> None:
        text = " ".join(f"src/mod{i}.py" for i in range(40))
        self.assertEqual(len(named_targets(text)), MAX_TARGETS)

    def test_prose_seen_in_real_requests_names_nothing(self) -> None:
        # Sentences from saved sessions that produced reminders the agent could
        # never satisfy, then cost it its tools.
        self.assertEqual(named_targets("les 44 erreurs ruff du dépôt complet sont dans des fichiers"), [])
        self.assertEqual(
            named_targets("The user wants to develop a Three.js scene for RUN/expertise and GIGA/SAILPOI"), []
        )
        self.assertEqual(
            named_targets("tests (text/style, code/test, lint/type/build) for navin/agent/loop.py"),
            ["navin/agent/loop.py"],
        )
        self.assertEqual(named_targets("Rust/Python, HTTP/HTTPS et/ou la version FR"), [])

    def test_branches_still_count_with_their_noun(self) -> None:
        self.assertEqual(named_targets("merge la branche develop dans la branche master"), ["develop", "master"])
        self.assertEqual(named_targets("compare prod et preprod sur staging"), ["preprod", "staging"])


class WorkspaceConfirmationTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "services" / "crm").mkdir(parents=True)
        (self.root / "src" / "ui").mkdir(parents=True)
        (self.root / ".git" / "refs" / "heads").mkdir(parents=True)
        (self.root / ".git" / "refs" / "heads" / "release").write_text("x")
        (self.root / ".git" / "packed-refs").write_text("abc refs/remotes/origin/hotfix\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_bare_word_after_a_noun_must_exist_in_the_project(self) -> None:
        text = "le service crm, la branche release, la branche hotfix et le dépôt complet"
        self.assertEqual(named_targets(text, self.root), ["crm", "release", "hotfix"])
        self.assertEqual(named_targets("le module billing casse", self.root), [])
        # Identifiers are kept as written: the user may name a remote place.
        self.assertEqual(named_targets("le namespace lynara-prod", self.root), ["lynara-prod"])

    def test_a_slash_pair_is_a_path_only_when_the_project_has_it(self) -> None:
        self.assertEqual(named_targets("src/ui et text/style", self.root), ["src/ui"])
        self.assertEqual(named_targets("forgejo/lynara", self.root), ["forgejo/lynara"])

    def test_a_missing_workspace_falls_back_to_the_text_alone(self) -> None:
        self.assertEqual(named_targets("le module billing", self.root / "nope"), ["billing"])


class RuntimeLineTest(unittest.IsolatedAsyncioTestCase):
    def test_lines_name_the_targets_and_the_rule(self) -> None:
        lines = scope_anchor_lines(["forgejo/lynara", "crm"])
        self.assertIn("forgejo/lynara, crm", lines[0])
        self.assertIn("Do not list or read the whole project", lines[1])
        self.assertEqual(scope_anchor_lines([]), [])

    async def test_the_provider_is_silent_without_targets(self) -> None:
        request = SimpleNamespace(original_user_text="explique moi ce projet")
        self.assertIsNone(await scope_anchor_context_provider(request))  # type: ignore[arg-type]

    async def test_the_provider_annotates_a_pointed_request(self) -> None:
        request = SimpleNamespace(original_user_text=LYNARA_BRIEF)
        block = await scope_anchor_context_provider(request)  # type: ignore[arg-type]
        assert block is not None
        self.assertEqual(block.source, "scope_anchor")
        self.assertIn("forgejo/lynara", block.content)

    async def test_the_workspace_scan_runs_off_the_event_loop(self) -> None:
        # 1 to 2 s per turn on the loop for a WSL project (desktop and CLI logs).
        import threading
        from unittest.mock import patch

        loop_thread = threading.get_ident()
        seen: list[int] = []

        def scan(text, root):
            seen.append(threading.get_ident())
            return ["crm"]

        with patch("navin.agent.scope_anchor.named_targets", scan):
            request = SimpleNamespace(original_user_text="crm")
            block = await scope_anchor_context_provider(request)  # type: ignore[arg-type]
        assert block is not None
        self.assertEqual(len(seen), 1)
        self.assertNotEqual(seen[0], loop_thread)


class TouchTest(unittest.TestCase):
    def test_any_argument_string_counts(self) -> None:
        calls = [
            ToolCallRequest(id="1", name="git", arguments={"action": "diff", "args": ["prod..preprod"]}),
        ]
        self.assertTrue(calls_touch_targets(calls, ["forgejo/lynara", "preprod"]))
        self.assertFalse(calls_touch_targets(calls, ["forgejo/lynara", "crm"]))

    def test_matching_ignores_case_and_a_trailing_slash(self) -> None:
        calls = [ToolCallRequest(id="1", name="list_dir", arguments={"path": "services/CRM/celery"})]
        self.assertTrue(calls_touch_targets(calls, ["crm/"]))

    def test_no_targets_means_always_anchored(self) -> None:
        self.assertTrue(calls_touch_targets([], []))

    def test_orientation_batches(self) -> None:
        look = [
            ToolCallRequest(id="1", name="list_dir", arguments={"path": "."}),
            ToolCallRequest(id="2", name="read_file", arguments={"path": "README.md"}),
        ]
        self.assertTrue(is_orientation_batch(look))
        self.assertFalse(is_orientation_batch(look + [ToolCallRequest(id="3", name="exec", arguments={"command": "npm test"})]))
        # Looking through the shell is still looking around.
        self.assertTrue(is_orientation_batch(look + [ToolCallRequest(id="3", name="exec", arguments={"command": "ls"})]))
        self.assertFalse(is_orientation_batch([]))

    def test_the_reminder_names_the_targets(self) -> None:
        message = scope_drift_message(["forgejo/lynara", "crm"])
        self.assertEqual(message["role"], "user")
        self.assertIn("forgejo/lynara, crm", message["content"])


class _FakeProvider:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    async def chat_with_retry(self, **_kwargs: Any) -> LLMResponse:
        idx = min(self.calls, len(self._responses) - 1)
        self.calls += 1
        return self._responses[idx]

    async def chat_stream_with_retry(self, **kwargs: Any) -> LLMResponse:
        return await self.chat_with_retry(**kwargs)


class _LookTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return f"{self._name} ok"


def _look(call_id: str, name: str, path: str) -> LLMResponse:
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id=call_id, name=name, arguments={"path": path})],
    )


def _tour(call_id: str, path: str) -> LLMResponse:
    """A repo-wide look: a recursive listing."""
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(
            id=call_id, name="list_dir", arguments={"path": path, "recursive": True},
        )],
    )


async def _run(
    responses: list[LLMResponse], targets: tuple[str, ...], max_iterations: int = 10,
    request: str = "investigate",
) -> list[dict[str, Any]]:
    provider = _FakeProvider(responses)
    tools = ToolRegistry()
    for name in ("list_dir", "read_file", "grep"):
        tools.register(_LookTool(name))
    result = await AgentRunner().run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": request}],
            tools=tools,
            runtime=LLMRuntime(
                provider=provider,
                model="test-model",
                generation=GenerationSettings(),
                context_window_tokens=128_000,
            ),
            max_iterations=max_iterations,
            max_tool_result_chars=2000,
            hook=AgentHook(),
            scope_targets=targets,
        )
    )
    return result.messages


def _reminders(messages: list[dict[str, Any]]) -> list[str]:
    return [
        str(m.get("content"))
        for m in messages
        if m.get("role") == "user" and str(m.get("content", "")).startswith("Scope reminder")
    ]


class RunnerDriftTest(unittest.TestCase):
    TARGETS = ("forgejo/lynara", "crm", "preprod")

    def test_two_look_around_batches_off_target_get_one_reminder(self) -> None:
        messages = asyncio.run(_run([
            _look("1", "list_dir", "."),
            _look("2", "read_file", "README.md"),
            _look("3", "read_file", "docs/architecture.md"),
            LLMResponse(content="done", finish_reason="stop"),
        ], self.TARGETS))
        reminders = _reminders(messages)
        self.assertEqual(len(reminders), 1)
        self.assertIn("forgejo/lynara, crm, preprod", reminders[0])
        # The reminder lands right after the second off-target batch.
        idx = next(i for i, m in enumerate(messages) if m.get("content") == reminders[0])
        self.assertEqual(messages[idx - 1].get("role"), "tool")
        self.assertIn("README.md", str(messages[idx - 2].get("tool_calls")))

    def test_going_to_a_named_target_first_is_never_interrupted(self) -> None:
        messages = asyncio.run(_run([
            _look("1", "grep", "services/crm/"),
            _look("2", "read_file", "README.md"),
            _look("3", "read_file", "docs/architecture.md"),
            _look("4", "list_dir", "."),
            LLMResponse(content="done", finish_reason="stop"),
        ], self.TARGETS))
        self.assertEqual(_reminders(messages), [])

    def test_a_request_without_targets_is_untouched(self) -> None:
        messages = asyncio.run(_run([
            _look("1", "list_dir", "."),
            _look("2", "read_file", "README.md"),
            _look("3", "read_file", "docs/a.md"),
            LLMResponse(content="done", finish_reason="stop"),
        ], ()))
        self.assertEqual(_reminders(messages), [])

    def test_an_off_target_turn_keeps_its_tools_after_the_reminder(self) -> None:
        # The targets are a guess from the user's prose: missing them earns
        # one reminder, never a forced answer without tools.
        messages = asyncio.run(_run([
            *[_look(str(i), "read_file", f"docs/part{i}.md") for i in range(1, 7)],
            LLMResponse(content="done", finish_reason="stop"),
        ], self.TARGETS))
        self.assertEqual(len(_reminders(messages)), 1)
        texts = [str(m.get("content")) for m in messages if m.get("role") == "user"]
        self.assertFalse(any(t.startswith("Reminder 2") or "Tools are off" in t for t in texts), texts)
        self.assertEqual(messages[-1].get("content"), "done")


if __name__ == "__main__":
    unittest.main()


class ExplorationBudgetTest(unittest.TestCase):
    @staticmethod
    def _checks(messages: list[dict[str, Any]]) -> list[str]:
        return [
            str(m.get("content"))
            for m in messages
            if m.get("role") == "user" and str(m.get("content", "")).startswith("Exploration check")
        ]

    def test_four_look_around_batches_in_a_row_get_one_check(self) -> None:
        looks = [_tour(str(i), f"src/{i}") for i in range(10)]
        messages = asyncio.run(_run([*looks, LLMResponse(content="done", finish_reason="stop")], ()))
        checks = self._checks(messages)
        self.assertEqual(len(checks), 1)
        self.assertIn("last 4 steps", checks[0])

    def test_three_look_around_batches_are_left_alone(self) -> None:
        looks = [_tour(str(i), f"src/{i}") for i in range(3)]
        messages = asyncio.run(_run([*looks, LLMResponse(content="done", finish_reason="stop")], ()))
        self.assertEqual(self._checks(messages), [])

    def test_reads_alone_never_trigger_the_check(self) -> None:
        reads = [_look(str(i), "read_file", f"f{i}.py") for i in range(20)]
        messages = asyncio.run(
            _run([*reads, LLMResponse(content="done", finish_reason="stop")], (), max_iterations=30)
        )
        self.assertEqual(self._checks(messages), [])

    def test_reads_between_searches_do_not_hide_a_search_tour(self) -> None:
        steps = []
        for i in range(8):
            steps.append(_tour(f"s{i}", f"src/{i}"))
            steps.append(_look(f"r{i}", "read_file", f"f{i}.py"))
        messages = asyncio.run(
            _run([*steps, LLMResponse(content="done", finish_reason="stop")], (), max_iterations=30)
        )
        self.assertEqual(len(self._checks(messages)), 1)

    def test_distinct_targeted_lookups_never_trigger_the_check(self) -> None:
        # Scoped greps, precise reads and index lookups, none repeated: this is
        # an agent doing its job, not touring the repo.
        steps = []
        for i in range(10):
            steps.append(_look(f"g{i}", "grep", f"navin/agent/mod{i}.py"))
            steps.append(_look(f"r{i}", "read_file", f"navin/agent/mod{i}.py"))
        messages = asyncio.run(
            _run([*steps, LLMResponse(content="done", finish_reason="stop")], (), max_iterations=30)
        )
        self.assertEqual(self._checks(messages), [])
        self.assertEqual(messages[-1].get("content"), "done")

    def test_repeated_lookups_still_trigger_the_check(self) -> None:
        # Re-running the same two greps over and over is a tour, even scoped.
        steps = [
            _look(str(i), "grep", f"src/{i % 2}") for i in range(8)
        ]
        messages = asyncio.run(
            _run([*steps, LLMResponse(content="done", finish_reason="stop")], (), max_iterations=30)
        )
        self.assertEqual(len(self._checks(messages)), 1)

    def test_audit_request_gets_a_wider_bar(self) -> None:
        tours = [_tour(str(i), f"src/{i}") for i in range(10)]
        done = LLMResponse(content="done", finish_reason="stop")
        six = asyncio.run(_run(
            [*tours[:6], done], (), max_iterations=30, request="Audit du module agent",
        ))
        self.assertEqual(self._checks(six), [])
        ten = asyncio.run(_run(
            [*tours, done], (), max_iterations=30, request="Audit du module agent",
        ))
        checks = self._checks(ten)
        self.assertEqual(len(checks), 1)
        self.assertIn("last 8 steps", checks[0])

    def test_a_non_look_step_resets_the_count(self) -> None:
        looks = [_look(str(i), "grep", f"src/{i}") for i in range(3)]
        act = _look("act", "edit_file", "x.py")
        more = [_look(str(i), "grep", f"lib/{i}") for i in range(3, 6)]
        provider_tools = ("list_dir", "read_file", "grep", "edit_file")
        provider = _FakeProvider([*looks, act, *more, LLMResponse(content="done", finish_reason="stop")])
        tools = ToolRegistry()
        for name in provider_tools:
            tools.register(_LookTool(name))
        result = asyncio.run(AgentRunner().run(AgentRunSpec(
            initial_messages=[{"role": "user", "content": "investigate"}],
            tools=tools,
            runtime=LLMRuntime(provider=provider, model="test-model",
                               generation=GenerationSettings(), context_window_tokens=128_000),
            max_iterations=20, max_tool_result_chars=2000, hook=AgentHook(),
        )))
        self.assertEqual(self._checks(result.messages), [])


class _RecordingProvider(_FakeProvider):
    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(responses)
        self.prompts: list[list[dict[str, Any]]] = []

    async def chat_with_retry(self, **kwargs: Any) -> LLMResponse:
        self.prompts.append(list(kwargs.get("messages") or []))
        return await super().chat_with_retry(**kwargs)


def _run_full(
    responses: list[LLMResponse],
    tool_names: tuple[str, ...] = ("list_dir", "read_file", "grep", "edit_file", "exec"),
    max_iterations: int = 30,
) -> tuple[Any, _RecordingProvider]:
    provider = _RecordingProvider(responses)
    tools = ToolRegistry()
    for name in tool_names:
        tools.register(_LookTool(name))
    result = asyncio.run(AgentRunner().run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "investigate"}],
        tools=tools,
        runtime=LLMRuntime(provider=provider, model="test-model",
                           generation=GenerationSettings(), context_window_tokens=128_000),
        max_iterations=max_iterations, max_tool_result_chars=2000, hook=AgentHook(),
    )))
    return result, provider


def _user_texts(messages: list[dict[str, Any]], prefix: str) -> list[str]:
    return [
        str(m.get("content"))
        for m in messages
        if m.get("role") == "user" and str(m.get("content", "")).startswith(prefix)
    ]


class IgnoredReminderTest(unittest.TestCase):
    """A reminder ignored twice in a row ends the turn with a real answer."""

    def test_two_ignored_reminders_force_the_final_answer(self) -> None:
        searches = [_tour(str(i), f"src/{i}") for i in range(20)]
        final = LLMResponse(content="Voici ce que j'ai trouve.", finish_reason="stop")
        result, provider = _run_full([*searches[:6], final, *searches[6:]])
        self.assertEqual(result.final_content, "Voici ce que j'ai trouve.")
        self.assertEqual(result.stop_reason, "no_progress")
        # 4 searches, the check, 1 ignored (one more reminder), 1 ignored: answer.
        self.assertEqual(provider.calls, 7)
        self.assertEqual(len(_user_texts(result.messages, "Exploration check")), 1)
        self.assertEqual(len(_user_texts(result.messages, "Reminder 2")), 1)
        last_prompt = provider.prompts[-1]
        self.assertIn("Tools are off", str(last_prompt[-1].get("content")))

    def test_acting_on_the_reminder_keeps_the_turn_going(self) -> None:
        searches = [_tour(str(i), f"src/{i}") for i in range(5)]
        act = _look("act", "edit_file", "x.py")
        after = [_look(f"a{i}", "grep", f"lib/{i}") for i in range(3)]
        done = LLMResponse(content="done", finish_reason="stop")
        result, provider = _run_full([*searches, act, *after, done])
        self.assertEqual(result.final_content, "done")
        self.assertEqual(result.stop_reason, "completed")
        self.assertEqual(provider.calls, 10)


class IdenticalActionRunnerTest(unittest.TestCase):
    """The third identical action since the last edit is refused, not run."""

    @staticmethod
    def _tool_results(messages: list[dict[str, Any]]) -> list[str]:
        return [str(m.get("content")) for m in messages if m.get("role") == "tool"]

    def test_third_identical_action_gets_a_reminder_and_the_fifth_ends_the_turn(self) -> None:
        same = [_look(str(i), "exec", "make check") for i in range(5)]
        final = LLMResponse(content="Voici ou on en est.", finish_reason="stop")
        result, provider = _run_full([*same, final])
        results = self._tool_results(result.messages)
        self.assertEqual(results[:2], ["exec ok", "exec ok"])
        self.assertIn("already ran 2 times", results[2])
        self.assertEqual(result.stop_reason, "no_progress")
        self.assertEqual(result.final_content, "Voici ou on en est.")
        self.assertLessEqual(provider.calls, 6)

    def test_an_edit_in_between_allows_the_rerun(self) -> None:
        steps = [
            _look("1", "exec", "make check"),
            _look("2", "exec", "make check"),
            _look("3", "edit_file", "x.py"),
            _look("4", "exec", "make check"),
            LLMResponse(content="done", finish_reason="stop"),
        ]
        result, _ = _run_full(steps)
        self.assertEqual(
            self._tool_results(result.messages),
            ["exec ok", "exec ok", "edit_file ok", "exec ok"],
        )


def _shell(call_id: str, command: str) -> LLMResponse:
    return LLMResponse(
        content="",
        finish_reason="tool_calls",
        tool_calls=[ToolCallRequest(id=call_id, name="exec", arguments={"command": command})],
    )


def _call(name: str, **arguments: Any) -> ToolCallRequest:
    return ToolCallRequest(id="x", name=name, arguments=arguments)


class ReadonlyShellTest(unittest.TestCase):
    """Searching through the shell is still searching."""

    def test_shell_searches_and_reads_are_look_calls(self) -> None:
        for command in (
            "grep -rn handlePermissionError frontend/src/lib --include=*.ts | grep -v test",
            "cd backend && ls finance/app/utils/ocr_helpers/",
            "awk 'NR>=s && NR<=s+700' ocr.sql | grep -n -E \"stamp_duty_amount\"",
            "sed -n '1,80p' app/gemini_prompt.py 2>/dev/null",
            "git log --oneline -5",
            "find . -name '*.py' | head -20",
        ):
            with self.subTest(command=command):
                self.assertTrue(is_readonly_shell_call(_call("exec", command=command)))
                self.assertFalse(is_action_call(_call("exec", command=command)))
                self.assertTrue(is_search_batch([_call("exec", command=command)]))

    def test_commands_that_write_or_run_are_actions(self) -> None:
        for command in (
            "sed -i 's/a/b/' x.py",
            "grep foo x.py > out.txt",
            "python -m pytest tests -q",
            "npm run build",
            "git commit -m wip",
            "find . -name '*.pyc' -delete",
            "cat $(mktemp)",
            "ls && rm -rf build",
        ):
            with self.subTest(command=command):
                self.assertFalse(is_readonly_shell_call(_call("exec", command=command)))
                self.assertTrue(is_action_call(_call("exec", command=command)))

    def test_bookkeeping_and_reads_are_not_actions(self) -> None:
        self.assertFalse(is_action_call(_call("board", action="claim")))
        self.assertFalse(is_action_call(_call("read_file", path="a.py")))
        self.assertTrue(is_action_call(_call("apply_patch", edits=[])))
        self.assertTrue(is_action_call(_call("ask_user", question="?")))

    def test_shell_greps_between_reads_trigger_the_exploration_check(self) -> None:
        steps = []
        for i in range(8):
            steps.append(_shell(f"s{i}", f"grep -rn token{i} src | head"))
            steps.append(_look(f"r{i}", "read_file", f"f{i}.py"))
        result, _ = _run_full([*steps, LLMResponse(content="done", finish_reason="stop")])
        self.assertEqual(len(_user_texts(result.messages, "Exploration check")), 1)


def _run_build(
    responses: list[LLMResponse], *, build: bool = True,
) -> tuple[Any, _RecordingProvider]:
    provider = _RecordingProvider(responses)
    tools = ToolRegistry()
    for name in ("read_file", "grep", "edit_file", "exec"):
        tools.register(_LookTool(name))
    result = asyncio.run(AgentRunner().run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "ok vas y"}],
        tools=tools,
        runtime=LLMRuntime(provider=provider, model="test-model",
                           generation=GenerationSettings(), context_window_tokens=128_000),
        max_iterations=20, max_tool_result_chars=2000, hook=AgentHook(),
        requires_verify_before_done=build,
    )))
    return result, provider


class BuildTurnFinishesTheWorkTest(unittest.TestCase):
    """A build turn may not end on 'nothing changed yet' or on a to-do list."""

    STALLED = (
        "Je dois etre franc : aucun fichier n'a encore ete modifie. "
        "Ce qui reste a ecrire : le prompt, le controle, les tests."
    )

    def _continues(self, messages: list[dict[str, Any]], prompt: str) -> int:
        return sum(1 for m in messages if m.get("role") == "user" and m.get("content") == prompt)

    def test_reads_then_a_promise_are_sent_back_into_the_work_once(self) -> None:
        from navin.utils.runtime import NO_ACTION_CONTINUE_PROMPT

        result, provider = _run_build([
            _look("1", "read_file", "gemini_prompt.py"),
            _look("2", "grep", "src"),
            LLMResponse(content=self.STALLED, finish_reason="stop"),
            _shell("3", "python scripts/apply_prompt.py"),
            LLMResponse(content="Prompt modifie.", finish_reason="stop"),
        ])
        self.assertEqual(self._continues(result.messages, NO_ACTION_CONTINUE_PROMPT), 1)
        self.assertIn("exec", result.tools_used)
        self.assertEqual(result.final_content, "Prompt modifie.")
        self.assertEqual(provider.calls, 5)

    def test_the_reminder_is_sent_only_once(self) -> None:
        from navin.utils.runtime import NO_ACTION_CONTINUE_PROMPT

        result, provider = _run_build([
            _look("1", "read_file", "a.py"),
            LLMResponse(content=self.STALLED, finish_reason="stop"),
            LLMResponse(content=self.STALLED, finish_reason="stop"),
        ])
        self.assertEqual(self._continues(result.messages, NO_ACTION_CONTINUE_PROMPT), 1)
        self.assertEqual(result.final_content, self.STALLED)
        self.assertEqual(provider.calls, 3)

    def test_a_plain_answer_after_reads_is_left_alone(self) -> None:
        result, provider = _run_build([
            _look("1", "read_file", "a.py"),
            LLMResponse(content="La fonction renvoie un 422.", finish_reason="stop"),
        ])
        self.assertEqual(result.final_content, "La fonction renvoie un 422.")
        self.assertEqual(provider.calls, 2)

    def test_outside_build_turns_nothing_is_forced(self) -> None:
        result, provider = _run_build([
            _look("1", "read_file", "a.py"),
            LLMResponse(content=self.STALLED, finish_reason="stop"),
        ], build=False)
        self.assertEqual(result.final_content, self.STALLED)
        self.assertEqual(provider.calls, 2)

    def test_stopping_halfway_continues_while_each_reminder_is_acted_on(self) -> None:
        from navin.utils.runtime import REMAINING_WORK_CONTINUE_PROMPT

        halfway = "Etape 1 faite. Ce qui reste a faire : les traductions fr/en/ar."
        result, provider = _run_build([
            _shell("1", "python scripts/gen_prompt.py"),
            LLMResponse(content=halfway, finish_reason="stop"),
            # The reminder was acted on (a command ran): a new reminder.
            _shell("2", "python scripts/gen_i18n.py"),
            LLMResponse(content=halfway, finish_reason="stop"),
            # Nothing done since the last reminder: one firmer push, then the
            # turn ends (the fake keeps answering the same text).
            LLMResponse(content=halfway, finish_reason="stop"),
        ])
        self.assertEqual(self._continues(result.messages, REMAINING_WORK_CONTINUE_PROMPT), 2)
        self.assertEqual(provider.calls, 6)
        self.assertEqual(result.final_content, halfway)

    # The two sentences a /forge session ended on (2026-10-29) after a git
    # status, a listing, the graph, the index, two reads and the board: the
    # user saw neither the plan nor a line of code.
    ANNOUNCED_FR = (
        "Je démarre pour de vrai : cartographie de l'existant navin/security/, "
        "plan au board, puis code dans ce tour."
    )
    ANNOUNCED_EN = (
        "The board plan from the cut turn was never filed. I file it now and "
        "build the first step (the socle) in this turn."
    )

    def test_reads_then_an_announcement_of_the_work_are_sent_back_into_it(self) -> None:
        from navin.utils.runtime import NO_ACTION_CONTINUE_PROMPT

        for announced in (self.ANNOUNCED_FR, self.ANNOUNCED_EN):
            with self.subTest(text=announced):
                result, provider = _run_build([
                    _look("1", "read_file", "navin/security/model.py"),
                    _look("2", "grep", "Finding"),
                    LLMResponse(content=announced, finish_reason="stop"),
                    _shell("3", "python scripts/scaffold_security.py"),
                    LLMResponse(content="Socle ecrit et teste.", finish_reason="stop"),
                ])
                self.assertEqual(self._continues(result.messages, NO_ACTION_CONTINUE_PROMPT), 1)
                self.assertIn("exec", result.tools_used)
                self.assertEqual(result.final_content, "Socle ecrit et teste.")
                self.assertEqual(provider.calls, 5)

    def test_an_announcement_after_real_work_is_one_more_reminder(self) -> None:
        from navin.utils.runtime import REMAINING_WORK_CONTINUE_PROMPT

        result, provider = _run_build([
            _shell("1", "python scripts/file_plan.py"),
            LLMResponse(content="Plan au board. " + self.ANNOUNCED_EN, finish_reason="stop"),
            _shell("2", "python scripts/scaffold_security.py"),
            LLMResponse(content="Plan au board, socle ecrit et teste.", finish_reason="stop"),
        ])
        self.assertEqual(self._continues(result.messages, REMAINING_WORK_CONTINUE_PROMPT), 1)
        self.assertEqual(result.final_content, "Plan au board, socle ecrit et teste.")
        self.assertEqual(provider.calls, 4)


class RemainingWorkMarkersTest(unittest.TestCase):
    def test_markers(self) -> None:
        from navin.utils.runtime import lists_remaining_work

        for text in (
            "Ce qui reste a ecrire (inchange)",
            "Au prochain message, je passe a l'ecriture.",
            "Aucun fichier n'a encore ete modifie.",
            "Remaining steps: tests and i18n.",
            "No files have been changed yet.",
        ):
            with self.subTest(text=text):
                self.assertTrue(lists_remaining_work(text))
        for text in ("Tout est fait, les tests passent.", "", None):
            with self.subTest(text=text):
                self.assertFalse(lists_remaining_work(text))

    def test_an_announcement_of_imminent_work_is_unfinished_work(self) -> None:
        from navin.utils.runtime import announces_work, lists_remaining_work

        for text in (
            BuildTurnFinishesTheWorkTest.ANNOUNCED_FR,
            BuildTurnFinishesTheWorkTest.ANNOUNCED_EN,
            "Je vais maintenant creer les fichiers du module.",
            "J'attaque le routeur de skills.",
            "Let me start with the orchestrator.",
            "I'll write the registry and its tests.",
            "I'm now wiring the panel into the rail.",
            "Now I create the scanner adapters.",
            "Starting with the model.",
        ):
            with self.subTest(text=text):
                self.assertTrue(announces_work(text))
                self.assertTrue(lists_remaining_work(text))
        for text in (
            # Done, in the past: not a promise.
            "Tout a ete fait dans ce tour : plan, socle, tests verts.",
            "Le serveur a commence a repondre, tout est en place.",
            # A refusal, an offer left to the user, a question, a user-owned step.
            "Je ne vais pas toucher au module billing sans relecture.",
            "I will not change the public API.",
            "Si tu veux, je lance le build complet ensuite.",
            "If you want, I will add the PDF export too.",
            "Shall I start with the mobile skill now?",
            "Tu peux maintenant lancer npm run build de ton cote.",
            "I'll push to Forgejo once you confirm.",
            # The phrase quoted as a phrase.
            'The reminder fires on "I file it now" sentences.',
            "",
            None,
        ):
            with self.subTest(text=text):
                self.assertFalse(announces_work(text))
                self.assertFalse(lists_remaining_work(text))
