# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Delivery studio workflows must preload skills and force tool execution.

Thinking models otherwise plan a whole deck in reasoning, announce upcoming
visuals in text, and end the turn with zero tool calls.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from navin.agent.context import ContextBuilder
from navin.agent.hook import AgentHook
from navin.agent.runner import AgentRunner, AgentRunSpec
from navin.agent.tools.base import Tool
from navin.agent.tools.registry import ToolRegistry
from navin.command.builtin import (
    _CODE_BUILD_WORKFLOWS,
    _CODE_STRICT_LOOP_CLAUSE,
    _CODE_VERIFY_CLAUSE,
    _CODE_VERIFY_WORKFLOWS,
    _COMPOSER_MODE_BY_COMMAND,
    _DEBUG_REPRO_VERIFY_CLAUSE,
    _DELIVERY_RUN_CLAUSE,
    _DELIVERY_WORKFLOWS,
    _EVIDENCE_ONLY_CLAUSE,
    _EXPERT_REPORT_CLAUSE,
    _EXPERT_REPORT_WORKFLOWS,
    _HTML_REPORT_CLAUSE,
    _HTML_REPORT_WORKFLOWS,
    _MONTAGE_TOOLS_CLAUSE,
    _MONTAGE_UNCLEAR_FOCUS_CLAUSE,
    _SCOPED_FANOUT_CLAUSE,
    _UNCLEAR_FOCUS_CLAUSE,
    _WORKFLOW_BRIEFS,
    _workflow_handler,
    _workflow_skill_names,
    focus_is_plain_question,
)
from navin.command.modules import (
    APPLY_PATCH_ONLY_METADATA_KEY,
    EVIDENCE_ONLY_METADATA_KEY,
    PRELOAD_SKILLS_METADATA_KEY,
    REQUIRES_TOOL_DELIVERY_METADATA_KEY,
    REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY,
    SLIM_SKILL_PRELOAD_METADATA_KEY,
)
from navin.providers.base import GenerationSettings, LLMResponse, ToolCallRequest
from navin.utils.llm_runtime import LLMRuntime
from navin.utils.runtime import DELIVERY_CONTINUE_PROMPT, VERIFY_BEFORE_DONE_CONTINUE_PROMPT


