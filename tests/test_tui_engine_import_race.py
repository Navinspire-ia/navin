# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The CLI engine build survives an import race with the UI thread."""

from __future__ import annotations

import asyncio

import pytest

from navin.tui import runtime as runtime_module
from navin.tui.runtime import _concurrent_import_race


def test_import_races_are_recognised_and_real_errors_are_not() -> None:
    assert _concurrent_import_race(
        ImportError("deadlock detected by _ModuleLock('navin.world_model.settings') at 1")
    )
    assert _concurrent_import_race(
        ImportError("cannot import name 'RefreshStats' from 'navin.index.store' (/x/navin/index/store.py)")
    )
    assert not _concurrent_import_race(ImportError("No module named 'numpy'"))


class _Runtime:
    _build_engine_off_loop = runtime_module.TuiRuntime._build_engine_off_loop

    def __init__(self, failures: list[Exception]) -> None:
        self.failures = failures
        self.calls = 0

    def _build_engine(self) -> None:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)


def test_engine_build_retries_a_lost_import_race() -> None:
    rt = _Runtime([ImportError("deadlock detected by _ModuleLock('navin.x')")])
    asyncio.run(rt._build_engine_off_loop())
    assert rt.calls == 2


def test_engine_build_does_not_retry_a_missing_module() -> None:
    rt = _Runtime([ImportError("No module named 'missing'")])
    with pytest.raises(ImportError, match="missing"):
        asyncio.run(rt._build_engine_off_loop())
    assert rt.calls == 1


def test_world_model_package_imports_lazily() -> None:
    # A fresh interpreter: clearing sys.modules here would break later tests
    # that already hold these modules.
    import subprocess
    import sys

    code = (
        "import sys, navin.world_model as w; "
        "assert 'navin.world_model.settings' not in sys.modules; "
        "assert w.read_settings.__module__ == 'navin.world_model.settings'"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
