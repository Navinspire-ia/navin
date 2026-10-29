# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The App Factory nudge: once, late enough, and only for a web app without native targets."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from navin.agent import appfactory_hint as hint
from navin.agent.tools.context import RequestContext


@pytest.fixture(autouse=True)
def _fresh_caches():
    hint._offered_cache.clear()
    hint._commit_cache.clear()
    yield
    hint._offered_cache.clear()
    hint._commit_cache.clear()


def _web_project(root: Path, *, framework: str = "react", sub: str = "") -> Path:
    folder = root / sub if sub else root
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "package.json").write_text(json.dumps({"dependencies": {framework: "^1"}}), encoding="utf-8")
    return root


def test_a_web_app_that_stands_is_nudged_once_and_never_again(tmp_path: Path) -> None:
    root = _web_project(tmp_path / "shop", sub="webui")
    marker = tmp_path / "hints.json"
    lines = hint.appfactory_hint_lines(root, commits=42, marker=marker)
    assert lines and "App Factory" in lines[0]
    assert "in webui/" in lines[0] and "42 commits" in lines[0]
    assert "ONE sentence" in lines[1] and "Other > Mobile apps / Desktop apps" in lines[1]
    assert "do not repeat the offer" in lines[2]
    # Recorded before it is shown: the next turn, and the next session, stay silent.
    assert str(root) in json.loads(marker.read_text(encoding="utf-8"))
    assert hint.appfactory_hint_lines(root, commits=42, marker=marker) == []
    hint._offered_cache.clear()
    assert hint.appfactory_hint_lines(root, commits=42, marker=marker) == []


def test_too_early_without_history_then_nudged_once_the_project_has_a_past(tmp_path: Path) -> None:
    root = _web_project(tmp_path / "fresh")
    marker = tmp_path / "hints.json"
    assert hint.appfactory_hint_lines(root, commits=3, marker=marker) == []
    assert hint.appfactory_hint_lines(root, commits=None, marker=marker) == []
    assert not marker.exists()
    # A continuity brief means earlier sessions already worked here.
    (root / ".navin" / "continuity").mkdir(parents=True)
    (root / ".navin" / "continuity" / "RESUME.md").write_text("# Resume\n", encoding="utf-8")
    lines = hint.appfactory_hint_lines(root, commits=3, marker=marker)
    assert lines and "earlier sessions of work" in lines[0]


@pytest.mark.parametrize("native", ["src-tauri", "os/android", "android", "capacitor.config.ts"])
def test_a_project_that_already_ships_natively_is_left_alone(tmp_path: Path, native: str) -> None:
    root = _web_project(tmp_path / "native")
    target = root / native
    if "." in Path(native).name:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
    else:
        target.mkdir(parents=True)
    assert hint.appfactory_hint_lines(root, commits=100, marker=tmp_path / "hints.json") == []


def test_a_backend_only_or_unknown_project_is_not_a_candidate(tmp_path: Path) -> None:
    root = tmp_path / "api"
    root.mkdir()
    (root / "package.json").write_text(json.dumps({"dependencies": {"express": "^4"}}), encoding="utf-8")
    assert hint.web_frontend(root) is None
    assert hint.appfactory_hint_lines(root, commits=100, marker=tmp_path / "hints.json") == []
    (root / "pyproject.toml").write_text("[project]\nname='api'\n", encoding="utf-8")
    assert hint.appfactory_hint_lines(root, commits=100, marker=tmp_path / "hints.json") == []


def test_the_provider_delivers_the_block_off_the_loop_and_only_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _web_project(tmp_path / "app", framework="vue")
    marker = tmp_path / "hints.json"
    monkeypatch.setattr(hint, "marker_path", lambda: marker)
    monkeypatch.setattr(hint, "_git_commit_count", lambda _root: 30)

    async def run() -> tuple[object, object]:
        request = RequestContext(channel="cli", chat_id="t", workspace=root)
        first = await hint.appfactory_hint_context_provider(request)
        second = await hint.appfactory_hint_context_provider(request)
        return first, second

    first, second = asyncio.run(run())
    assert first is not None and first.source == "appfactory_hint"
    assert "Tauri 2" in first.content and "Capacitor 8" in first.content
    assert second is None


def test_a_request_without_workspace_gets_nothing() -> None:
    block = asyncio.run(hint.appfactory_hint_context_provider(RequestContext(channel="cli", chat_id="t")))
    assert block is None


def test_a_missing_or_unreadable_workspace_yields_no_hint_without_an_exception(tmp_path: Path) -> None:
    """Each step owns its file and git errors: no blanket catch is needed above them."""
    gone = tmp_path / "does-not-exist"
    assert hint.web_frontend(gone) is None
    assert hint.has_native_target(gone) is False
    assert hint.project_stands(gone, None) is False
    assert hint.commit_count(gone) is None
    assert hint.appfactory_hint_lines(gone, marker=tmp_path / "hints.json") == []
    # A package.json that is not JSON is skipped, not raised.
    root = tmp_path / "broken"
    root.mkdir()
    (root / "package.json").write_text("{not json", encoding="utf-8")
    assert hint.web_frontend(root) is None