class DeliveryBriefTest(unittest.TestCase):
    def _brief(self, command: str) -> tuple[str, dict]:
        msg = SimpleNamespace(
            content="",
            metadata={},
            channel="cli",
            chat_id="test",
        )
        ctx = SimpleNamespace(
            args="pitch deck PPTX",
            raw=f"{command} pitch deck",
            msg=msg,
            loop=None,
        )
        asyncio.run(_workflow_handler(command)(ctx))  # type: ignore[arg-type]
        return msg.content, dict(msg.metadata)

    def test_every_delivery_command_carries_the_clause(self) -> None:
        for command in sorted(_DELIVERY_WORKFLOWS):
            content, _meta = self._brief(command)
            self.assertIn(_DELIVERY_RUN_CLAUSE, content, command)

    def test_delivery_commands_all_exist(self) -> None:
        self.assertEqual(_DELIVERY_WORKFLOWS - set(_WORKFLOW_BRIEFS), set())

    def test_studio_preloads_brief_skills_and_requires_delivery(self) -> None:
        content, meta = self._brief("/studio")
        expected = _workflow_skill_names(_WORKFLOW_BRIEFS["/studio"][1])
        self.assertEqual(meta.get(PRELOAD_SKILLS_METADATA_KEY), expected)
        self.assertTrue(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY))
        self.assertIn("preloaded into Active Skills", content)
        self.assertNotIn("Load and follow these skills if available", content)

    def test_plan_only_modes_stay_out_of_delivery_nudge(self) -> None:
        for command in ("/blueprint", "/board", "/report"):
            _content, meta = self._brief(command)
            self.assertFalse(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY), command)
            # Still preload named skills for every workflow brief.
            self.assertTrue(meta.get(PRELOAD_SKILLS_METADATA_KEY), command)

    def test_cruise_and_mission_are_delivery_workflows_in_agent_mode(self) -> None:
        self.assertIn("/cruise", _WORKFLOW_BRIEFS)
        self.assertIn("/mission", _WORKFLOW_BRIEFS)
        self.assertIn("/cruise", _DELIVERY_WORKFLOWS)
        self.assertEqual(_COMPOSER_MODE_BY_COMMAND["/cruise"], "agent")
        self.assertEqual(_COMPOSER_MODE_BY_COMMAND["/mission"], "agent")
        self.assertIn("/mission", _WORKFLOW_BRIEFS)
        self.assertIn("mission-ledger", _WORKFLOW_BRIEFS["/cruise"][1])
        self.assertIn("mission-ledger", _WORKFLOW_BRIEFS["/mission"][1])

    def test_forge_requires_delivery_and_verify_before_done(self) -> None:
        self.assertTrue(_CODE_BUILD_WORKFLOWS <= _DELIVERY_WORKFLOWS)
        for command in sorted(_CODE_BUILD_WORKFLOWS):
            content, meta = self._brief(command)
            self.assertIn(_DELIVERY_RUN_CLAUSE, content, command)
            self.assertIn(_CODE_VERIFY_CLAUSE, content, command)
            self.assertIn(_CODE_STRICT_LOOP_CLAUSE, content, command)
            self.assertTrue(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY), command)
            self.assertTrue(meta.get(REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY), command)
            # Prompt preference only - runner no longer hard-blocks write_file.
            self.assertFalse(meta.get(APPLY_PATCH_ONLY_METADATA_KEY), command)

    def _brief_with_focus(self, command: str, focus: str) -> tuple[str, dict]:
        msg = SimpleNamespace(content="", metadata={}, channel="cli", chat_id="test")
        ctx = SimpleNamespace(args=focus, raw=f"{command} {focus}", msg=msg, loop=None)
        asyncio.run(_workflow_handler(command)(ctx))  # type: ignore[arg-type]
        return msg.content, dict(msg.metadata)

    def test_a_plain_question_in_agent_mode_is_not_forced_into_tools(self) -> None:
        """"Quelle option de ls ... ?" in Agent used to cost a second model
        round-trip (delivery nudge) and a paragraph about "no deliverable"."""
        for focus in (
            "Quelle option de ls affiche les fichiers caches ? Reponds en une phrase.",
            "Comment fonctionne le flux d'authentification dans ce projet",
            "How does the retry ladder pick its delays?",
            "Explique la difference entre apply_patch et edit_file",
        ):
            for command in sorted(_CODE_BUILD_WORKFLOWS):
                content, meta = self._brief_with_focus(command, focus)
                self.assertFalse(
                    meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY), (command, focus)
                )
                self.assertNotIn(_DELIVERY_RUN_CLAUSE, content, (command, focus))
                # Still a full Agent turn: the brief and its verify rules stay.
                self.assertIn(_CODE_VERIFY_CLAUSE, content, (command, focus))

    def test_a_question_that_asks_for_work_keeps_the_delivery_nudge(self) -> None:
        for focus in (
            "Peux-tu creer un composant Button dans src/ui ?",
            "Can you fix the failing login test?",
            "pourquoi le build casse ? corrige le",
        ):
            self.assertFalse(focus_is_plain_question(focus), focus)
            _content, meta = self._brief_with_focus("/forge", focus)
            self.assertTrue(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY), focus)

    def test_plain_question_detector(self) -> None:
        self.assertTrue(focus_is_plain_question("c'est quoi un webhook"))
        self.assertTrue(focus_is_plain_question("What does the sandbox allow"))
        self.assertTrue(focus_is_plain_question("site web pour restaurant ?"))
        self.assertFalse(focus_is_plain_question("site web pour restaurant"))
        self.assertFalse(focus_is_plain_question("ajoute un mode sombre"))
        self.assertFalse(focus_is_plain_question(""))

    def test_forge_skills_are_on_demand_not_preloaded_bodies(self) -> None:
        content, _meta = self._brief("/forge")
        self.assertIn("on demand, one per phase", content)
        self.assertIn("skill action=read", content)
        self.assertNotIn("preloaded into Active Skills", content)

    def test_code_builds_allow_only_code_tools(self) -> None:
        """Regression: /forge once opened by piping the user's landing-page
        brief into generate_video. Build turns publish an allowlist so a new
        desk tool cannot sneak into the schema the way a denylist missed it.
        Image generation stays: web assets are a legitimate build need."""
        from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS
        from navin.command.modules import ALLOWED_TOOLS_METADATA_KEY

        for command in sorted(_CODE_BUILD_WORKFLOWS):
            _content, meta = self._brief(command)
            allowed = set(meta.get(ALLOWED_TOOLS_METADATA_KEY) or [])
            self.assertEqual(allowed, set(CODE_BUILD_ALLOWED_TOOLS), command)
            self.assertIn("generate_image", allowed, command)
            self.assertIn("read_file", allowed, command)
            self.assertIn("verify", allowed, command)
            self.assertNotIn("generate_video", allowed, command)
            self.assertNotIn("montage", allowed, command)
            self.assertNotIn("scrape", allowed, command)
            self.assertNotIn("tenders", allowed, command)
            self.assertNotIn("career", allowed, command)
            self.assertNotIn("denied_tools", meta, command)

    def test_debug_requires_verify_and_repro_loop_clause(self) -> None:
        self.assertTrue(_CODE_BUILD_WORKFLOWS <= _CODE_VERIFY_WORKFLOWS)
        self.assertIn("/debug", _CODE_VERIFY_WORKFLOWS)
        content, meta = self._brief("/debug")
        self.assertIn(_DEBUG_REPRO_VERIFY_CLAUSE, content)
        self.assertIn(_CODE_STRICT_LOOP_CLAUSE, content)
        self.assertTrue(meta.get(REQUIRES_VERIFY_BEFORE_DONE_METADATA_KEY))
        self.assertNotIn(_CODE_VERIFY_CLAUSE, content)

    def test_html_report_studios_preload_skill_and_clause(self) -> None:
        for command in sorted(_HTML_REPORT_WORKFLOWS):
            content, meta = self._brief(command)
            self.assertIn(_HTML_REPORT_CLAUSE, content, command)
            skills = meta.get(PRELOAD_SKILLS_METADATA_KEY) or []
            self.assertIn("studio-html-report", skills, command)
            if command in _EXPERT_REPORT_WORKFLOWS:
                continue
            self.assertIn("ui-ux-pro-max", skills, command)
            self.assertIn("make-interfaces-feel-better", skills, command)
            self.assertIn("open_preview", _HTML_REPORT_CLAUSE)
            self.assertIn("@react-three/fiber", _HTML_REPORT_CLAUSE)
            self.assertIn("Export PDF", _HTML_REPORT_CLAUSE)

    def test_expert_modes_require_real_examples_and_choice_plan(self) -> None:
        self.assertTrue(_EXPERT_REPORT_WORKFLOWS <= _HTML_REPORT_WORKFLOWS)
        self.assertTrue(_EXPERT_REPORT_WORKFLOWS <= _DELIVERY_WORKFLOWS)
        for command in sorted(_EXPERT_REPORT_WORKFLOWS):
            content, meta = self._brief(command)
            self.assertIn(_EXPERT_REPORT_CLAUSE, content, command)
            self.assertIn(_EVIDENCE_ONLY_CLAUSE, content, command)
            self.assertIn(_SCOPED_FANOUT_CLAUSE, content, command)
            self.assertIn(_HTML_REPORT_CLAUSE, content, command)
            self.assertTrue(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY), command)
            self.assertTrue(meta.get(EVIDENCE_ONLY_METADATA_KEY), command)
            self.assertTrue(meta.get(SLIM_SKILL_PRELOAD_METADATA_KEY), command)
            skills = meta.get(PRELOAD_SKILLS_METADATA_KEY) or []
            self.assertIn("studio-html-report", skills, command)

    def test_evidence_only_injected_into_system_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
            builder = ContextBuilder(root)
            prompt = builder.build_system_prompt(evidence_only=True)
            self.assertIn("Evidence-only mode", prompt)
            self.assertIn("Never invent findings", prompt)
            plain = builder.build_system_prompt(evidence_only=False)
            self.assertNotIn("Evidence-only mode", plain)

    def test_non_html_delivery_skips_html_report_clause(self) -> None:
        content, meta = self._brief("/ops")
        self.assertNotIn(_HTML_REPORT_CLAUSE, content)
        self.assertNotIn(_EXPERT_REPORT_CLAUSE, content)
        skills = meta.get(PRELOAD_SKILLS_METADATA_KEY) or []
        self.assertNotIn("studio-html-report", skills)

    def _greeting_brief(self, command: str, greeting: str) -> tuple[str, dict]:
        msg = SimpleNamespace(
            content="",
            metadata={},
            channel="cli",
            chat_id="test",
        )
        ctx = SimpleNamespace(
            args=greeting,
            raw=f"{command} {greeting}",
            msg=msg,
            loop=None,
        )
        asyncio.run(_workflow_handler(command)(ctx))  # type: ignore[arg-type]
        return msg.content, dict(msg.metadata)

    def test_greeting_never_sees_the_mission_pipeline(self) -> None:
        # "/montage hi" must not trigger analyze / file writes: no brief, no
        # delivery clause, no preloaded mission skills - just the intent gate.
        for command in ("/montage", "/campaign", "/seo", "/leads", "/ops"):
            for greeting in ("hi", "salut", "hello", ""):
                content, meta = self._greeting_brief(command, greeting)
                brief = _WORKFLOW_BRIEFS[command][2]
                self.assertNotIn(brief, content, f"{command} {greeting!r}")
                self.assertNotIn(_DELIVERY_RUN_CLAUSE, content, f"{command} {greeting!r}")
                self.assertIn("INTENT GATE", content, f"{command} {greeting!r}")
                self.assertFalse(
                    meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY),
                    f"{command} {greeting!r}",
                )
                self.assertEqual(
                    meta.get(PRELOAD_SKILLS_METADATA_KEY),
                    [],
                    f"{command} {greeting!r}",
                )
                if command == "/montage":
                    self.assertIn(_MONTAGE_UNCLEAR_FOCUS_CLAUSE, content)
                    self.assertNotIn(_UNCLEAR_FOCUS_CLAUSE, content)
                    self.assertIn("Project linkage:", content)

    def test_montage_actionable_brief_forces_tools_and_linked_project(self) -> None:
        msg = SimpleNamespace(
            content="",
            metadata={
                "workspace_scope": {
                    "project_path": "/tmp/demo-app",
                    "project_name": "demo-app",
                    "access_mode": "project",
                }
            },
            channel="websocket",
            chat_id="test",
        )
        ctx = SimpleNamespace(
            args="Record a live product demo of the linked app",
            raw="/montage Record a live product demo of the linked app",
            msg=msg,
            loop=None,
        )
        asyncio.run(_workflow_handler("/montage")(ctx))  # type: ignore[arg-type]
        self.assertIn(_MONTAGE_TOOLS_CLAUSE, msg.content)
        self.assertIn("Project linkage: linked (demo-app", msg.content)
        self.assertIn("TOOLS ARE REGISTERED THIS TURN", msg.content)
        self.assertIn("record_start", msg.content)
        self.assertNotIn(_MONTAGE_UNCLEAR_FOCUS_CLAUSE, msg.content)


