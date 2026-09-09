# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The heavy multiplexer tools ship only when a turn can use them, and never vanish when it can."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from navin.agent.loop import AgentLoop
from navin.agent.tool_demand import (
    BOARD_TOOL,
    BROWSER_TOOL,
    CRON_TOOL,
    LIST_EXEC_SESSIONS_TOOL,
    MOBILE_TOOL,
    NOTEBOOK_TOOL,
    ON_DEMAND_TOOLS,
    WRITE_STDIN_TOOL,
    clear_project_facts_cache,
    on_demand_tools_for_facts,
    on_demand_tools_for_text,
    project_facts,
)
from navin.agent.tools.exec_session import ExecSessionManager


def _gated(text: str | None, *, module: str | None = "code", workspace: str | None = None) -> set[str]:
    metadata = {"product_module": module} if module else {}
    return set(AgentLoop._denied_tools(None, metadata, text, None, workspace=workspace)) & set(
        ON_DEMAND_TOOLS
    )


class TextLiftTest(unittest.TestCase):
    def test_a_plain_code_turn_names_none_of_them(self) -> None:
        self.assertEqual(on_demand_tools_for_text("corrige la fonction parse_date dans utils.py"), frozenset())
        self.assertEqual(on_demand_tools_for_text("add a retry to the http client"), frozenset())

    def test_each_tool_answers_to_its_own_words(self) -> None:
        cases = {
            BOARD_TOOL: ("/forge ajoute un cache", "ouvre le backlog", "what is left on the board?"),
            BROWSER_TOOL: ("prends un screenshot", "va sur https://example.org", "click the submit button"),
            CRON_TOOL: ("rappelle-moi chaque jour a 9h", "schedule a weekly report", "cron toutes les heures"),
            MOBILE_TOOL: ("build l'apk android", "lance le simulateur ios", "expo start"),
            NOTEBOOK_TOOL: ("ouvre le notebook", "edit cell 3 of analysis.ipynb", "restart the jupyter kernel"),
            WRITE_STDIN_TOOL: ("lance le serveur de dev en arriere-plan", "tail the logs", "start a repl"),
        }
        for tool, texts in cases.items():
            for text in texts:
                with self.subTest(tool=tool, text=text):
                    self.assertIn(tool, on_demand_tools_for_text(text))

    def test_stdin_brings_the_session_list_with_it(self) -> None:
        lifted = on_demand_tools_for_text("poll the background build")
        self.assertIn(WRITE_STDIN_TOOL, lifted)
        self.assertIn(LIST_EXEC_SESSIONS_TOOL, lifted)

    def test_accents_and_apostrophes_do_not_hide_the_word(self) -> None:
        self.assertIn(CRON_TOOL, on_demand_tools_for_text("Planifie-le pour chaque matin"))
        self.assertIn(MOBILE_TOOL, on_demand_tools_for_text("l'émulateur ne démarre pas"))


class RepositoryFactsTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_project_facts_cache()
        self.root = Path(tempfile.mkdtemp())

    def test_an_empty_checkout_has_no_tags(self) -> None:
        self.assertEqual(project_facts(self.root), frozenset())
        self.assertEqual(on_demand_tools_for_facts(self.root), frozenset())

    def test_a_missing_or_unset_root_gates_nothing_open_and_lifts_nothing(self) -> None:
        self.assertEqual(project_facts(None), frozenset())
        self.assertEqual(project_facts(self.root / "does-not-exist"), frozenset())

    def test_an_expo_checkout_keeps_mobile_without_the_word(self) -> None:
        (self.root / "app.json").write_text("{}", encoding="utf-8")
        self.assertIn(MOBILE_TOOL, on_demand_tools_for_facts(self.root))
        self.assertNotIn(MOBILE_TOOL, _gated("build the app", workspace=str(self.root)))

    def test_android_and_ios_folders_together_mean_mobile(self) -> None:
        (self.root / "android").mkdir()
        (self.root / "ios").mkdir()
        self.assertIn("mobile", project_facts(self.root))

    def test_one_of_the_two_folders_does_not(self) -> None:
        (self.root / "android").mkdir()
        self.assertNotIn("mobile", project_facts(self.root))

    def test_a_web_checkout_keeps_browser(self) -> None:
        (self.root / "package.json").write_text("{}", encoding="utf-8")
        self.assertIn(BROWSER_TOOL, on_demand_tools_for_facts(self.root))
        self.assertNotIn(BROWSER_TOOL, _gated("corrige le bouton login", workspace=str(self.root)))

    def test_a_backend_checkout_does_not_pay_for_browser(self) -> None:
        (self.root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        self.assertIn(BROWSER_TOOL, _gated("corrige le bouton login", workspace=str(self.root)))

    def test_a_notebook_one_level_down_keeps_notebook_edit(self) -> None:
        (self.root / "analysis").mkdir()
        (self.root / "analysis" / "eda.ipynb").write_text("{}", encoding="utf-8")
        self.assertIn(NOTEBOOK_TOOL, on_demand_tools_for_facts(self.root))

    def test_a_notebook_buried_in_node_modules_does_not_count(self) -> None:
        deep = self.root / "node_modules" / "pkg"
        deep.mkdir(parents=True)
        (deep / "demo.ipynb").write_text("{}", encoding="utf-8")
        self.assertNotIn("notebooks", project_facts(self.root))

    def test_a_project_with_a_board_keeps_board(self) -> None:
        (self.root / ".navin" / "board").mkdir(parents=True)
        self.assertIn(BOARD_TOOL, on_demand_tools_for_facts(self.root))
        self.assertNotIn(BOARD_TOOL, _gated("continue", workspace=str(self.root)))

    def test_the_tags_are_cached_then_refreshed_on_clear(self) -> None:
        self.assertEqual(project_facts(self.root), frozenset())
        (self.root / "app.json").write_text("{}", encoding="utf-8")
        self.assertEqual(project_facts(self.root), frozenset(), "still cached")
        clear_project_facts_cache()
        self.assertIn("mobile", project_facts(self.root))


class LiveSessionTest(unittest.TestCase):
    def test_a_fresh_manager_has_no_open_session(self) -> None:
        self.assertFalse(ExecSessionManager().has_open_sessions())

    def test_a_live_session_keeps_stdin_and_the_list(self) -> None:
        lifted = on_demand_tools_for_facts(None, exec_sessions_open=True)
        self.assertEqual(lifted, frozenset({WRITE_STDIN_TOOL, LIST_EXEC_SESSIONS_TOOL}))


class DenylistWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        clear_project_facts_cache()
        self.empty = tempfile.mkdtemp()

    def test_a_plain_code_turn_in_an_empty_checkout_withholds_all_seven(self) -> None:
        self.assertEqual(_gated("corrige parse_date", workspace=self.empty), set(ON_DEMAND_TOOLS))

    def test_the_same_turn_without_a_module_withholds_them_too(self) -> None:
        self.assertEqual(_gated("corrige parse_date", module=None, workspace=self.empty), set(ON_DEMAND_TOOLS))

    def test_forge_brings_the_board_back(self) -> None:
        self.assertNotIn(BOARD_TOOL, _gated("/forge ajoute un cache", workspace=self.empty))

    def test_a_desk_module_is_not_touched(self) -> None:
        self.assertEqual(_gated("liste les appels d'offres", module="tenders", workspace=self.empty), set())

    def test_naming_a_tool_lifts_only_that_tool(self) -> None:
        gated = _gated("rappelle-moi chaque jour", workspace=self.empty)
        self.assertNotIn(CRON_TOOL, gated)
        self.assertEqual(gated | {CRON_TOOL}, set(ON_DEMAND_TOOLS))


if __name__ == "__main__":
    unittest.main()
