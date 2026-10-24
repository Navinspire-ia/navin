# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Definition sites for code names in the request reach the model up front."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from navin.agent.likely_locations import (
    MAX_LINES,
    candidate_symbols,
    likely_location_lines,
    likely_locations_context_provider,
)


class _Loc:
    def __init__(self, path: str, line: int, name: str) -> None:
        self.path, self.line, self.name = path, line, name

    def render(self) -> str:
        return f"{self.path}:{self.line} {self.name} [function]"


class _FakeIndex:
    def __init__(self, table: dict[str, list[_Loc]], loaded: bool = True) -> None:
        self.table = table
        self._loaded = loaded
        self._lifecycle_lock = threading.RLock()

    def ensure(self) -> None:
        self._loaded = True

    def definition(self, name: str) -> list[_Loc]:
        return self.table.get(name, [])


def _request(text: str, root: Path | None = Path("/tmp/project")) -> SimpleNamespace:
    return SimpleNamespace(original_user_text=text, workspace=root)


def _run(request: SimpleNamespace, index: _FakeIndex):
    with mock.patch("navin.index.get_index", return_value=index):
        return asyncio.run(likely_locations_context_provider(request))


def test_code_looking_names_are_found_and_plain_words_are_not() -> None:
    assert candidate_symbols("where is model_pick_items built, and ModelPickerScreen?") == [
        "model_pick_items", "ModelPickerScreen",
    ]
    assert candidate_symbols("la liste des modeles du selecteur est en double") == []
    assert candidate_symbols("") == [] and candidate_symbols(None) == []


def test_backticks_dotted_paths_and_files() -> None:
    assert candidate_symbols("see `navin.tui.models.model_kind()` please") == ["model_kind"]
    assert candidate_symbols("edit settings_api.py and navin/tui/app.py") == []


def test_names_are_capped_and_deduplicated() -> None:
    text = "foo_bar_one foo_bar_two foo_bar_three foo_bar_four foo_bar_five FOO_BAR_ONE"
    assert len(candidate_symbols(text)) == 4
    assert candidate_symbols("one_name one_name") == ["one_name"]


def test_lines_are_capped() -> None:
    table = {f"sym_{i}": [_Loc(f"f{i}.py", i, f"sym_{i}")] * 3 for i in range(4)}
    lines = likely_location_lines(_FakeIndex(table), list(table))
    assert len(lines) == MAX_LINES


def test_provider_injects_definition_sites() -> None:
    index = _FakeIndex({"model_pick_items": [_Loc("navin/tui/models.py", 41, "model_pick_items")]})
    block = _run(_request("how does model_pick_items filter rows?"), index)
    assert block is not None and block.source == "likely_locations"
    assert "navin/tui/models.py:41 model_pick_items" in block.content


def test_a_cold_index_is_loaded_when_the_request_names_code() -> None:
    cold = _FakeIndex({"model_pick_items": [_Loc("a.py", 1, "model_pick_items")]}, loaded=False)
    assert _run(_request("model_pick_items?"), cold) is not None
    assert cold._loaded


def test_provider_is_silent_without_names_or_matches() -> None:
    index = _FakeIndex({"model_pick_items": [_Loc("a.py", 1, "model_pick_items")]})
    assert _run(_request("bonjour, ca va ?"), index) is None
    assert _run(_request("what is unknown_symbol_here?"), index) is None
    assert _run(_request("model_pick_items?", root=None), index) is None


def test_a_failing_index_never_breaks_the_turn() -> None:
    class Broken(_FakeIndex):
        def definition(self, name: str):
            raise RuntimeError("index busy")

    assert _run(_request("model_pick_items?"), Broken({})) is None
