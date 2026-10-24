# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""A burst of file writes rebuilds the metagraph once, on one shared thread."""

import threading
import time

from navin.webui import metagraph_notify


def test_a_burst_of_writes_rebuilds_once_per_root(monkeypatch):
    fired: list[str] = []
    done = threading.Event()

    def rebuild(key: str) -> None:
        fired.append(key)
        if len(fired) == 2:
            done.set()

    monkeypatch.setattr(metagraph_notify, "_rebuild", rebuild)
    monkeypatch.setattr(metagraph_notify, "_DEBOUNCE_S", 0.05)
    threads_before = threading.active_count()
    for _ in range(20):
        metagraph_notify.schedule_metagraph_refresh("/repo/a")
    metagraph_notify.schedule_metagraph_refresh("/repo/b")
    assert threading.active_count() <= threads_before + 1, "no thread per write"
    assert done.wait(2)
    time.sleep(0.1)
    assert sorted(fired) == ["/repo/a", "/repo/b"]
