"""A user saying "oui" to the agent's own question must start the work.

The intent gate reads a bare "oui" as low-info chit-chat. Right after the
agent asked "shall I start?" that verdict produced the worst loop the product
can have: the gate forbids tools and asks again what to build, the user says
yes again, and neither side ever moves. Observed live with a 500 m2 garage
plan - three turns, zero tool calls, the same greeting every time.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from navin.command.builtin import (
    _CONFIRMED_FOCUS_CLAUSE,
    _UNCLEAR_FOCUS_CLAUSE,
    _WORKFLOW_BRIEFS,
    _workflow_handler,
    _workflow_skill_names,
)
from navin.command.confirmation import (
    confirmed_pending_question,
    focus_is_affirmative,
    last_assistant_question,
)
from navin.command.modules import (
    PRELOAD_SKILLS_METADATA_KEY,
    REQUIRES_TOOL_DELIVERY_METADATA_KEY,
)

PENDING_QUESTION = (
    "Bonjour ! Souhaitez-vous que je lance la generation complete du plan 3D "
    "avec l'implantation detaillee des 500 m2 ?"
)


class AffirmativeFocusTest(unittest.TestCase):
    def test_bare_green_lights_are_confirmations(self) -> None:
        for focus in ("oui", "OUI", "yes", "ok", "vas-y", "ok go", "go ahead",
                      "bien sur", "allez y", "oui stp", "d'accord", "yalla"):
            self.assertTrue(focus_is_affirmative(focus), focus)

    def test_a_refusal_is_not_a_confirmation(self) -> None:
        for focus in ("non", "no", "pas maintenant", "attends", "stop"):
            self.assertFalse(focus_is_affirmative(focus), focus)

    def test_a_yes_carrying_new_instructions_stands_on_its_own(self) -> None:
        # "oui mais ajoute un etage" is a build request, not a green light:
        # the normal gate must read it, gaps and all.
        self.assertFalse(focus_is_affirmative("oui mais ajoute un etage"))
        self.assertFalse(focus_is_affirmative("ok cree le dashboard admin"))

    def test_empty_and_long_messages_are_never_confirmations(self) -> None:
        self.assertFalse(focus_is_affirmative(""))
        self.assertFalse(focus_is_affirmative("ok " * 12))


class PendingQuestionTest(unittest.TestCase):
    def test_reads_the_question_the_agent_left_open(self) -> None:
        messages = [
            {"role": "user", "content": "plan du garage"},
            {"role": "assistant", "content": PENDING_QUESTION},
        ]
        self.assertEqual(last_assistant_question(messages), PENDING_QUESTION)
        self.assertEqual(confirmed_pending_question("oui", messages), PENDING_QUESTION)

    def test_a_finished_turn_leaves_nothing_to_confirm(self) -> None:
        # No question means "oui" is just acknowledgement; inventing a target
        # from the last thing said would be worse than the gate's question.
        messages = [{"role": "assistant", "content": "Fichier ecrit: plan.html"}]
        self.assertEqual(confirmed_pending_question("oui", messages), "")

    def test_only_the_latest_assistant_message_counts(self) -> None:
        messages = [
            {"role": "assistant", "content": "On commence par quoi ?"},
            {"role": "user", "content": "le plan 3D"},
            {"role": "assistant", "content": "Voici le plan 3D."},
        ]
        self.assertEqual(confirmed_pending_question("oui", messages), "")

    def test_tool_only_assistant_records_do_not_hide_the_question(self) -> None:
        messages = [
            {"role": "assistant", "content": PENDING_QUESTION},
            {"role": "assistant", "content": ""},
        ]
        self.assertEqual(confirmed_pending_question("oui", messages), PENDING_QUESTION)

    def test_no_history_is_handled(self) -> None:
        self.assertEqual(confirmed_pending_question("oui", None), "")
        self.assertEqual(last_assistant_question([]), "")


class ConfirmedWorkflowBriefTest(unittest.TestCase):
    def _brief(self, command: str, focus: str, history: list | None) -> tuple[str, dict]:
        msg = SimpleNamespace(
            content="", metadata={}, channel="cli", chat_id="test",
        )
        ctx = SimpleNamespace(
            args=focus,
            raw=f"{command} {focus}",
            msg=msg,
            loop=None,
            session=SimpleNamespace(messages=history) if history is not None else None,
        )
        asyncio.run(_workflow_handler(command)(ctx))  # type: ignore[arg-type]
        return msg.content, dict(msg.metadata)

    def test_a_confirmed_forge_turn_gets_the_full_mission_brief(self) -> None:
        content, meta = self._brief(
            "/forge",
            "oui",
            [{"role": "assistant", "content": PENDING_QUESTION}],
        )
        self.assertIn(_CONFIRMED_FOCUS_CLAUSE, content)
        self.assertNotIn(_UNCLEAR_FOCUS_CLAUSE, content)
        # The confirmed question is the target, so the model knows what "oui"
        # meant without re-deriving it from history.
        self.assertIn(PENDING_QUESTION, content)
        self.assertIn('They answered "oui" to that question.', content)
        # Skills come back too: a gated turn preloads none, so their presence
        # is how we know the mission pipeline is really open.
        for skill in _workflow_skill_names(_WORKFLOW_BRIEFS["/forge"][1]):
            self.assertIn(skill, meta.get(PRELOAD_SKILLS_METADATA_KEY, []))

    def test_a_confirmed_turn_arms_the_no_tool_nudge(self) -> None:
        # Answering a yes with a third promise is the loop itself. /forge is
        # not a delivery workflow, so without this it had no net at all.
        _content, meta = self._brief(
            "/forge",
            "oui",
            [{"role": "assistant", "content": PENDING_QUESTION}],
        )
        self.assertTrue(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY))

    def test_a_cold_greeting_still_hits_the_intent_gate(self) -> None:
        content, meta = self._brief("/forge", "oui", [])
        self.assertIn(_UNCLEAR_FOCUS_CLAUSE, content)
        self.assertNotIn(_CONFIRMED_FOCUS_CLAUSE, content)
        self.assertEqual(meta.get(PRELOAD_SKILLS_METADATA_KEY), [])
        self.assertFalse(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY))

    def test_a_handler_without_a_session_does_not_crash(self) -> None:
        content, _meta = self._brief("/forge", "oui", None)
        self.assertIn(_UNCLEAR_FOCUS_CLAUSE, content)

    def test_a_concrete_target_is_untouched_by_the_guard(self) -> None:
        content, _meta = self._brief(
            "/forge",
            "cree un dashboard admin avec authentification",
            [{"role": "assistant", "content": PENDING_QUESTION}],
        )
        self.assertNotIn(_CONFIRMED_FOCUS_CLAUSE, content)
        self.assertIn("cree un dashboard admin avec authentification", content)

    def test_ask_mode_also_stops_re_asking(self) -> None:
        content, _meta = self._brief(
            "/ask",
            "oui",
            [{"role": "assistant", "content": "Voulez-vous que je compare les deux options ?"}],
        )
        self.assertIn(_CONFIRMED_FOCUS_CLAUSE, content)


if __name__ == "__main__":
    unittest.main()
