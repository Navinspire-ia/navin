# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Deterministic dev intent routing: which turns open the Code module."""

from __future__ import annotations

import pytest

from navin.agent.dev_intent import (
    CODE_MODULE,
    detect_dev_intent,
    product_module_for_turn,
)


class TestRecognisesDevWork:
    @pytest.mark.parametrize(
        "text",
        [
            "cree une api de login en fastapi",
            "implemente le endpoint /users",
            "corrige le bug du formulaire de contact",
            "refactor le composant Header",
            "ajoute des tests unitaires sur le parser",
            "debug le login, il boucle",
            "optimise les requetes sql du dashboard",
            "migre la base de donnees vers postgres",
            "fais moi une landing page",
            "cree un site vitrine pour mon resto",
            "modifie le composant Button pour ajouter une variante",
            "installe tailwind dans le projet",
            "explique moi ce fichier",
            "review mon code",
            "analyse l architecture du repo",
        ],
    )
    def test_french_requests(self, text):
        intent = detect_dev_intent(text)
        assert intent is not None, text
        assert intent.module == CODE_MODULE

    @pytest.mark.parametrize(
        "text",
        [
            "build a react dashboard with charts",
            "fix the failing tests in the auth module",
            "implement a rest api with fastapi",
            "refactor this component to use hooks",
            "add pytest coverage for the parser",
            "deploy the app to vercel",
            "why does my build fail on typescript",
        ],
    )
    def test_english_requests(self, text):
        intent = detect_dev_intent(text)
        assert intent is not None, text
        assert intent.module == CODE_MODULE

    @pytest.mark.parametrize(
        "text",
        [
            "regarde src/App.tsx",
            "le fichier navin/agent/loop.py plante",
            "ouvre ./scripts/build.sh",
            "probleme dans components/Header.jsx",
        ],
    )
    def test_a_concrete_path_is_enough(self, text):
        intent = detect_dev_intent(text)
        assert intent is not None, text
        assert intent.strong, "a real path is strong evidence, not a guess"

    @pytest.mark.parametrize(
        "text",
        [
            "Traceback (most recent call last): File x.py line 3",
            "TypeError: undefined is not a function",
            "ModuleNotFoundError: No module named 'pydantic'",
            "error TS2345: argument of type string",
            "npm ERR! code ELIFECYCLE",
        ],
    )
    def test_a_pasted_error_needs_no_verb(self, text):
        intent = detect_dev_intent(text)
        assert intent is not None, text
        assert intent.strong


class TestLeavesEverythingElseAlone:
    @pytest.mark.parametrize(
        "text",
        [
            "bonjour",
            "merci beaucoup",
            "quelle est la capitale du perou",
            "resume moi cet article de presse",
            "ecris moi un email de relance client",
            "trouve moi des prospects dans le batiment",
            "fais moi un devis pour 3 jours de presta",
            "raconte moi une blague",
            "quel temps fait il demain",
            "traduis ce paragraphe en anglais",
            # Business words that read as dev words if the lexicon is careless.
            "analyse la performance de ma campagne",
            "relance ce client par telephone",
            "cree une presentation pour le client",
            "fais moi un tableau de bord commercial sur excel",
        ],
    )
    def test_non_dev_requests(self, text):
        assert detect_dev_intent(text) is None, text

    @pytest.mark.parametrize(
        "text",
        [
            "genere un chat qui mange une banane",
            "cree une image de logo",
            "fais une video de 10 secondes",
            "compose une musique douce",
            "genere une voix off pour la video",
            "cree une video complete avec musique et voix",
        ],
    )
    def test_media_requests_belong_to_the_media_desk(self, text):
        # These share their verb with dev requests; the media router owns them.
        assert detect_dev_intent(text) is None, text

    def test_empty_input(self):
        assert detect_dev_intent(None) is None
        assert detect_dev_intent("") is None
        assert detect_dev_intent("   ") is None


class TestSwitchDecision:
    def test_a_dev_turn_from_an_unscoped_shell_switches(self):
        assert product_module_for_turn("corrige le bug du login") == CODE_MODULE
        assert product_module_for_turn("corrige le bug du login", current_module=None) == CODE_MODULE
        assert product_module_for_turn("corrige le bug du login", current_module="") == CODE_MODULE

    def test_ask_never_moves_the_user(self):
        assert product_module_for_turn("corrige le bug du login", composer_mode="ask") is None

    def test_montage_keeps_its_desk(self):
        assert product_module_for_turn("corrige le bug du login", composer_mode="montage") is None

    @pytest.mark.parametrize("mode", ["agent", "plan", "review", "security", "debug", None, ""])
    def test_every_other_mode_switches(self, mode):
        assert product_module_for_turn("implemente le endpoint /users", composer_mode=mode) == CODE_MODULE

    def test_an_already_scoped_shell_is_left_alone(self):
        # Code is already the target, and a deliberately chosen module is not
        # taken away because a build word appeared.
        for module in ("code", "seo", "ads", "leads", "scraping", "risklens"):
            assert product_module_for_turn("corrige le bug", current_module=module) is None

    def test_a_non_dev_turn_switches_nothing(self):
        assert product_module_for_turn("bonjour, ca va ?") is None


class TestCaseAndAccentsDoNotMatter:
    @pytest.mark.parametrize(
        "text",
        [
            "CORRIGE LE BUG DU LOGIN",
            "Implémente le endpoint /users",
            "Refactorise le composant Héros",
            "débogue l'API",
        ],
    )
    def test_variants(self, text):
        assert detect_dev_intent(text) is not None, text
