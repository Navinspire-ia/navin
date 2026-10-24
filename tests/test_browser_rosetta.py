# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""An Intel build under Rosetta launches a universal browser natively.

The macOS x64 sidecar, smoke-tested on an Apple Silicon runner, started the
system Chrome as Intel under Rosetta and headless launch hung for 180 s.
"""

import os
import stat

from navin.agent.tools import browser


def test_native_launcher_under_rosetta(monkeypatch, tmp_path):
    monkeypatch.setattr(browser, "_rosetta_translated", lambda: True)
    monkeypatch.setattr(browser, "_has_arm64_slice", lambda path: True)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    launcher = browser.native_browser_executable(chrome)
    assert launcher != chrome
    text = open(launcher).read()
    assert text.startswith("#!/bin/sh\n")
    assert "exec /usr/bin/arch -arm64 '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \"$@\"" in text
    assert os.stat(launcher).st_mode & stat.S_IXUSR
    assert browser.native_browser_executable(chrome) == launcher, "stable across launches"


def test_untouched_when_native_or_intel_only(monkeypatch):
    chrome = "/opt/chrome"
    monkeypatch.setattr(browser, "_rosetta_translated", lambda: False)
    assert browser.native_browser_executable(chrome) == chrome
    monkeypatch.setattr(browser, "_rosetta_translated", lambda: True)
    monkeypatch.setattr(browser, "_has_arm64_slice", lambda path: False)
    assert browser.native_browser_executable(chrome) == chrome


def test_not_translated_off_macos():
    import sys

    if sys.platform != "darwin":
        assert browser._rosetta_translated() is False
