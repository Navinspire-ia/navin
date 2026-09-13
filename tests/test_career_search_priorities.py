# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

from collections import Counter
from unittest.mock import patch

import pytest

from navin.career.errors import CareerError
from navin.career.prospecting import handle_prospecting, prospecting_snapshot
from navin.career.search_plan import plan_search, role_shares
from navin.career.store import CareerStore

ROLES = ["Ingénieur IA générative", "Data engineer", "Développeur full stack"]
WEIGHTS = dict(zip(ROLES, [50, 20, 20]))


def configured_store(tmp_path, **extra):
    store = CareerStore(tmp_path / "career")
    store.save_secret("CAREER_SERPAPI_KEY", "test-search-key")
    handle_prospecting(store, "prospecting_config", {"criteria": {
        "roles": ROLES, "profile_roles": ROLES, "role_priorities": WEIGHTS, "countries": ["FR", "AE", "SA", "MA", "OM", "QA", "BH", "KW", "GB", "US"],
        "sources": ["linkedin", "google_jobs", "remotive"], "platforms": ["linkedin"],
        "skills": ["Python", "RAG", "SQL", "React", "Atlas"], "profile_skills": ["Python", "RAG", "SQL", "React", "Atlas"],
        "role_skills": dict(zip(ROLES, [["Python", "RAG"], ["Python", "SQL"], ["React"]])), **extra,
    }})
    return store


def test_weighted_searches_reach_both_sides_and_keep_role_skills_separate(tmp_path):
    store = configured_store(tmp_path)
    # 18 calls on each side allow exactly 10 / 4 / 4 at the requested 50/20/20.
    with patch("navin.career.prospecting.MAX_TASKS", 36), \
         patch("navin.career.prospecting._mission_source", return_value=[]) as missions, \
         patch("navin.career.prospecting._candidates", return_value=[]) as candidates:
        handle_prospecting(store, "prospecting_search", {})
    for searches in (missions, candidates):
        assert Counter(call.args[2]["roles"][0] for call in searches.call_args_list) == dict(zip(ROLES, [10, 4, 4]))
        for call in searches.call_args_list:
            criteria = call.args[2]
            assert len(criteria["roles"]) == 1
            assert "Atlas" in criteria["skills"]
            if criteria["roles"] == [ROLES[0]]:
                assert set(criteria["skills"]) <= {"Python", "RAG", "Atlas"}
            if criteria["roles"] == [ROLES[2]]:
                assert set(criteria["skills"]) <= {"React", "Atlas"}
    statuses = prospecting_snapshot(store)["last_run"]["sources"]
    assert len({row["source"] for row in statuses}) == 36
    assert {row["role"] for row in statuses} == set(ROLES)


def test_daily_rotation_survives_reload_without_starving_small_priorities(tmp_path):
    store = configured_store(tmp_path, mode="missions", role_priorities=dict(zip(ROLES, [98, 1, 1])))
    with patch("navin.career.prospecting.MAX_TASKS", 1), patch("navin.career.prospecting._mission_source", return_value=[]) as missions:
        for _ in range(100):
            handle_prospecting(CareerStore(store.root), "prospecting_search", {})
    counts = Counter(call.args[2]["roles"][0] for call in missions.call_args_list)
    assert counts == dict(zip(ROLES, [98, 1, 1]))
    genai_calls = [call for call in missions.call_args_list if call.args[2]["roles"] == [ROLES[0]]]
    assert {call.args[1] for call in genai_calls} == {"linkedin", "google_jobs", "remotive"}
    assert len({call.args[2]["countries"][0] for call in genai_calls}) == 10


def test_paused_role_is_excluded_but_can_still_match_a_specific_offer(tmp_path):
    store = configured_store(tmp_path, role_priorities=dict(zip(ROLES, [100, 0, 0])))
    with patch("navin.career.prospecting._mission_source", return_value=[]) as missions, patch("navin.career.prospecting._candidates", return_value=[]) as profiles:
        handle_prospecting(store, "prospecting_search", {})
        assert all(call.args[2]["roles"] == [ROLES[0]] for call in missions.call_args_list + profiles.call_args_list)
        schedule = prospecting_snapshot(store)["search_schedule"]
        store.upsert_opportunities([{"id": "fullstack-job", "title": ROLES[2], "stack": ["React", "SQL"], "country": "FR", "url": "https://example.com/job"}])
        profiles.reset_mock()
        handle_prospecting(store, "prospecting_search", {"id": "fullstack-job"})
        assert profiles.call_count > 0
        assert all(call.args[2]["roles"] == [ROLES[2]] and call.args[2]["skills"] == ["React", "SQL"] for call in profiles.call_args_list)
        assert prospecting_snapshot(store)["search_schedule"] == schedule


def test_priorities_are_persisted_pruned_and_validated_in_english(tmp_path):
    store = configured_store(tmp_path)
    assert prospecting_snapshot(CareerStore(store.root))["criteria"]["role_priorities"] == WEIGHTS
    for weights in ([], {ROLES[0]: -1}, {ROLES[0]: 101}, {ROLES[0]: float("nan")}, {ROLES[0]: True}, {ROLES[0]: "50"}):
        with pytest.raises(CareerError, match="Invalid role priorit"):
            handle_prospecting(store, "prospecting_config", {"criteria": {"role_priorities": weights}})
        assert prospecting_snapshot(store)["criteria"]["role_priorities"] == WEIGHTS
    handle_prospecting(store, "prospecting_config", {"criteria": {"roles": [ROLES[0]], "profile_roles": [ROLES[0]]}})
    assert prospecting_snapshot(store)["criteria"]["role_priorities"] == {ROLES[0]: 50}
    assert list(prospecting_snapshot(store)["criteria"]["role_skills"]) == [ROLES[0]]
    handle_prospecting(store, "prospecting_config", {"criteria": {"role_priorities": {ROLES[0]: 0}}})
    with pytest.raises(CareerError, match="All selected roles are paused"):
        handle_prospecting(store, "prospecting_search", {})


def test_default_remaining_and_normalized_shares():
    assert role_shares(ROLES, {ROLES[0]: 50}) == dict(zip(ROLES, [.5, .25, .25]))
    assert role_shares(ROLES, WEIGHTS)[ROLES[0]] == pytest.approx(5 / 9)
    assert role_shares(ROLES, {}) == pytest.approx(dict.fromkeys(ROLES, 1 / 3))
    assert role_shares(ROLES, dict.fromkeys(ROLES, 0)) == dict.fromkeys(ROLES, 0)


def test_exhausted_sources_release_capacity_without_duplicate_calls():
    tasks = [{"id": "limited", "group": "A", "weight": .99}] + [
        {"id": f"other-{index}", "group": "B", "weight": .01} for index in range(10)]
    selected, _ = plan_search(tasks, {}, 8)
    assert selected[0]["id"] == "limited"
    assert len(selected) == len({task["id"] for task in selected}) == 8
