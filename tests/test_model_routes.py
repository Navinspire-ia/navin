"""Task routing: workflow slash commands → model_routes presets."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from navin.agent.loop import AgentLoop
from navin.agent.model_routes import (
    COMPOSER_MODE_ROUTE_ROLES,
    WORKFLOW_ROUTE_ROLES,
    composer_mode_role,
    infer_task_role,
    resolve_model_route,
    resolve_route_for_message,
    workflow_role_for_content,
)
from navin.bus.events import INBOUND_META_MODEL_PRESET, InboundMessage


class WorkflowRoleMappingTests(unittest.TestCase):
    def test_core_workflows_map_to_expected_roles(self) -> None:
        self.assertEqual(workflow_role_for_content("/blueprint ship it"), "plan")
        self.assertEqual(workflow_role_for_content("/forge Implement"), "dev")
        self.assertEqual(workflow_role_for_content("/cruise ship it"), "dev")
        self.assertEqual(workflow_role_for_content("/mission long goal"), "deep")
        self.assertEqual(workflow_role_for_content("/inspect Review auth"), "review")
        self.assertEqual(workflow_role_for_content("/fortify"), "security")
        self.assertEqual(workflow_role_for_content("/debug Fix crash"), "deep")
        self.assertEqual(workflow_role_for_content("/risklens"), "deep")
        self.assertEqual(workflow_role_for_content("/scrape https://x"), "search")
        self.assertEqual(workflow_role_for_content("/seo audit"), "docs")
        self.assertEqual(workflow_role_for_content("/career Find missions"), "search")
        self.assertEqual(WORKFLOW_ROUTE_ROLES["/career"], "search")

    def test_plain_chat_has_no_role(self) -> None:
        self.assertIsNone(workflow_role_for_content("hello there"))
        self.assertIsNone(workflow_role_for_content("/unknown-command"))

    def test_every_mapped_command_has_a_role(self) -> None:
        for command, role in WORKFLOW_ROUTE_ROLES.items():
            self.assertTrue(command.startswith("/"), command)
            self.assertTrue(role, command)


class ComposerModeRoleTests(unittest.TestCase):
    """P2-4: bare composer_mode metadata maps onto task-routing roles."""

    def test_modes_map_to_their_slash_equivalents(self) -> None:
        self.assertEqual(composer_mode_role("plan"), "plan")
        self.assertEqual(composer_mode_role("review"), "review")
        self.assertEqual(composer_mode_role("security"), "security")
        self.assertEqual(composer_mode_role("debug"), "deep")

    def test_agent_and_ask_modes_do_not_route(self) -> None:
        self.assertIsNone(composer_mode_role("agent"))
        self.assertIsNone(composer_mode_role("ask"))
        self.assertIsNone(composer_mode_role(None))
        self.assertIsNone(composer_mode_role(""))

    def test_mode_names_are_normalized(self) -> None:
        self.assertEqual(composer_mode_role("  PLAN  "), "plan")

    def test_mapping_stays_aligned_with_the_slash_commands(self) -> None:
        self.assertEqual(
            COMPOSER_MODE_ROUTE_ROLES["plan"], WORKFLOW_ROUTE_ROLES["/blueprint"]
        )
        self.assertEqual(
            COMPOSER_MODE_ROUTE_ROLES["review"], WORKFLOW_ROUTE_ROLES["/inspect"]
        )
        self.assertEqual(
            COMPOSER_MODE_ROUTE_ROLES["security"], WORKFLOW_ROUTE_ROLES["/fortify"]
        )
        self.assertEqual(
            COMPOSER_MODE_ROUTE_ROLES["debug"], WORKFLOW_ROUTE_ROLES["/debug"]
        )


class ResolveRouteTests(unittest.TestCase):
    def test_resolve_model_route_returns_configured_preset(self) -> None:
        routes = {"dev": "sonnet", "plan": "default", "fast": ""}
        self.assertEqual(resolve_model_route("dev", routes=routes), "sonnet")
        self.assertEqual(resolve_model_route("plan", routes=routes), "default")
        self.assertIsNone(resolve_model_route("fast", routes=routes))
        self.assertIsNone(resolve_model_route("missing", routes=routes))

    def test_unknown_preset_rejected_when_allowlist_given(self) -> None:
        routes = {"dev": "gone"}
        self.assertIsNone(
            resolve_model_route("dev", routes=routes, known_presets={"sonnet"})
        )
        self.assertEqual(
            resolve_model_route("dev", routes=routes, known_presets={"gone"}),
            "gone",
        )

    def test_resolve_route_for_message(self) -> None:
        routes = {"dev": "sonnet", "plan": "opus"}
        self.assertEqual(
            resolve_route_for_message("/forge do it", routes=routes),
            "sonnet",
        )
        self.assertEqual(
            resolve_route_for_message("/blueprint", routes=routes),
            "opus",
        )
        self.assertIsNone(resolve_route_for_message("no command", routes=routes))


class RuntimeForInboundRoutingTests(unittest.TestCase):
    def test_thread_model_preset_wins_over_workflow_route(self) -> None:
        """An explicit picker pin is a user choice; task routing must not override it."""
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        pinned = MagicMock()
        pinned.model_preset = "ui-pick"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"sonnet": object(), "ui-pick": object()}
        loop.runtime_resolver.resolve_preset.return_value = pinned

        msg = InboundMessage(
            channel="websocket",
            sender_id="u",
            chat_id="c",
            content="/forge Implement the plan",
            metadata={INBOUND_META_MODEL_PRESET: "ui-pick"},
        )

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"dev": "sonnet"},
        ):
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("ui-pick")
        self.assertIs(result, pinned)

    def test_career_slash_uses_the_search_route(self) -> None:
        """Career desk seeds /career; that must hit Settings → Task routing."""
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        routed = MagicMock()
        routed.model_preset = "flash"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"flash": object()}
        loop.runtime_resolver.resolve_preset.return_value = routed
        loop._session_pinned_preset.return_value = None

        msg = InboundMessage(
            channel="websocket",
            sender_id="u",
            chat_id="c",
            content="/career Find missions for: Data Engineer FR 700/day",
            metadata={"product_module": "career"},
        )

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"search": "flash", "dev": "sonnet"},
        ):
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("flash")
        self.assertIs(result, routed)

    def test_career_module_chat_uses_the_search_route(self) -> None:
        """A Career chat without /career must not fall through to inferred `dev`."""
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        routed = MagicMock()
        routed.model_preset = "flash"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"flash": object(), "sonnet": object()}
        loop.runtime_resolver.resolve_preset.return_value = routed
        loop._session_pinned_preset.return_value = None

        msg = InboundMessage(
            channel="websocket",
            sender_id="u",
            chat_id="c",
            content="Trouve-moi une mission Senior Data Engineer freelance France",
            metadata={"product_module": "career"},
        )

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"search": "flash", "dev": "sonnet"},
        ):
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("flash")
        self.assertIs(result, routed)

    def test_workflow_route_applies_without_thread_pin(self) -> None:
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        routed = MagicMock()
        routed.model_preset = "sonnet"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"sonnet": object()}
        loop.runtime_resolver.resolve_preset.return_value = routed
        # No sticky chat pin in this scenario.
        loop._session_pinned_preset.return_value = None

        msg = InboundMessage(
            channel="websocket",
            sender_id="u",
            chat_id="c",
            content="/forge Implement the plan",
            metadata={},
        )

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"dev": "sonnet"},
        ):
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("sonnet")
        self.assertIs(result, routed)

    def test_thread_preset_used_when_no_workflow_route(self) -> None:
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        pinned = MagicMock()
        pinned.model_preset = "ui-pick"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"ui-pick": object()}
        loop.runtime_resolver.resolve_preset.return_value = pinned

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"dev": "sonnet"},
        ):
            msg = InboundMessage(
                channel="websocket",
                sender_id="u",
                chat_id="c",
                content="just a normal question",
                metadata={INBOUND_META_MODEL_PRESET: "ui-pick"},
            )
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("ui-pick")
        self.assertIs(result, pinned)

    def test_composer_mode_metadata_routes_without_a_slash(self) -> None:
        """P2-4 acceptance: {composer_mode: plan} + bare text -> plan preset."""
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        routed = MagicMock()
        routed.model_preset = "opus-plan"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"opus-plan": object()}
        loop.runtime_resolver.resolve_preset.return_value = routed
        loop._session_pinned_preset.return_value = None

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"plan": "opus-plan"},
        ):
            msg = InboundMessage(
                channel="cli",
                sender_id="u",
                chat_id="c",
                content="design the auth system",
                metadata={"composer_mode": "plan"},
            )
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("opus-plan")
        self.assertIs(result, routed)

    def test_slash_route_wins_over_composer_mode(self) -> None:
        """An explicit /forge stays a dev turn even if the mode says plan."""
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        routed = MagicMock()
        routed.model_preset = "sonnet-dev"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"sonnet-dev": object(), "opus-plan": object()}
        loop.runtime_resolver.resolve_preset.return_value = routed
        loop._session_pinned_preset.return_value = None

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"dev": "sonnet-dev", "plan": "opus-plan"},
        ):
            msg = InboundMessage(
                channel="cli",
                sender_id="u",
                chat_id="c",
                content="/forge implement it",
                metadata={"composer_mode": "plan"},
            )
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("sonnet-dev")
        self.assertIs(result, routed)

    def test_agent_mode_still_reaches_the_dev_route_in_code(self) -> None:
        """composer_mode=agent must not shadow the code-module dev fallback."""
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        routed = MagicMock()
        routed.model_preset = "main"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"main": object()}
        loop.runtime_resolver.resolve_preset.return_value = routed
        loop._session_pinned_preset.return_value = None

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"dev": "main"},
        ):
            msg = InboundMessage(
                channel="websocket",
                sender_id="u",
                chat_id="c",
                content="fix the login bug",
                metadata={"product_module": "code", "composer_mode": "agent"},
            )
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("main")
        self.assertIs(result, routed)

    def test_code_module_falls_back_to_dev_route(self) -> None:
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        routed = MagicMock()
        routed.model_preset = "main"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"main": object(), "default": object()}
        loop.runtime_resolver.resolve_preset.return_value = routed
        # No sticky chat pin in this scenario.
        loop._session_pinned_preset.return_value = None

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"dev": "main"},
        ):
            msg = InboundMessage(
                channel="websocket",
                sender_id="u",
                chat_id="c",
                content="fix the login bug",
                metadata={"product_module": "code"},
            )
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("main")
        self.assertIs(result, routed)

    def test_plain_chat_infers_a_fast_route(self) -> None:
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        routed = MagicMock()
        routed.model_preset = "flash"
        routed.model = "deepseek/deepseek-v4-flash"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"flash": object()}
        loop.runtime_resolver.resolve_preset.return_value = routed
        loop._session_pinned_preset.return_value = None

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"fast": "flash", "dev": "sonnet"},
        ):
            msg = InboundMessage(
                channel="websocket",
                sender_id="u",
                chat_id="c",
                content="what is this?",
                metadata={},
            )
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("flash")
        self.assertIs(result, routed)
        self.assertEqual(loop._active_task_role, "fast")
        self.assertEqual(msg.metadata.get("model_route_role"), "fast")
        self.assertEqual(msg.metadata.get("task_role"), "fast")
        self.assertEqual(msg.metadata.get("model_name"), "deepseek/deepseek-v4-flash")
        self.assertEqual(msg.metadata.get("model_preset"), "flash")

    def test_pin_keeps_the_model_but_still_stamps_the_task(self) -> None:
        """Picker lock wins the runtime; the thinking strip still knows the task."""
        loop = MagicMock()
        default = MagicMock()
        default.model_preset = "default"
        pinned = MagicMock()
        pinned.model_preset = "ui-pick"
        pinned.model = "x-ai/grok-4.6"
        pinned_cfg = MagicMock()
        pinned_cfg.label = "Grok 4.6"
        pinned_cfg.modality = "text"

        loop.llm_runtime.return_value = default
        loop.model_presets = {"ui-pick": pinned_cfg, "flash": object()}
        loop.runtime_resolver.resolve_preset.return_value = pinned

        with patch(
            "navin.agent.model_routes.load_model_routes",
            return_value={"fast": "flash"},
        ):
            msg = InboundMessage(
                channel="websocket",
                sender_id="u",
                chat_id="c",
                content="what is this?",
                metadata={INBOUND_META_MODEL_PRESET: "ui-pick"},
            )
            result = AgentLoop.runtime_for_inbound(loop, msg)

        loop.runtime_resolver.resolve_preset.assert_called_once_with("ui-pick")
        self.assertIs(result, pinned)
        self.assertEqual(loop._active_task_role, "fast")
        self.assertEqual(msg.metadata.get("task_role"), "fast")
        self.assertEqual(msg.metadata.get("model_name"), "x-ai/grok-4.6")
        self.assertEqual(msg.metadata.get("model_label"), "Grok 4.6")


class InferTaskRoleTests(unittest.TestCase):
    def test_complex_and_security_keywords(self) -> None:
        self.assertEqual(infer_task_role("refactor the architecture"), "deep")
        self.assertEqual(infer_task_role("audit this for XSS"), "security")

    def test_review_plan_search_docs(self) -> None:
        self.assertEqual(infer_task_role("please review this diff"), "review")
        self.assertEqual(infer_task_role("draft a planning spec"), "plan")
        self.assertEqual(infer_task_role("search the web for this API"), "search")
        self.assertEqual(infer_task_role("write a README for this"), "docs")

    def test_short_question_is_fast(self) -> None:
        self.assertEqual(infer_task_role("c'est quoi ce fichier?"), "fast")
        self.assertEqual(infer_task_role("what is this?"), "fast")

    def test_everyday_coding_is_dev(self) -> None:
        self.assertEqual(infer_task_role("implement a function to parse the api"), "dev")

    def test_slash_still_maps(self) -> None:
        self.assertEqual(infer_task_role("/forge do it"), "dev")


if __name__ == "__main__":
    unittest.main()
