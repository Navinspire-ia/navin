# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A go-ahead, a green report and a "tell me go" are read for what they are."""

from __future__ import annotations

import pytest

from navin.board.auto_advance import reports_failure
from navin.utils.runtime import is_go_ahead, lists_remaining_work


@pytest.mark.parametrize("text", [
    "go", "/forge go", "Go !", "vas-y", "vas y", "ok vas y", "oui continue", "continue",
    "ok", "go ahead", "allez go", "ok go stp", "fais-le", "on y va", "/forge vas y",
    "lance", "oui lance", "la suite", "enchaîne", "continue jusqu'au bout",
    "vasy", "/forge vasy", "vazi", "gooo",
])
def test_go_ahead(text: str) -> None:
    assert is_go_ahead(text)


@pytest.mark.parametrize("text", [
    "go to the settings page and fix the button",
    "vas y corrige le bug du login",
    "pourquoi ça plante ?",
    "ok mais pas le billing",
    "non",
    "continue mais sans toucher au front",
    "/forge ajoute un bouton",
    "vasy corrige le login",
    "google",
    "",
])
def test_not_a_go_ahead(text: str) -> None:
    assert not is_go_ahead(text)


@pytest.mark.parametrize("text", [
    "Tests : 71 passés, 0 échec.",
    "Aucun échec, 30 tests verts.",
    "12 passed, 0 failed",
    "no failures",
    "failed: 0",
])
def test_green_report_naming_the_failure_count_is_not_a_failure(text: str) -> None:
    assert not reports_failure(text)


@pytest.mark.parametrize("text", ["Le build a échoué.", "2 échecs restent.", "The migration failed."])
def test_real_failure_is_still_a_failure(text: str) -> None:
    assert reports_failure(text)


@pytest.mark.parametrize("text", [
    'Je la démarre dès que tu me dis "go".',
    "Dis-moi go et je lance le registre.",
    "Say go and I start the registry.",
    "I will start as soon as you confirm.",
])
def test_waiting_for_a_go_is_work_left(text: str) -> None:
    assert lists_remaining_work(text)


def test_a_finished_report_is_not_work_left() -> None:
    assert not lists_remaining_work("Tout est fait et vérifié : 71 tests verts.")
