# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the WebUI session-import API helpers."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from navin.session.import_sessions import ExternalSession, SourceReport
from navin.webui import session_import_api


def _report(name: str, sessions: int) -> SourceReport:
    items = [
        ExternalSession(
            source=name,
            session_id=f"ses-{idx}",
            title=f"Session {idx}",
            messages=[{"role": "user", "content": "hello"}],
            origin_path=Path("/tmp/fake.jsonl"),
            created_at=datetime(2026, 5, 1, 12, 0, 0),
            updated_at=datetime(2026, 5, 1, 12, 5, 0),
        )
        for idx in range(sessions)
    ]
    return SourceReport(
        name=name, label=name.title(), root=Path("/tmp"), status="ready",
        sessions=items,
    )


class SessionImportApiTests(unittest.TestCase):
    def test_scan_reports_every_source(self) -> None:
        reports = [_report("codex", 2), _report("claude-code", 0)]
        with mock.patch.object(
            session_import_api, "discover", return_value=reports
        ), mock.patch.object(
            session_import_api, "resolve_sources", return_value=("codex",)
        ):
            payload = session_import_api.scan_external_sessions()
        by_name = {s["name"]: s for s in payload["sources"]}
        self.assertEqual(by_name["codex"]["discovered"], 2)
        self.assertEqual(by_name["codex"]["status"], "ready")
        self.assertEqual(by_name["claude-code"]["discovered"], 0)

    def test_import_writes_into_workspace_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "ws"
            reports = [_report("codex", 2)]
            with mock.patch.object(
                session_import_api, "discover", return_value=reports
            ), mock.patch.object(
                session_import_api, "resolve_sources", return_value=("codex",)
            ):
                payload = session_import_api.run_external_session_import(
                    workspace, source="codex"
                )
            self.assertEqual(payload["sources"][0]["imported"], 2)
            files = list((workspace / "sessions").glob("*.jsonl"))
            self.assertEqual(len(files), 2)

    def test_import_rejects_unknown_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                session_import_api,
                "resolve_sources",
                side_effect=ValueError("Unknown source 'nope'"),
            ):
                with self.assertRaises(session_import_api.SessionImportError) as ctx:
                    session_import_api.run_external_session_import(
                        Path(tmp), source="nope"
                    )
            self.assertEqual(ctx.exception.status, 400)

    def test_saved_roots_add_list_remove(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Path(tmp) / "workspaceStorage"
            from navin.session import import_sessions

            with mock.patch.object(
                import_sessions, "_EXTRA_ROOTS_FILE", Path(tmp) / "r.json"
            ):
                added = session_import_api.add_saved_root(
                    "cursor", str(storage)
                )
                self.assertEqual(
                    added["saved_roots"], {"cursor": [str(storage)]}
                )
                removed = session_import_api.delete_saved_root(
                    "cursor", str(storage)
                )
                self.assertEqual(removed["saved_roots"], {})
            with self.assertRaises(session_import_api.SessionImportError) as ctx:
                session_import_api.add_saved_root("nope", str(storage))
            self.assertEqual(ctx.exception.status, 400)


class ImportRootsMethodRoutingTests(unittest.IsolatedAsyncioTestCase):
    """The gateway handshake only forwards GET, so the verb travels as
    ``?_method=`` and the handler must honor it."""

    def _handler(self):
        from navin.webui import ws_http

        stub = SimpleNamespace(
            check_api_token=lambda request: True,
            workspace_controls_available=lambda connection: True,
        )
        return ws_http.GatewayHTTPHandler._handle_webui_session_import_roots.__get__(stub)

    def _json(self, response):
        import json

        return json.loads(response.body.decode("utf-8"))

    def _request(self, query: str, method: str = "GET"):
        return SimpleNamespace(path=f"/api/webui/sessions/import/roots{query}", method=method)

    async def test_method_query_param_drives_add_and_delete(self) -> None:
        handler = self._handler()
        with tempfile.TemporaryDirectory() as tmp:
            from navin.session import import_sessions

            with mock.patch.object(
                import_sessions, "_EXTRA_ROOTS_FILE", Path(tmp) / "r.json"
            ):
                added = await handler(
                    None, self._request(f"?source=cursor&path={tmp}&_method=POST")
                )
                self.assertEqual(self._json(added)["saved_roots"], {"cursor": [tmp]})
                removed = await handler(
                    None, self._request(f"?source=cursor&path={tmp}&_method=DELETE")
                )
                self.assertEqual(self._json(removed)["saved_roots"], {})

    async def test_get_without_method_lists_roots(self) -> None:
        handler = self._handler()
        with tempfile.TemporaryDirectory() as tmp:
            from navin.session import import_sessions

            with mock.patch.object(
                import_sessions, "_EXTRA_ROOTS_FILE", Path(tmp) / "r.json"
            ):
                listed = await handler(None, self._request(""))
                self.assertEqual(self._json(listed)["saved_roots"], {})

    async def test_get_ignores_add_params_and_lists_roots(self) -> None:
        handler = self._handler()
        response = await handler(None, self._request("?source=cursor&path=/tmp"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self._json(response)["saved_roots"], {})


if __name__ == "__main__":
    unittest.main()
