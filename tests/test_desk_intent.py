# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Desk schemas are off by default and come back when the turn asks for them.

The five desk tools cost ~9k prompt tokens together, and prompt size is
wall-clock: measured 2026-09-01 on glm-5.3-flash, a ~64k prompt answered in
1768 ms against 1037 ms for ~31k, both at a 100% cache hit. A coding turn
paying for `tenders` on every step was renting that for nothing.
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

    def test_each_desk_is_recognised_from_plain_french(self):
        cases = {
            "trouve moi des appels d offres BTP": "tenders",
            "ameliore mon CV pour un poste de data engineer": "career",
            "il me faut des leads dans la logistique": "leads",
            "prepare une campagne marketing pour le lancement": "marketing",
            "regarde mon portefeuille et place un stop loss": "trading",
        }
        for text, tool in cases.items():
            with self.subTest(text=text):
                self.assertIn(tool, desk_tools_for_text(text))

    def test_a_slash_command_opens_its_desk(self):
        self.assertIn("tenders", desk_tools_for_text("/tenders veille du jour"))
        self.assertIn("career", desk_tools_for_text("/career refais mon CV"))
        self.assertIn("marketing", desk_tools_for_text("/campaign lancement"))

    def test_accents_and_apostrophes_do_not_hide_a_desk(self):
        self.assertIn("tenders", desk_tools_for_text("cherche des appels d'offres"))
        self.assertIn("career", desk_tools_for_text("ma lettre de motivation"))


class DeskSchemaGatingTest(unittest.TestCase):
    def test_a_coding_turn_ships_no_desk_schema(self):
        self.assertEqual(_desks_in_schema("corrige runner.py"), set())

    def test_naming_a_desk_puts_it_back_in_the_schema(self):
        self.assertEqual(
            _desks_in_schema("trouve des appels d offres"), {"tenders"}
        )

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
            {}, "je cherche des appels d offres"
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
    until a turn names a video or a contact; then they stay for the session."""

    CODE = {"product_module": "code", "composer_mode": "agent"}

    def _visible(self, text: str, session: dict | None = None) -> set[str]:
        denied = AgentLoop._denied_tools(None, self.CODE, text, session)
        return {"montage", "crm"} - set(denied)

    def test_a_code_turn_ships_neither(self):
        self.assertEqual(self._visible("fix the failing test and run pytest"), set())
        self.assertEqual(self._visible("le client envoie une requete HTTP"), set())

    def test_naming_a_video_brings_montage_back(self):
        self.assertEqual(self._visible("add subtitles to the demo video"), {"montage"})
        self.assertEqual(self._visible("ajoute une vidéo de démo"), {"montage"})
        self.assertIn("montage", desk_tools_for_text("/montage teaser produit"))

    def test_naming_the_crm_brings_it_back(self):
        self.assertEqual(self._visible("sync the CRM contacts into the table"), {"crm"})
        self.assertEqual(self._visible("/crm import"), {"crm"})

    def test_the_session_remembers_an_opened_desk(self):
        session = {ACTIVE_DESKS_METADATA_KEY: ["montage"]}
        self.assertEqual(self._visible("continue", session), {"montage"})
        self.assertEqual(
            AgentLoop._desk_tools_opened_by_turn(self.CODE, "coupe la video en clips"),
            {"montage"},
        )

    def test_outside_the_code_workbench_nothing_changes(self):
        denied = AgentLoop._denied_tools(None, {}, "fix the failing test", None)
        self.assertNotIn("montage", denied)
        self.assertNotIn("crm", denied)


if __name__ == "__main__":
    unittest.main()