class SkillPreloadTest(unittest.TestCase):
    def test_a_workflow_skill_is_named_not_dumped(self) -> None:
        """A brief's skill is suggested by name; its body loads on demand.

        Injecting bodies cost ~9k prompt tokens on every step of every turn,
        and a 57k prompt is ~2.3s slower per call than a small one even at a
        100% cache hit.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "pptx-generator"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: pptx-generator\ndescription: Build decks\n---\n\n"
                "UNIQUE_PPTX_BODY_MARKER\n",
                encoding="utf-8",
            )
            builder = ContextBuilder(root)
            prompt = builder.build_system_prompt(
                skill_names=["pptx-generator"],
                include_memory_recent_history=False,
            )
            self.assertIn("# Active Skills", prompt)
            self.assertIn("pptx-generator", prompt)
            self.assertIn("skill action=read", prompt)
            self.assertNotIn("UNIQUE_PPTX_BODY_MARKER", prompt)

    def test_a_mentioned_skill_still_loads_its_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "pptx-generator"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: pptx-generator\ndescription: Build decks\n---\n\n"
                "UNIQUE_PPTX_BODY_MARKER\n",
                encoding="utf-8",
            )
            builder = ContextBuilder(root)
            prompt = builder.build_system_prompt(
                include_memory_recent_history=False,
                current_message="use $pptx-generator for this deck",
            )
            self.assertIn("UNIQUE_PPTX_BODY_MARKER", prompt)


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


class _ReadFileTool(Tool):
    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a file"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return f"skill body for {kwargs.get('path', '')}"


def _runtime(provider: Any) -> LLMRuntime:
    return LLMRuntime(
        provider=provider,
        model="test-model",
        generation=GenerationSettings(),
        context_window_tokens=128_000,
    )


class DeliveryNudgeRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_zero_tool_final_answer_is_nudged_once(self) -> None:
        provider = _FakeProvider(
            [
                LLMResponse(
                    content="Je vais générer les visuels du pitch deck.",
                    finish_reason="stop",
                ),
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="call_1",
                            name="read_file",
                            arguments={"path": "skills/pptx-generator/SKILL.md"},
                        )
                    ],
                ),
                LLMResponse(
                    content="Deck prêt: projects/navin-pitch/deck.pptx",
                    finish_reason="stop",
                ),
            ]
        )
        tools = ToolRegistry()
        tools.register(_ReadFileTool())
        runner = AgentRunner()
        result = await runner.run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "fais un pitch"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=5,
                max_tool_result_chars=4000,
                hook=AgentHook(),
                requires_tool_delivery=True,
            )
        )
        self.assertGreaterEqual(provider.calls, 2)
        self.assertIn("read_file", result.tools_used)
        nudge_messages = [
            m for m in result.messages
            if m.get("role") == "user" and DELIVERY_CONTINUE_PROMPT in str(m.get("content"))
        ]
        self.assertEqual(len(nudge_messages), 1)

    async def test_non_delivery_turn_is_not_nudged(self) -> None:
        provider = _FakeProvider(
            [
                LLMResponse(content="Voici mon avis.", finish_reason="stop"),
            ]
        )
        runner = AgentRunner()
        result = await runner.run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "question"}],
                tools=ToolRegistry(),
                runtime=_runtime(provider),
                max_iterations=3,
                max_tool_result_chars=4000,
                hook=AgentHook(),
                requires_tool_delivery=False,
            )
        )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(result.tools_used, [])
        self.assertEqual(result.final_content, "Voici mon avis.")

    async def test_build_turn_without_verify_is_nudged_once(self) -> None:
        provider = _FakeProvider(
            [
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="call_1",
                            name="apply_patch",
                            arguments={"path": "app.py", "patch": "x"},
                        )
                    ],
                ),
                LLMResponse(
                    content="Fix applied, we are done.",
                    finish_reason="stop",
                ),
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="call_2",
                            name="verify",
                            arguments={"action": "check"},
                        )
                    ],
                ),
                LLMResponse(
                    content="Verified. Done.",
                    finish_reason="stop",
                ),
            ]
        )
        tools = ToolRegistry()
        tools.register(_ApplyPatchTool())
        tools.register(_VerifyTool())
        runner = AgentRunner()
        result = await runner.run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "fix the bug"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=6,
                max_tool_result_chars=4000,
                hook=AgentHook(),
                requires_tool_delivery=True,
                requires_verify_before_done=True,
            )
        )
        self.assertIn("apply_patch", result.tools_used)
        self.assertIn("verify", result.tools_used)
        nudge_messages = [
            m for m in result.messages
            if m.get("role") == "user"
            and VERIFY_BEFORE_DONE_CONTINUE_PROMPT in str(m.get("content"))
        ]
        self.assertEqual(len(nudge_messages), 1)

    async def test_read_only_tools_do_not_trigger_verify_nudge(self) -> None:
        provider = _FakeProvider(
            [
                LLMResponse(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=[
                        ToolCallRequest(
                            id="call_1",
                            name="read_file",
                            arguments={"path": "app.py"},
                        )
                    ],
                ),
                LLMResponse(
                    content="Here is what I found.",
                    finish_reason="stop",
                ),
            ]
        )
        tools = ToolRegistry()
        tools.register(_ReadFileTool())
        result = await AgentRunner().run(
            AgentRunSpec(
                initial_messages=[{"role": "user", "content": "explain"}],
                tools=tools,
                runtime=_runtime(provider),
                max_iterations=4,
                max_tool_result_chars=4000,
                hook=AgentHook(),
                requires_verify_before_done=True,
            )
        )
        self.assertIn("read_file", result.tools_used)
        nudge_messages = [
            m for m in result.messages
            if m.get("role") == "user"
            and VERIFY_BEFORE_DONE_CONTINUE_PROMPT in str(m.get("content"))
        ]
        self.assertEqual(len(nudge_messages), 0)


class _ApplyPatchTool(Tool):
    @property
    def name(self) -> str:
        return "apply_patch"

    @property
    def description(self) -> str:
        return "patch"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "patched"


class _VerifyTool(Tool):
    @property
    def name(self) -> str:
        return "verify"

    @property
    def description(self) -> str:
        return "verify"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        return "verify ok"


if __name__ == "__main__":
    unittest.main()
