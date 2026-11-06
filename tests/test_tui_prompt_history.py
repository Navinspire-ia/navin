# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The prompts the Up and Down arrows recall survive leaving the CLI.

On 2026-11-06 a user who left a CLI and came back found none of the day's
prompts: every CLI saved the whole of tui.json from the copy it loaded when it
started, so the last one to save erased what the others had written. Covers:

- the prompts of a project come back after leaving and returning, in order;
- the arrows walk them, from the latest, and give the draft back;
- several CLIs, a stale one included, lose nothing of each other, prompts or
  preferences, and real processes appending together lose no line;
- a project recalls its own prompts, plus the old global list, moved once;
- a full log starts a new generation, a torn line is skipped and said;
- sending a prompt in the real app is what writes it.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from loguru import logger

from navin.config.loader import get_config_path, set_config_path
from navin.providers.base import LLMResponse
from navin.providers.factory import ProviderSnapshot
from navin.tui import prompt_history
from navin.tui.app import NavinApp
from navin.tui.prefs import TuiPrefs
from navin.tui.prompt_history import PromptHistory
from tests.test_tui_send_now_integration import (
    NoopHook,
    ScriptedProvider,
    _drain_runtime,
    make_config,
)

ROOT = Path(__file__).resolve().parents[1]
UP, DOWN = -1, 1


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A private ~/.navin: tui.json and prompt-history.jsonl live in tmp_path."""
    path = tmp_path / "tui.json"
    monkeypatch.setattr(TuiPrefs, "path", classmethod(lambda cls: path))
    return tmp_path


def _start_cli(project: Path) -> TuiPrefs:
    """What run_tui does when a CLI starts in ``project``."""
    prefs = TuiPrefs.load()
    prefs.load_history(project)
    return prefs


class _Composer:
    def __init__(self, text: str = "") -> None:
        self.text = text

    def set_text(self, text: str) -> None:
        self.text = text


class _Host:
    _history = NavinApp._history

    def __init__(self, prefs: TuiPrefs, draft: str = "") -> None:
        self.prefs = prefs
        self.composer = _Composer(draft)
        self._history_index: int | None = None
        self._history_draft = ""

    def press(self, direction: int) -> str:
        self._history(SimpleNamespace(direction=direction))
        return self.composer.text


def test_prompts_come_back_after_leaving_and_returning(home: Path) -> None:
    project = home / "app"
    first = _start_cli(project)
    for text in ("fix the login", "add tests", "add tests"):  # a repeat counts once
        first.remember(text, "cli:1")

    assert _start_cli(project).history == ["fix the login", "add tests"]


def test_the_arrows_walk_the_prompts_of_the_last_run_and_give_the_draft_back(home: Path) -> None:
    project = home / "app"
    before = _start_cli(project)
    for text in ("a", "b", "c"):
        before.remember(text, "cli:1")

    host = _Host(_start_cli(project), draft="half typed")

    assert [host.press(UP) for _ in range(4)] == ["c", "b", "a", "a"]  # stops at the oldest
    assert [host.press(DOWN) for _ in range(3)] == ["b", "c", "half typed"]
    assert host.press(DOWN) == "half typed"  # nothing newer than the draft


def test_a_cli_opened_hours_ago_does_not_erase_what_the_others_saved(home: Path) -> None:
    app, site = home / "app", home / "site"
    stale = _start_cli(app)  # opened hours ago, never restarted
    fresh = _start_cli(app)  # another CLI in the same project
    other = _start_cli(site)

    stale.remember("from the stale CLI", "cli:1")
    fresh.remember("from the fresh CLI", "cli:2")
    stale.remember("stale again", "cli:1")
    other.remember("another project", "cli:9")

    fresh.theme, fresh.theme_explicit = "light", True
    fresh.remember_session(app, "cli:2")
    assert fresh.save()
    other.remember_session(site, "cli:9")
    assert other.save()
    stale.remember_session(app, "cli:1")  # the stale copy still holds the old theme
    assert stale.save()

    back = _start_cli(app)
    assert back.history == ["from the stale CLI", "from the fresh CLI", "stale again"]
    assert back.theme == "light"  # not put back by the stale CLI
    assert back.session_for(site) == "cli:9"  # the other project's session kept
    assert back.session_for(app) == "cli:1"  # same project: the last one to save


def test_processes_appending_at_once_lose_no_prompt(home: Path) -> None:
    log = home / "prompt-history.jsonl"
    code = textwrap.dedent(
        """
        import sys
        from navin.tui.prompt_history import PromptHistory

        history = PromptHistory(sys.argv[1])
        for number in range(40):
            assert history.append("/work/app", "cli:" + sys.argv[2], f"process {sys.argv[2]} prompt {number} " + "x" * 300)
        """
    )
    workers = [subprocess.Popen([sys.executable, "-c", code, str(log), str(index)], cwd=ROOT) for index in range(4)]
    assert [worker.wait(timeout=180) for worker in workers] == [0, 0, 0, 0]

    texts = [json.loads(line)["text"] for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(texts) == len(set(texts)) == 160
    for index in range(4):
        numbers = [int(text.split()[3]) for text in texts if text.startswith(f"process {index} ")]
        assert numbers == list(range(40))  # each process keeps its own order


def test_a_project_recalls_its_own_prompts_and_the_old_list_moves_once(home: Path) -> None:
    (home / "tui.json").write_text(
        json.dumps({"theme": "light", "theme_explicit": True, "history": ["old one", "old two"]}),
        encoding="utf-8",
    )
    app, site = home / "app", home / "site"

    in_app = _start_cli(app)
    assert in_app.history == ["old one", "old two"]
    in_app.remember("app prompt", "cli:1")
    in_site = _start_cli(site)
    assert in_site.history == ["old one", "old two"]  # nothing of the app project
    in_site.remember("site prompt", "cli:2")

    assert _start_cli(app).history == ["old one", "old two", "app prompt"]
    assert _start_cli(site).history == ["old one", "old two", "site prompt"]
    saved = json.loads((home / "tui.json").read_text(encoding="utf-8"))
    assert "history" not in saved
    assert saved["theme"] == "light"
    rows = [json.loads(line) for line in (home / "prompt-history.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(1 for row in rows if row["folder"] == "") == 2  # moved once, not at each start


def test_the_old_list_stays_in_place_when_it_cannot_be_moved(home: Path, monkeypatch) -> None:
    (home / "tui.json").write_text(json.dumps({"history": ["keep me"]}), encoding="utf-8")
    monkeypatch.setattr(PromptHistory, "import_legacy", lambda self, prompts: 0)

    _start_cli(home / "app")

    assert json.loads((home / "tui.json").read_text(encoding="utf-8"))["history"] == ["keep me"]


def test_a_full_log_starts_a_new_generation(home: Path, monkeypatch) -> None:
    monkeypatch.setattr(prompt_history, "ROTATE_BYTES", 3000)
    store = PromptHistory(home / "prompt-history.jsonl")
    for number in range(60):
        assert store.append("/work/app", "cli:1", f"prompt {number:02d} " + "y" * 100)

    assert store.previous.exists()
    assert store.path.stat().st_size <= 3000 + 400  # never more than one prompt over
    recalled = store.recent("/work/app")
    numbers = [int(text.split()[1]) for text in recalled]
    assert recalled[-1].startswith("prompt 59")
    assert numbers == sorted(numbers)  # in order across both generations
    assert len(numbers) > 15  # the older generation is read too


def test_an_unreadable_line_is_skipped_and_said(home: Path) -> None:
    def entry(text: str) -> str:
        return json.dumps({"at": 1, "folder": "/x", "session": "", "text": text})

    log = home / "prompt-history.jsonl"
    log.write_bytes(
        b"\n".join(
            [
                entry("first").encode(),
                b'{"at": 2, "folder": "/x", "session": "", "te',  # cut by a CLI killed while it wrote
                b"[1, 2]",
                b"\xff\xfe not text",
                json.dumps({"folder": "/x"}).encode(),
                entry("second").encode(),
                b"",
            ]
        )
    )
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        recalled = PromptHistory(log).recent("/x")
    finally:
        logger.remove(sink)

    assert recalled == ["first", "second"]
    assert any("4 unreadable line(s) skipped" in message for message in messages), messages


def test_a_giant_paste_keeps_its_beginning(home: Path) -> None:
    store = PromptHistory(home / "prompt-history.jsonl")
    assert store.append("/x", "cli:1", "start " + "z" * (prompt_history.MAX_PROMPT_CHARS + 5000))

    [recalled] = store.recent("/x")

    assert recalled.startswith("start ")
    assert len(recalled) == prompt_history.MAX_PROMPT_CHARS


def test_saving_without_a_change_writes_nothing(home: Path) -> None:
    prefs = TuiPrefs.load()
    assert prefs.save()
    assert not (home / "tui.json").exists()

    prefs.theme, prefs.theme_explicit = "light", True
    assert prefs.save()
    written = (home / "tui.json").stat().st_mtime_ns
    assert json.loads((home / "tui.json").read_text(encoding="utf-8"))["theme"] == "light"

    assert prefs.save()
    assert (home / "tui.json").stat().st_mtime_ns == written


def test_a_corrupt_preferences_file_is_replaced_not_trusted(home: Path) -> None:
    (home / "tui.json").write_text("{not json", encoding="utf-8")
    prefs = TuiPrefs.load()
    assert prefs.theme == "navin"

    prefs.sidebar, prefs.sidebar_explicit = True, True
    assert prefs.save()

    assert TuiPrefs.load().sidebar is True


@contextmanager
def _engine_patches(provider: ScriptedProvider):
    def snapshot(*args, **kwargs):
        return ProviderSnapshot(provider, "test-send-now-tui", 128_000, ("test-send-now-tui",))

    with (
        patch("navin.providers.factory.build_provider_snapshot_allowing_unconfigured", snapshot),
        patch("navin.providers.factory.load_provider_snapshot_allowing_unconfigured", snapshot),
        patch("navin.agent.skills._home_skill_dirs", return_value=[]),
        patch("navin.agent.loop.AgentLoop._connect_mcp", new_callable=AsyncMock),
        patch("navin.optional_live.live_modules_available", return_value=False),
        patch("navin.tui.app.live_modules_available", return_value=False),
        patch("navin.cron.service.CronService"),
        patch("navin.cron.spend.CronSpendHook", NoopHook),
        patch("navin.webui.token_usage.TokenUsageHook", NoopHook),
        patch("navin.update.notice.update_notice", return_value=""),
    ):
        yield


def test_a_prompt_sent_in_the_app_is_recalled_by_the_next_cli(home: Path) -> None:
    async def run() -> None:
        project = home / "project"
        project.mkdir()
        old_config_path = get_config_path()
        set_config_path(home / "config.json")
        provider = ScriptedProvider([LLMResponse(content="Done.")])
        try:
            with _engine_patches(provider):
                # First run: the user sends a prompt, then leaves.
                config = make_config(project)
                first = NavinApp(config, prefs=_start_cli(project))
                try:
                    async with first.run_test(size=(100, 32)):
                        first._engine_ready = True
                        await first._render_history()
                        await first.submit_text("ship the release")
                        for _ in range(300):
                            if not first.runtime.turn_active and provider.calls:
                                break
                            await asyncio.sleep(0.01)
                finally:
                    await _drain_runtime(first)

                # Second run, a new process: Up on the empty composer recalls it.
                second = NavinApp(make_config(project), prefs=_start_cli(project))
                try:
                    async with second.run_test(size=(100, 32)) as pilot:
                        second._engine_ready = True
                        await pilot.press("up")
                        await pilot.pause()
                        assert second.composer.text == "ship the release"
                finally:
                    await _drain_runtime(second)
        finally:
            set_config_path(old_config_path)

    asyncio.run(run())
