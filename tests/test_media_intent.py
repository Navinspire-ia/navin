# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Media intent routing: the request that used to come back as inline SVG.

The regression these tests defend is behavioural, not cosmetic. "genere un chat
qui mange une banane" names no medium, so the old code left the decision to the
model; a failed-over small model answered with clarifying questions and a
hand-written SVG. Reading that phrasing as an image request is therefore the
point, and so is *not* reading "genere un fichier de config" the same way.
"""

from __future__ import annotations

import pytest

from navin.agent.media_intent import (
    IMAGE_TOOL,
    KIND_FULL_VIDEO,
    KIND_IMAGE,
    KIND_MUSIC,
    KIND_SPEECH,
    KIND_VIDEO,
    MONTAGE_TOOL,
    MUSIC_TOOL,
    SPEECH_TOOL,
    VIDEO_TOOL,
    detect_media_intent,
    forced_tool_choice,
    media_tools_for_text,
)


class TestImplicitImage:
    def test_the_original_bug_report(self):
        intent = detect_media_intent("genere un chat qui mange une banane")
        assert intent is not None
        assert intent.kind == KIND_IMAGE
        assert intent.tools == (IMAGE_TOOL,)
        assert intent.explicit is False
        # Implicit or not, the turn must actually call the image model.
        assert intent.should_force_tool() is True

    def test_accents_and_casing_do_not_matter(self):
        for text in (
            "Génère un chat qui mange une banane",
            "GÉNÈRE UN CHAT QUI MANGE UNE BANANE",
            "génere un chat qui mange une banane",
        ):
            intent = detect_media_intent(text)
            assert intent is not None, text
            assert intent.kind == KIND_IMAGE

    def test_a_long_sentence_is_not_guessed_as_an_image(self):
        text = (
            "cree une strategie de lancement pour notre produit en tenant compte "
            "du budget marketing et des concurrents sur le marche europeen actuel"
        )
        assert detect_media_intent(text) is None

    def test_without_a_generation_verb_nothing_is_routed(self):
        assert detect_media_intent("un chat qui mange une banane") is None
        assert detect_media_intent("pourquoi le ciel est bleu") is None


class TestExplicitMedium:
    @pytest.mark.parametrize(
        "text",
        [
            "genere une image d un chat",
            "cree une illustration de chat",
            "fais moi un logo pour ma marque",
            "generate an image of a cat",
            "make a poster for the launch",
        ],
    )
    def test_image_nouns(self, text):
        intent = detect_media_intent(text)
        assert intent is not None, text
        assert intent.kind == KIND_IMAGE
        assert intent.explicit is True

    @pytest.mark.parametrize(
        "text",
        [
            "genere une video de 30 secondes",
            "cree un clip pour instagram",
            "fais une animation de logo",
            "generate a short video",
        ],
    )
    def test_video_nouns(self, text):
        intent = detect_media_intent(text)
        assert intent is not None, text
        assert intent.kind == KIND_VIDEO
        assert intent.tools == (VIDEO_TOOL,)

    @pytest.mark.parametrize(
        "text",
        [
            "genere une musique douce",
            "cree un jingle de 5 secondes",
            "compose une melodie au piano",
            "generate a soundtrack",
        ],
    )
    def test_music_nouns(self, text):
        intent = detect_media_intent(text)
        assert intent is not None, text
        assert intent.kind == KIND_MUSIC
        assert intent.tools == (MUSIC_TOOL,)

    @pytest.mark.parametrize(
        "text",
        [
            "genere une voix off pour ce texte",
            "fais une narration en francais",
            "cree un podcast audio",
            "generate a voiceover",
        ],
    )
    def test_speech_nouns(self, text):
        intent = detect_media_intent(text)
        assert intent is not None, text
        assert intent.kind == KIND_SPEECH
        assert intent.tools == (SPEECH_TOOL,)

    def test_drawing_verbs_beat_technical_nouns(self):
        # "diagramme d'architecture" is technical, but "dessine" names the medium.
        intent = detect_media_intent("dessine un schema d architecture")
        assert intent is not None
        assert intent.kind == KIND_IMAGE
        assert intent.explicit is True


class TestFullVideo:
    def test_video_plus_music_and_voice_is_one_deliverable(self):
        intent = detect_media_intent(
            "cree une video complete avec musique et voix off"
        )
        assert intent is not None
        assert intent.kind == KIND_FULL_VIDEO
        assert VIDEO_TOOL in intent.tools
        assert MUSIC_TOOL in intent.tools
        assert SPEECH_TOOL in intent.tools
        # The assembly step needs the montage toolchain, not just the generators.
        assert MONTAGE_TOOL in intent.tools
        assert intent.tools[-1] == MONTAGE_TOOL

    def test_images_join_the_pipeline_when_asked(self):
        intent = detect_media_intent(
            "genere une video avec des images et de la musique"
        )
        assert intent is not None
        assert intent.kind == KIND_FULL_VIDEO
        assert IMAGE_TOOL in intent.tools

    def test_music_is_added_when_only_footage_is_named(self):
        intent = detect_media_intent("fais un montage video pour le produit")
        assert intent is not None
        assert intent.kind == KIND_FULL_VIDEO
        assert MUSIC_TOOL in intent.tools

    def test_multi_asset_turns_do_not_pin_the_first_tool(self):
        intent = detect_media_intent("cree une video avec voix off")
        assert intent is not None
        assert intent.kind == KIND_FULL_VIDEO
        assert intent.should_force_tool() is False

    def test_tools_are_unique_and_ordered(self):
        intent = detect_media_intent(
            "genere une video avec images, musique et voix off"
        )
        assert intent is not None
        assert len(intent.tools) == len(set(intent.tools))
        assert intent.tools[0] == VIDEO_TOOL


class TestNonMediaRequests:
    @pytest.mark.parametrize(
        "text",
        [
            "genere un fichier de config",
            "cree une fonction python",
            "fais moi un script de deploiement",
            "genere un rapport de vente",
            "cree une api rest",
            "genere un readme",
            "fais une presentation en slides",
            "cree un tableau csv",
            "generate a test suite",
            "create a database migration",
        ],
    )
    def test_software_and_documents_are_left_alone(self, text):
        assert detect_media_intent(text) is None, text

    def test_empty_input(self):
        assert detect_media_intent("") is None
        assert detect_media_intent(None) is None
        assert detect_media_intent("   ") is None


def _defs(*names: str) -> list[dict]:
    return [{"type": "function", "function": {"name": name}} for name in names]


class TestForcedToolChoice:
    def test_pins_the_generator_on_the_first_call(self):
        choice = forced_tool_choice(IMAGE_TOOL, _defs(IMAGE_TOOL, "read_file"), [])
        assert choice == {"type": "function", "function": {"name": IMAGE_TOOL}}

    def test_never_pins_a_tool_the_build_does_not_ship(self):
        # generate_speech is routed before the tool exists; that must stay inert
        # rather than send an unsatisfiable tool_choice to the provider.
        assert forced_tool_choice(SPEECH_TOOL, _defs(IMAGE_TOOL), []) is None

    def test_releases_the_pin_once_a_tool_has_run(self):
        messages = [
            {"role": "user", "content": "genere une image de chat"},
            {"role": "assistant", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "/tmp/cat.png"},
        ]
        assert forced_tool_choice(IMAGE_TOOL, _defs(IMAGE_TOOL), messages) is None

    def test_releases_the_pin_on_an_assistant_tool_call_alone(self):
        messages = [{"role": "assistant", "tool_calls": [{"id": "1"}]}]
        assert forced_tool_choice(IMAGE_TOOL, _defs(IMAGE_TOOL), messages) is None

    def test_plain_history_still_pins(self):
        messages = [
            {"role": "user", "content": "salut"},
            {"role": "assistant", "content": "bonjour"},
            {"role": "user", "content": "genere une image de chat"},
        ]
        assert forced_tool_choice(IMAGE_TOOL, _defs(IMAGE_TOOL), messages) is not None

    def test_no_tool_and_no_definitions(self):
        assert forced_tool_choice(None, _defs(IMAGE_TOOL), []) is None
        assert forced_tool_choice(IMAGE_TOOL, None, []) is None
        assert forced_tool_choice(IMAGE_TOOL, [], []) is None

    def test_malformed_definitions_are_ignored(self):
        assert forced_tool_choice(IMAGE_TOOL, ["nope", {}, None], []) is None
        assert forced_tool_choice(IMAGE_TOOL, [{"name": IMAGE_TOOL}], []) is not None


class TestAgentLoopWiring:
    """The routing has to survive the composer mode the user happened to be in."""

    def test_ask_mode_keeps_the_image_generator_for_a_media_request(self):
        from navin.agent.loop import AgentLoop

        denied = AgentLoop._denied_tools(
            None, {"composer_mode": "ask"}, "genere un chat qui mange une banane"
        )
        assert IMAGE_TOOL not in denied
        # Unrelated heavy tools stay denied in Ask.
        assert "scrape" in denied

    def test_ask_mode_still_denies_media_for_a_non_media_request(self):
        from navin.agent.loop import AgentLoop

        denied = AgentLoop._denied_tools(
            None, {"composer_mode": "ask"}, "explique moi ce fichier"
        )
        assert IMAGE_TOOL in denied

    def test_plan_mode_keeps_the_video_generator(self):
        from navin.agent.loop import AgentLoop

        denied = AgentLoop._denied_tools(
            None, {"composer_mode": "plan"}, "genere une video de 10 secondes"
        )
        assert VIDEO_TOOL not in denied

    def test_workflow_denied_tools_are_denied(self):
        from navin.agent.loop import AgentLoop

        denied = AgentLoop._denied_tools(
            None,
            {"denied_tools": ["generate_video", "generate_music"]},
            "cree une landing page moderne",
        )
        assert "generate_video" in denied
        assert "generate_music" in denied

    def test_forge_allowlist_lifts_video_when_asked(self):
        from navin.agent.loop import AgentLoop
        from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS

        allowed = AgentLoop._allowed_tools(
            None,
            {"allowed_tools": sorted(CODE_BUILD_ALLOWED_TOOLS)},
            "genere une video de demo de 10 secondes",
        )
        assert allowed is not None
        assert VIDEO_TOOL in allowed
        assert IMAGE_TOOL in allowed
        assert "tenders" not in allowed

    def test_forge_allowlist_keeps_video_out_without_a_media_ask(self):
        from navin.agent.loop import AgentLoop
        from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS

        allowed = AgentLoop._allowed_tools(
            None,
            {"allowed_tools": sorted(CODE_BUILD_ALLOWED_TOOLS)},
            "cree une landing page moderne",
        )
        assert allowed is not None
        assert VIDEO_TOOL not in allowed
        assert IMAGE_TOOL in allowed

    def test_session_allowlist_survives_a_plain_follow_up(self):
        from navin.agent.loop import AgentLoop
        from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS

        allowed = AgentLoop._allowed_tools(
            None,
            {},
            "continue",
            {"allowed_tools": sorted(CODE_BUILD_ALLOWED_TOOLS)},
        )
        assert allowed is not None
        assert "read_file" in allowed
        assert "tenders" not in allowed

    def test_studio_slash_does_not_reuse_the_forge_allowlist(self):
        from navin.agent.loop import AgentLoop
        from navin.agent.tool_surface import CODE_BUILD_ALLOWED_TOOLS

        allowed = AgentLoop._allowed_tools(
            None,
            {"original_command": "/studio"},
            "make a deck",
            {"allowed_tools": sorted(CODE_BUILD_ALLOWED_TOOLS)},
        )
        assert allowed is None

    def test_an_explicit_media_request_lifts_a_workflow_denial(self):
        from navin.agent.loop import AgentLoop

        denied = AgentLoop._denied_tools(
            None,
            {"denied_tools": ["generate_video"]},
            "genere une video de demo de 10 secondes",
        )
        assert VIDEO_TOOL not in denied

    def test_code_module_denylist_still_wins(self):
        from navin.agent.loop import AgentLoop
        from navin.command.modules import (
            CODE_DENIED_TOOLS,
            PRODUCT_MODULE_METADATA_KEY,
        )

        if not CODE_DENIED_TOOLS:
            pytest.skip("Code module has no denylist in this build")
        denied = AgentLoop._denied_tools(
            None,
            {PRODUCT_MODULE_METADATA_KEY: "code", "composer_mode": "agent"},
            "genere une image de chat",
        )
        assert CODE_DENIED_TOOLS <= denied

    def test_forced_tool_is_the_generator_for_single_medium(self):
        from navin.agent.loop import AgentLoop

        assert (
            AgentLoop._forced_media_tool("genere un chat qui mange une banane")
            == IMAGE_TOOL
        )
        assert AgentLoop._forced_media_tool("genere une musique douce") == MUSIC_TOOL

    def test_no_forced_tool_for_multi_asset_or_non_media(self):
        from navin.agent.loop import AgentLoop

        assert AgentLoop._forced_media_tool("cree une video avec voix off") is None
        assert AgentLoop._forced_media_tool("genere un fichier de config") is None
        assert AgentLoop._forced_media_tool(None) is None


class TestHelpers:
    def test_media_tools_for_text(self):
        assert media_tools_for_text("genere un chat qui mange une banane") == {
            IMAGE_TOOL
        }
        assert media_tools_for_text("genere un fichier") == frozenset()

    def test_the_primary_tool_is_the_one_to_pin(self):
        intent = detect_media_intent("genere une image de chat")
        assert intent is not None
        assert intent.primary_tool == IMAGE_TOOL

    def test_an_empty_tool_list_has_no_primary(self):
        from navin.agent.media_intent import MediaIntent

        assert MediaIntent(KIND_IMAGE, (), False, ()).primary_tool == ""
