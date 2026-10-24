import json

import pytest

from navin.board.ledger import MissionLedgerStore, checkpoint_id_for_version
from navin.board.store import BoardError
from navin.board.timeline import (
    build_timeline,
    compare_strategies,
    diff_versions,
    last_coherent_state,
    replay_step,
    restore_mission,
)
from navin.webui import checkpoints_api


def test_empty(tmp_path):
    out = build_timeline(tmp_path)
    assert out["events"] == [] and out["last_coherent"] is None


def test_checkpoint_id_and_timeline(tmp_path):
    st = MissionLedgerStore(tmp_path)
    led = st.create(goal="g", acceptance_criteria=["a"]) if "acceptance_criteria" in st.create.__code__.co_varnames else st.create(goal="g")
    st.bump_version(led, reason="replan x")
    st.save(led)
    out = build_timeline(tmp_path)
    assert [e["checkpoint_id"] for e in out["events"] if e["kind"] == "decision"][-1] == checkpoint_id_for_version(2)
    assert out["last_coherent"]["version"] == 2


def test_coherent_when_proven():
    led = {"history": [{"version": 3}], "acceptance_criteria": ["a"],
           "criteria_evidence": {"a": "pytest -q -> 3 passed"}}
    r = last_coherent_state(led)
    assert r["all_criteria_proven"] and r["checkpoint_id"] == "ckpt-v3"
    led["criteria_evidence"] = {}
    assert not last_coherent_state(led)["all_criteria_proven"]


def _two_versions(tmp_path):
    st = MissionLedgerStore(tmp_path)
    led = st.create(goal="g1")
    led["facts"] = ["f1"]
    st.save(led)
    led = st.load()
    led["goal"] = "g2"
    led["acceptance_criteria"] = ["c1"]
    st.bump_version(led, reason="replan")
    st.save(led)
    return st


def test_restore_creates_new_version_and_keeps_history(tmp_path):
    st = _two_versions(tmp_path)
    out = restore_mission(tmp_path, 1, reason="back to v1")
    led = st.load()
    assert out["restored_from"] == 1 and led["version"] == 3
    assert led["goal"] == "g1" and led["acceptance_criteria"] == []
    assert [h["version"] for h in led["history"]] == [1, 2, 3]
    assert "back to v1" in led["history"][-1]["reason"]


def test_restore_refuses_version_without_snapshot(tmp_path):
    st = _two_versions(tmp_path)
    raw = json.loads(st.path.read_text())
    raw["snapshots"].pop("1")
    st.path.write_text(json.dumps(raw))
    with pytest.raises(BoardError, match="no snapshot"):
        restore_mission(tmp_path, 1)
    assert st.load()["version"] == 2


def test_restore_coherent_without_proven_version(tmp_path):
    _two_versions(tmp_path)
    with pytest.raises(BoardError, match="coherent"):
        restore_mission(tmp_path, "coherent")


def test_diff_versions(tmp_path):
    _two_versions(tmp_path)
    d = diff_versions(tmp_path, 1, 2)
    assert d["goal"] == {"from": "g1", "to": "g2"}
    assert d["criteria"]["added"] == ["c1"]
    with pytest.raises(BoardError):
        diff_versions(tmp_path, 1, 9)


def test_board_actions(tmp_path):
    from navin.agent.tools.board import BoardTool

    _two_versions(tmp_path)
    tool = BoardTool.__new__(BoardTool)
    store = type("S", (), {"project_path": tmp_path})()
    common = dict(task_id=None, who="tester", goal=None, constraints=None, facts=None,
                  missing_info=None, acceptance_criteria=None, step_ids=None, ok=None,
                  progress_flag=None, fingerprint=None, tokens=None, evidence=None)
    tl = json.loads(tool._dispatch_ledger(store, action="ledger_timeline", reason=None, text=None, **common))
    assert tl["mission"] and all(e["restorable"] for e in tl["events"] if e["kind"] == "decision")
    d = json.loads(tool._dispatch_ledger(store, action="ledger_diff", reason=None, text="1..2", **common))
    assert d["goal"]["to"] == "g2"
    r = json.loads(tool._dispatch_ledger(store, action="ledger_restore", reason="why", text="1", **common))
    assert r["version"] == 3
    bad = tool._dispatch_ledger(store, action="ledger_restore", reason=None, text="99", **common)
    assert bad.startswith("Error:")


def test_api_functions(tmp_path):
    _two_versions(tmp_path)
    assert checkpoints_api.mission_diff(tmp_path, "1", "2")["facts"]["removed"] == []
    assert checkpoints_api.mission_restore(tmp_path, "1", "api")["version"] == 3
    with pytest.raises(checkpoints_api.CheckpointError):
        checkpoints_api.mission_restore(tmp_path, "42")


