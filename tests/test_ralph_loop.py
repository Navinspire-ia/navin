"""Ralph loop: argument parsing, state, Stop wiring, runner cap, commands."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from navin.agent import ralph_loop as rl
from navin.agent import user_hooks

ROOT = Path(__file__).resolve().parents[1]


def _stop(ws: Path, text: str, session: str = "s1", subagent: bool = False) -> str | None:
    return asyncio.run(user_hooks.stop_hook_reason(ws, session, text, stop_hook_active=False, subagent=subagent))


@pytest.fixture(autouse=True)
def _no_extra(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(user_hooks, "load_hooks", lambda *a, **k: [])


def test_parse_args_and_errors():
    st = rl.parse_args('"Fix the bug" --max-iterations 5 --completion-promise "ALL  DONE"')
    assert (st.prompt, st.max_iterations, st.completion_promise) == ("Fix the bug", 5, "ALL DONE")
    assert rl.parse_args("build an api now").prompt == "build an api now"
    for bad, word in [("", "No prompt"), ("x --max-iterations", "needs a number"),
                      ("x --max-iterations -3", "whole number"), ("x --max-iterations 1.5", "whole number"),
                      ("x --completion-promise", "needs a text"), ('"unclosed', "quotes")]:
        with pytest.raises(rl.RalphError, match=word):
            rl.parse_args(bad)


def test_start_status_cancel(tmp_path):
    out = rl.start(tmp_path, '"Do it" --max-iterations 3 --completion-promise DONE')
    assert "<promise>DONE</promise>" in out and "Do it" in out
    assert rl.state_path(tmp_path).is_file()
    assert "iteration 1/3" in rl.status(tmp_path)
    assert "iteration 1/3, promise: DONE" in user_hooks.hooks_summary(tmp_path)
    assert "iteration 1" in rl.cancel(tmp_path)
    assert not rl.state_path(tmp_path).exists()
    assert rl.status(tmp_path) == "No active Ralph loop."
    assert "ralph" not in user_hooks.hooks_summary(tmp_path)


def test_stop_refeeds_prompt_with_iteration(tmp_path):
    rl.start(tmp_path, '"Polish the docs" --max-iterations 4 --completion-promise "SHIPPED"')
    reason = _stop(tmp_path, "did some work")
    assert reason and "Iteration 2 of 4" in reason and "Polish the docs" in reason
    assert "<promise>SHIPPED</promise>" in reason
    assert rl.read_state(tmp_path).iteration == 2
    outcome = asyncio.run(user_hooks.run_hooks("Stop", {"session_id": "s1", "last_assistant_message": "x"}, tmp_path))
    assert outcome.blocked and any("iteration 3/4" in m for m in outcome.system_messages)


def test_promise_exact_match_only(tmp_path):
    rl.start(tmp_path, '"t" --max-iterations 10 --completion-promise "ALL GREEN"')
    assert _stop(tmp_path, "<promise>ALL GREE</promise>")
    assert _stop(tmp_path, "ALL GREEN without tags")
    assert rl.state_path(tmp_path).is_file()
    reason, notice = rl.stop_reason(tmp_path, "s1", "done\n<promise> ALL\nGREEN </promise>")
    assert reason is None and "finished at iteration 3" in notice
    assert not rl.state_path(tmp_path).exists()


def test_max_iterations_ends(tmp_path):
    rl.start(tmp_path, '"t" --max-iterations 2')
    assert _stop(tmp_path, "a")
    reason, notice = rl.stop_reason(tmp_path, "s1", "b")
    assert reason is None and "max iterations (2)" in notice
    assert not rl.state_path(tmp_path).exists()


def test_other_session_and_subagent_untouched(tmp_path):
    rl.start(tmp_path, '"t" --max-iterations 5', session="mine")
    assert _stop(tmp_path, "x", session="other") is None
    assert _stop(tmp_path, "x", session="mine", subagent=True) is None
    assert rl.read_state(tmp_path).iteration == 1
    assert _stop(tmp_path, "x", session="mine")


def test_corrupt_state_cleared(tmp_path):
    path = rl.state_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("---\niteration: abc\nmax_iterations: 3\n---\n\nhi\n")
    outcome = asyncio.run(user_hooks.run_hooks("Stop", {"session_id": "s1"}, tmp_path))
    assert not outcome.blocked and any("Ralph loop stopped" in m for m in outcome.system_messages)
    assert not path.exists()


def test_already_blocked_waits(tmp_path):
    rl.start(tmp_path, '"t" --max-iterations 5')
    outcome = user_hooks.HookOutcome(blocked=True)
    user_hooks._apply_ralph_loop(outcome, "Stop", {"session_id": "s1"}, tmp_path)
    assert rl.read_state(tmp_path).iteration == 1


def test_runner_cap_raised_while_active(tmp_path):
    from navin.agent import runner

    spec = SimpleNamespace(workspace=tmp_path, session_key="s1", user_hook_scope="main")
    assert runner._stop_hook_round_cap(spec) == runner._MAX_STOP_HOOK_ROUNDS
    rl.start(tmp_path, '"t" --max-iterations 10')
    assert runner._stop_hook_round_cap(spec) == 11
    assert runner._stop_hook_round_cap(SimpleNamespace(**{**vars(spec), "user_hook_scope": "subagent"})) == 3
    rl.start(tmp_path, '"t"')
    assert runner._stop_hook_round_cap(spec) == rl.UNLIMITED_CEILING
    rl.cancel(tmp_path)
    assert runner._stop_hook_round_cap(spec) == runner._MAX_STOP_HOOK_ROUNDS


def test_cli(tmp_path, capsys):
    assert rl.main(["start", str(tmp_path), "fix", "it", "--max-iterations", "x"]) == 1
    assert "whole number" in capsys.readouterr().err
    assert rl.main(["start", str(tmp_path), "fix it", "--max-iterations", "2"]) == 0
    assert rl.main(["status", str(tmp_path)]) == 0
    assert "iteration 1/2" in capsys.readouterr().out
    assert rl.main(["cancel", str(tmp_path)]) == 0
    assert "iteration 1" in capsys.readouterr().out


def test_commands_and_plugin():
    cmds = ROOT / "navin" / "builtin_commands"
    loop = (cmds / "ralph-loop.md").read_text()
    assert "navin.agent.ralph_loop start" in loop and "$ARGUMENTS" in loop and "exec" in loop
    assert "navin.agent.ralph_loop cancel" in (cmds / "cancel-ralph.md").read_text()
    assert "--max-iterations" in (cmds / "ralph-help.md").read_text()
    manifest = json.loads((ROOT / "navin" / "builtin_plugins" / "ralph-loop" / "plugin.json").read_text())
    for name in manifest["commands"]:
        assert (cmds / f"{name}.md").is_file()
