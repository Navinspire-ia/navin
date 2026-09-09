# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The public download action serves the exact generated Archify artifact."""

import base64
from unittest.mock import patch

from navin.tenders.store import TenderStore
from navin.webui.tenders_api import handle_tenders_action


def test_html_diagram_download_preserves_bytes_name_and_mime(tmp_path):
    store = TenderStore(tmp_path)
    html = '<!doctype html><html lang="fr"><svg aria-label="Exigence EXG-05"></svg></html>'.encode()
    record = store.save_bytes("architecture.html", html, file_id="out_archify_html_notice")
    store.save_tenders([{
        "id": "notice",
        "title": "Fictional requirement response",
        "response": {"exports": {"diagram_html": {**record, "kind": "html", "mime": "text/html"}}},
    }])
    with patch("navin.webui.tenders_api._store", return_value=store), patch(
        "navin.tenders.desk.attach_exports", side_effect=AssertionError("existing artifact must not be regenerated")
    ):
        payload = handle_tenders_action("download", {"id": "notice", "kind": "diagram_html"})["download"]
    assert payload["kind"] == "diagram_html"
    assert payload["mime"] == "text/html"
    assert payload["name"] == "architecture.html"
    assert base64.b64decode(payload["data"]) == html
