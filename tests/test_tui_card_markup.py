# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Agent-provided commands and questions must never become UI markup."""

import asyncio
from dataclasses import replace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, OptionList, Static

from navin.tui.theme import NAVIN_THEMES
from navin.tui.widgets import ApprovalCard, ChoiceCard
from tests.test_tui_prompt_cleanup import approval_request, start_work
from tests.test_tui_queue import make_app


class CardHost(App):
    def __init__(self, card):
        super().__init__()
        self.card = card
        for theme in NAVIN_THEMES:
            self.register_theme(theme)
        self.theme = "navin"

    def compose(self) -> ComposeResult:
        yield self.card


@pytest.mark.parametrize("text", [
    "[unterminated",
    "[/dim] [bold]literal[/bold]",
    "C:\\work\\",
    'printf "["; echo "[1, 2]"',
    "Crochets [ouverts\nchemin C:\\fin\\",
    "x" * 599 + "[truncated]",
])
def test_approval_renders_literal_fields_and_closure(text):
    async def run():
        card = ApprovalCard("request", text, text, text, text, text, text, True)
        app = CardHost(card)
        async with app.run_test(size=(156, 47)) as pilot:
            await pilot.pause()
            assert str(card.query_one(".card-title", Static).content) == f"⚠ Permission needed  {text}"
            assert [str(button.label) for button in card.query(Button)] == [
                "Allow  [y]", "Always  [a]", "Deny  [n]",
            ]
            detail = card.query_one(".card-detail", Static)
            assert str(detail.content) == "\n".join([
                text, f"why: {text}", text[:600], f"impact: {text}", f"scope: {text}",
            ])
            for allowed in (True, False):
                card.close(allowed, reason=text)
                await pilot.pause()
                verdict = "allowed" if allowed else "denied"
                icon = "✓" if allowed else "✗"
                assert str(card.query_one(".card-title", Static).content) == (
                    f"{icon} Permission {verdict}  {text}  {text}"
                )
    asyncio.run(run())


@pytest.mark.parametrize("decision", ["allow", "always", "deny"])
def test_literal_approval_still_delivers_the_selected_decision(tmp_path, decision):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(156, 47)) as pilot:
            await start_work(app)
            request = replace(approval_request(), action='printf "["', detail="C:\\work\\")
            await app._on_runtime_event(request)
            await pilot.pause()
            await pilot.click(app.query_one(ApprovalCard).query_one(f"#{decision}"))
            answer = await asyncio.wait_for(app.runtime.bus.consume_inbound(), 2)
            assert answer.metadata["allowed"] is (decision != "deny")
            assert answer.metadata["remember"] is (decision == "always")
            assert not app.query(ApprovalCard)
            assert app.composer.text == "brouillon conserve"
            assert app.runtime.turn_active
    asyncio.run(run())


def test_choice_labels_descriptions_and_custom_answer_are_literal():
    async def run():
        question = "Conserver [ce chemin C:\\work\\"
        label = "[ouvert"
        description = "[/dim] C:\\work\\"
        card = ChoiceCard("choice", question, [
            {"id": "yes", "label": label, "description": description},
        ], True, "yes")
        app = CardHost(card)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert str(card.query_one(".card-title", Static).content) == f"❔ {question}"
            options = card.query_one(OptionList)
            assert str(options.get_option_at_index(0).prompt) == f"1. {label} ★\n   {description}"
            await pilot.press("enter")
            card.close(custom_text=description)
            await pilot.pause()
            assert str(card.query_one(".card-title", Static).content) == f"✓ {question}  → {description}"
    asyncio.run(run())
