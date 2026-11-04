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


def test_edits_rebuild_the_graph_only_while_a_graph_view_is_open(monkeypatch, tmp_path):
    # Rebuilt after every agent write with nobody looking, the graph (a tree
    # walk plus a layout) kept a core busy for whole turns on Windows.
    from navin.webui import metagraph

    rebuilt: list[str] = []
    monkeypatch.setattr(metagraph, "rebuild_and_notify", lambda root, **_: rebuilt.append(str(root)))
    monkeypatch.setattr(metagraph, "_watched_until", {})
    metagraph_notify._rebuild(str(tmp_path))
    assert rebuilt == [], "no Graph view is open"

    metagraph.note_graph_watched(tmp_path)
    metagraph_notify._rebuild(str(tmp_path))
    assert rebuilt == [str(tmp_path)]

    monkeypatch.setattr(metagraph, "GRAPH_WATCH_S", -1.0)
    metagraph.note_graph_watched(tmp_path)  # a view that stopped fetching
    metagraph_notify._rebuild(str(tmp_path))
    assert rebuilt == [str(tmp_path)]
