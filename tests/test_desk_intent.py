# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Desk schemas are off by default; only a desk's slash command or its module opens it.

The five desk tools cost ~9k prompt tokens together, and prompt size is
wall-clock: measured 2026-09-01 on glm-5.3-flash, a ~64k prompt answered in
1768 ms against 1037 ms for ~31k, both at a 100% cache hit. A coding turn
paying for `tenders` on every step was renting that for nothing.

Plain words no longer open a desk: on coding turns they misfired constantly
("resume en 3 points" opened career) and each misfire reset the prompt cache.
"""

from __future__ import annotations

import unittest

from navin.agent.desk_intent import desk_tools_for_text
from navin.agent.loop import AgentLoop
from navin.command.modules import ACTIVE_DESKS_METADATA_KEY, STUDIO_OWNED_TOOLS

DESKS = frozenset(STUDIO_OWNED_TOOLS.values())


def _desks_in_schema(
    text: str | None,
    metadata: dict | None = None,
    session_metadata: dict | None = None,
) -> set[str]:
    denied = AgentLoop._denied_tools(None, metadata or {}, text, session_metadata)
    return set(DESKS) - set(denied)


class DeskIntentTest(unittest.TestCase):
    def test_ordinary_work_names_no_desk(self):
        for text in (
            "corrige le bug dans runner.py",
            "refais le dashboard React avec MUI",
            "ecris un script python qui parse un csv",
            "bonjour",
            "explique moi ce fichier",
        ):
            with self.subTest(text=text):
                self.assertEqual(desk_tools_for_text(text), frozenset())

    def test_plain_words_never_open_a_desk(self):
        """Each desk is its own module: Navin Code must not pull it in."""
        for text in (
            "trouve moi des appels d offres BTP",
            "ameliore mon CV pour un poste de data engineer",
            "resume en 3 points",
            "il me faut des leads dans la logistique",
            "prepare une campagne marketing pour le lancement",
            "regarde mon portefeuille et place un stop loss",
            "ajoute un champ contacts au modele",
            "ajoute une video de demo",
        ):
            with self.subTest(text=text):
                self.assertEqual(desk_tools_for_text(text), frozenset())

    def test_a_desk_path_is_not_its_slash_command(self):
        for text in ("regarde navin/career/loop.py", "corrige navin/trading/store.py", "/careers"):
            with self.subTest(text=text):
                self.assertEqual(desk_tools_for_text(text), frozenset())

    def test_a_slash_command_opens_its_desk(self):
        self.assertIn("tenders", desk_tools_for_text("/tenders veille du jour"))
        self.assertIn("career", desk_tools_for_text("/career refais mon CV"))
        self.assertIn("marketing", desk_tools_for_text("/campaign lancement"))

    def test_a_command_after_the_brief_still_opens_its_desk(self):
        self.assertEqual(desk_tools_for_text("fais la veille /tenders"), frozenset({"tenders"}))


class DeskSchemaGatingTest(unittest.TestCase):
    def test_a_coding_turn_ships_no_desk_schema(self):
        self.assertEqual(_desks_in_schema("corrige runner.py"), set())

    def test_naming_a_desk_keeps_it_out_of_the_schema(self):
        self.assertEqual(_desks_in_schema("trouve des appels d offres"), set())

    def test_its_slash_command_puts_a_desk_in_the_schema(self):
        self.assertEqual(_desks_in_schema("/tenders veille du jour"), {"tenders"})

    def test_a_product_module_keeps_its_own_desk(self):
        self.assertEqual(
            _desks_in_schema("continue", {"product_module": "tenders"}),
            {"tenders"},
        )

    def test_a_module_hides_the_other_desks(self):
        shown = _desks_in_schema("continue", {"product_module": "career"})
        self.assertEqual(shown, {"career"})

    def test_a_followup_keeps_the_desk_the_chat_opened(self):
        """"continue" must not lose the desk the conversation is about."""
        session = {ACTIVE_DESKS_METADATA_KEY: ["tenders"]}
        self.assertEqual(_desks_in_schema("continue", {}, session), {"tenders"})

    def test_a_turn_that_opens_a_desk_reports_it_for_the_session(self):
        opened = AgentLoop._desk_tools_opened_by_turn(
            {}, "/tenders je cherche des appels d offres"
        )
        self.assertEqual(opened, {"tenders"})

    def test_a_module_turn_reports_its_desk_for_the_session(self):
        opened = AgentLoop._desk_tools_opened_by_turn(
            {"product_module": "leads"}, "continue"
        )
        self.assertEqual(opened, {"leads"})

    def test_a_plain_turn_opens_nothing(self):
        self.assertEqual(
            AgentLoop._desk_tools_opened_by_turn({}, "corrige le test"), set()
        )


class CodeWorkbenchDesksTest(unittest.TestCase):
    """montage (1.6k tokens) and crm (0.4k) leave the Code workbench schema
    until /montage or /crm opens them; then they stay for the session."""

    CODE = {"product_module": "code", "composer_mode": "agent"}

    def _visible(self, text: str, session: dict | None = None) -> set[str]:
        denied = AgentLoop._denied_tools(None, self.CODE, text, session)
        return {"montage", "crm"} - set(denied)

    def test_a_code_turn_ships_neither(self):
        self.assertEqual(self._visible("fix the failing test and run pytest"), set())
        self.assertEqual(self._visible("le client envoie une requete HTTP"), set())

    def test_naming_a_video_or_a_contact_brings_nothing_back(self):
        self.assertEqual(self._visible("add subtitles to the demo video"), set())
        self.assertEqual(self._visible("sync the CRM contacts into the table"), set())

    def test_the_slash_commands_bring_them_back(self):
        self.assertEqual(self._visible("/montage teaser produit"), {"montage"})
        self.assertEqual(self._visible("/crm import"), {"crm"})

    def test_the_session_remembers_an_opened_desk(self):
        session = {ACTIVE_DESKS_METADATA_KEY: ["montage"]}
        self.assertEqual(self._visible("continue", session), {"montage"})
        self.assertEqual(
            AgentLoop._desk_tools_opened_by_turn(self.CODE, "/montage coupe la video en clips"),
            {"marketing", "montage"},
        )

    def test_outside_the_code_workbench_nothing_changes(self):
        denied = AgentLoop._denied_tools(None, {}, "fix the failing test", None)
        self.assertNotIn("montage", denied)
        self.assertNotIn("crm", denied)


if __name__ == "__main__":
    unittest.main()
