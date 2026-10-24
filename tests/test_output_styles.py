import subprocess
import sys
from pathlib import Path

import pytest

from navin.agent import output_styles as os_mod
from navin.utils.proc import no_window_kwargs

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    ws = tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(os_mod.ENV_VAR, raising=False)
    return ws, home


def _style(folder: Path, name: str, body: str, desc: str = "d") -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{name}.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n", encoding="utf-8")


def test_builtins(env):
    ws, _ = env
    names = {s.name for s in os_mod.list_styles(ws)}
    assert {"default", "explanatory", "learning"} <= names
    assert "Insight" in os_mod.get_style(ws, "explanatory").instructions
    assert "TODO(human)" in os_mod.get_style(ws, "learning").instructions
    assert os_mod.active_style(ws).name == "default"
    assert os_mod.prompt_section(ws) == ""


def test_custom_precedence(env):
    ws, home = env
    _style(home / ".navin" / "output-styles", "mine", "PERSONAL")
    assert "PERSONAL" in os_mod.get_style(ws, "mine").instructions
    _style(ws / ".navin" / "output-styles", "mine", "PROJECT")
    assert os_mod.get_style(ws, "mine").source == "project"
    _style(ws / ".navin" / "output-styles", "learning", "OVERRIDE")
    assert "OVERRIDE" in os_mod.get_style(ws, "learning").instructions


def test_selection_order(env, monkeypatch):
    ws, home = env
    (home / ".navin").mkdir()
    (home / ".navin" / "output-style").write_text("explanatory\n")
    assert os_mod.active_style(ws).name == "explanatory"
    os_mod.set_style(ws, "learning")
    assert os_mod.active_style(ws).name == "learning"
    monkeypatch.setenv(os_mod.ENV_VAR, "default")
    assert os_mod.active_style(ws).name == "default"


def test_set_clear_and_unknown(env):
    ws, home = env
    os_mod.set_style(ws, "learning", scope="personal")
    assert (home / ".navin" / "output-style").read_text().strip() == "learning"
    os_mod.set_style(ws, "explanatory")
    assert os_mod.prompt_section(ws).startswith("# Output Style: explanatory")
    assert os_mod.clear_style(ws)
    assert os_mod.active_style(ws).name == "learning"
    assert os_mod.clear_style(ws, scope="personal")
    assert not os_mod.clear_style(ws)
    with pytest.raises(ValueError):
        os_mod.set_style(ws, "nope")
    (ws / ".navin" / "output-style.local").write_text("nope\n")
    assert os_mod.active_style(ws).name == "default"
    assert os_mod.prompt_section(ws) == ""


def test_system_prompt_injection(env):
    from navin.agent.context import ContextBuilder

    ws, _ = env
    assert "# Output Style:" not in ContextBuilder(ws).build_system_prompt(workspace=ws)
    os_mod.set_style(ws, "learning")
    first = ContextBuilder(ws).build_system_prompt(workspace=ws)
    assert "# Output Style: learning" in first and "TODO(human)" in first
    assert first == ContextBuilder(ws).build_system_prompt(workspace=ws)


def test_command_and_cli(env):
    ws, home = env
    cmd = ROOT / "navin" / "builtin_commands" / "output-style.md"
    text = cmd.read_text(encoding="utf-8")
    assert "python -m navin.agent.output_styles $ARGUMENTS" in text and "allowed-tools: exec" in text
    run_env = {"HOME": str(home), "USERPROFILE": str(home), "PATH": "", "PYTHONPATH": str(ROOT)}

    def run(*a):
        return subprocess.run(
            [sys.executable, "-m", "navin.agent.output_styles", *a, "--workspace", str(ws)],
            capture_output=True, text=True, env=run_env, cwd=str(ROOT), timeout=60, **no_window_kwargs(),
        )

    out = run().stdout
    assert "* default" in out and "learning" in out
    assert run("learning").returncode == 0
    assert "* learning" in run("list").stdout
    assert run("bogus").returncode == 1
    run("off")
    assert "* default" in run().stdout
