# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Demo / motion / promo video intents route to the premium recipe playbooks."""

from __future__ import annotations

from pathlib import Path

import pytest

from navin.agent.skill_routing import action_skill_names, skill_route_for_turn
from navin.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader


@pytest.mark.parametrize(
    "message",
    [
        "make a demo video of my app",
        "Fais une vidéo de démo de mon app",
        "create a promo clip for the launch",
        "we need a launch video",
        "storyboard a motion design teaser",
    ],
)
@pytest.mark.parametrize("module", [None, "montage", "marketing"])
def test_demo_video_intent_routes_to_motion_design(message, module):
    metadata = {"product_module": module} if module else {}
    route = skill_route_for_turn(metadata, message)
    assert route is not None
    assert route[1] == "demo-video"
    names = action_skill_names(*route)
    assert "montage-studio" in names
    assert "motion-design" in names


def test_unrelated_chat_stays_unrouted():
    assert skill_route_for_turn({}, "fix the product page so the video plays") is None
    assert skill_route_for_turn({}, "fix the login bug") is None


def test_translation_still_wins_inside_montage():
    assert skill_route_for_turn({"product_module": "montage"}, "translate my demo video") == ("montage", "localize")


def test_action_aliases():
    assert skill_route_for_turn({"product_module": "montage", "action": "motion_render"}, "go") == ("montage", "demo-video")
    assert skill_route_for_turn({"product_module": "marketing", "action": "promo"}, "go") == ("marketing", "demo-video")


def test_builtin_playbooks_exist_and_carry_the_recipe():
    loader = SkillsLoader(Path(BUILTIN_SKILLS_DIR).parent, builtin_skills_dir=Path(BUILTIN_SKILLS_DIR))
    names = {entry["name"] for entry in loader.list_skills(filter_unavailable=False)}
    assert {"motion-design", "montage-studio", "video-generation"} <= names
    motion = (Path(BUILTIN_SKILLS_DIR) / "motion-design" / "SKILL.md").read_text(encoding="utf-8")
    assert "first 2 seconds" in motion and "cta_end_card" in motion and "secret_ref" in motion
    montage = (Path(BUILTIN_SKILLS_DIR) / "montage-studio" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Premium demo video" in montage
    for action in ("motion_templates", "motion_render", "footage_polish"):
        assert action in montage
    video = (Path(BUILTIN_SKILLS_DIR) / "video-generation" / "SKILL.md").read_text(encoding="utf-8")
    assert "via `exec`" not in video and "ffmpeg -i" not in video
    assert "montage(action=assemble" in video
    for text in (motion, montage, video):
        assert "—" not in text and "–" not in text
