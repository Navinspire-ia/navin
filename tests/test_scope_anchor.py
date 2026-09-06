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
from types import SimpleNamespace
from typing import Any

from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.scope_anchor import (
    MAX_TARGETS,
    calls_touch_targets,
    is_orientation_batch,
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
        self.assertFalse(is_orientation_batch(look + [ToolCallRequest(id="3", name="exec", arguments={"command": "ls"})]))
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


async def _run(responses: list[LLMResponse], targets: tuple[str, ...]) -> list[dict[str, Any]]:
    provider = _FakeProvider(responses)
    tools = ToolRegistry()
    for name in ("list_dir", "read_file", "grep"):
        tools.register(_LookTool(name))
    result = await AgentRunner().run(
        AgentRunSpec(
            initial_messages=[{"role": "user", "content": "investigate"}],
            tools=tools,
            runtime=LLMRuntime(
                provider=provider,
                model="test-model",
                generation=GenerationSettings(),
                context_window_tokens=128_000,
            ),
            max_iterations=10,
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


if __name__ == "__main__":
    unittest.main()