def _mission_with_steps(tmp_path):
    """v1: plan a -> b -> c; a done, b failed twice; v2: replan b with a new title."""
    st = MissionLedgerStore(tmp_path)
    led = st.create(
        goal="ship",
        acceptance_criteria=["tests pass", "build green"],
        steps=[
            {"id": "a", "title": "model", "status": "completed"},
            {"id": "b", "title": "api v1", "status": "planned", "depends_on": ["a"], "retry_count": 2},
            {"id": "c", "title": "ui", "status": "planned", "depends_on": ["b"]},
        ],
    )
    led["criteria_evidence"] = {"tests pass": "1: pytest -q -> 3 passed"}
    st.save(led)
    led = st.load()
    st.local_replan(led, "b", reason="try api v2", new_steps=[{"id": "b", "title": "api v2", "depends_on": ["a"]}])
    st.save(led)
    led = st.load()
    for step in led["steps"]:
        step["status"] = "completed"
    led["criteria_evidence"] = {"tests pass": "1: pytest -q -> 3 passed", "build green": "2: npm run build -> exit 0"}
    st.save(led)
    return st


def test_outcomes_follow_each_version(tmp_path):
    st = _mission_with_steps(tmp_path)
    out = st.load()["outcomes"]
    assert out["1"]["steps_completed"] == 1 and out["1"]["retries"] == 2
    assert out["1"]["criteria_proven"] == 1 and out["1"]["criteria_total"] == 2
    assert out["2"]["steps_completed"] == 3 and out["2"]["criteria_proven"] == 2


def test_replay_step_resets_it_and_dependents_only(tmp_path):
    st = _mission_with_steps(tmp_path)
    r = replay_step(tmp_path, "b", reason="flaky api")
    led = st.load()
    status = {s["id"]: s["status"] for s in led["steps"]}
    assert status == {"a": "completed", "b": "planned", "c": "planned"}
    assert r["version"] == 3 and sorted(r["invalidated"]) == ["b", "c"]
    assert "replay b" in led["history"][-1]["reason"] and "replay:b" in led["history"][-1]["changes"]


def test_replay_step_from_an_older_version_uses_its_definition(tmp_path):
    st = _mission_with_steps(tmp_path)
    replay_step(tmp_path, "b", from_version=1)
    b = next(s for s in st.load()["steps"] if s["id"] == "b")
    assert b["title"] == "api v1" and b["status"] == "planned" and b["retry_count"] == 0
    with pytest.raises(BoardError, match="not in version"):
        replay_step(tmp_path, "zzz", from_version=1)
    with pytest.raises(BoardError, match="no snapshot"):
        replay_step(tmp_path, "b", from_version=42)
    with pytest.raises(BoardError, match="unknown step"):
        replay_step(tmp_path, "zzz")


def test_compare_strategies_ranks_the_plan_that_proved_more(tmp_path):
    _mission_with_steps(tmp_path)
    c = compare_strategies(tmp_path, 1, 2)
    assert c["better"] == 2 and c["reasons"][0].startswith("criteria proven")
    assert c["a"]["measured"] == "live" and c["a"]["decision"] == "ledger created"
    assert c["b"]["ended_at"] is None and c["a"]["ended_at"] == c["b"]["started_at"]
    with pytest.raises(BoardError, match="not in the mission history"):
        compare_strategies(tmp_path, 1, 9)


def test_compare_falls_back_to_snapshot_for_old_registries(tmp_path):
    st = _mission_with_steps(tmp_path)
    raw = json.loads(st.path.read_text())
    raw.pop("outcomes")
    st.path.write_text(json.dumps(raw))
    c = compare_strategies(tmp_path, 1, 1)
    assert c["a"]["measured"] == "snapshot" and c["better"] is None


def test_timeline_titles_board_activity_and_lists_steps(tmp_path):
    from navin.board.store import ProjectBoardStore

    _mission_with_steps(tmp_path)
    ProjectBoardStore(tmp_path).log_activity(kind="note", actor="navin", actor_type="agent", detail="pytest -q green")
    out = build_timeline(tmp_path)
    act = [e for e in out["events"] if e["version"] is None]
    assert act and act[-1]["title"] == "pytest -q green" and act[-1]["kind"] == "test"
    assert [s["id"] for s in out["steps"]] == ["a", "b", "c"]


def test_board_and_api_replay_compare(tmp_path):
    from navin.agent.tools.board import BoardTool

    _mission_with_steps(tmp_path)
    tool = BoardTool.__new__(BoardTool)
    store = type("S", (), {"project_path": tmp_path})()
    common = dict(who="tester", goal=None, constraints=None, facts=None,
                  missing_info=None, acceptance_criteria=None, step_ids=None, ok=None,
                  progress_flag=None, fingerprint=None, tokens=None, evidence=None)
    c = json.loads(tool._dispatch_ledger(store, action="ledger_compare", task_id=None, reason=None, text="1..2", **common))
    assert c["better"] == 2
    r = json.loads(tool._dispatch_ledger(store, action="ledger_replay", task_id="c", reason="r", text="v1", **common))
    assert r["replayed"] == "c" and r["from_version"] == 1
    assert tool._dispatch_ledger(store, action="ledger_replay", task_id=None, reason=None, text=None, **common).startswith("Error:")
    assert tool._dispatch_ledger(store, action="ledger_compare", task_id=None, reason=None, text="2", **common).startswith("Error:")
    assert checkpoints_api.mission_compare(tmp_path, "1", "2")["better"] == 2
    assert checkpoints_api.mission_replay(tmp_path, "b", "", "api")["invalidated"]
    with pytest.raises(checkpoints_api.CheckpointError):
        checkpoints_api.mission_replay(tmp_path, "b", "x")
