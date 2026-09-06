"""Tests for the Artifact Canvas store, detect helper, and present_artifact tool."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.present_artifact import PresentArtifactTool
from navin.artifacts.detect import extract_fenced_artifacts, upsert_fenced_artifacts
from navin.artifacts.store import ArtifactError, ArtifactStore
from navin.bus.outbound_events import (
    ArtifactSelectEvent,
    ArtifactUpsertEvent,
    outbound_event_from_message,
)


class _Bus:
    def __init__(self) -> None:
        self.sent: list = []
        self.outbound = SimpleNamespace(put_nowait=self.sent.append)


class ArtifactStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.store = ArtifactStore("chat-1", root=self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_upsert_list_get_roundtrip(self) -> None:
        created = self.store.upsert(
            artifact_type="html",
            title="Hello",
            content="<h1>Hi</h1>",
        )
        self.assertTrue(created["id"].startswith("art-"))
        self.assertEqual(created["type"], "html")
        self.assertEqual(created["content"], "<h1>Hi</h1>")
        self.assertEqual(created["version"], 1)

        listed = self.store.list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], created["id"])
        self.assertNotIn("content", listed[0])

        got = self.store.get(created["id"])
        assert got is not None
        self.assertEqual(got["content"], "<h1>Hi</h1>")
        self.assertEqual(got["title"], "Hello")

    def test_upsert_updates_version_and_content(self) -> None:
        first = self.store.upsert(
            artifact_type="markdown",
            title="Notes",
            content="# v1",
            artifact_id="notes-1",
        )
        second = self.store.upsert(
            artifact_type="markdown",
            title="Notes",
            content="# v2",
            artifact_id="notes-1",
        )
        self.assertEqual(first["id"], "notes-1")
        self.assertEqual(second["version"], 2)
        self.assertEqual(second["content"], "# v2")
        self.assertEqual(len(self.store.list()), 1)

    def test_export_path_points_at_content_file(self) -> None:
        art = self.store.upsert(
            artifact_type="mermaid",
            title="Flow",
            content="graph TD; A-->B;",
            artifact_id="flow-1",
        )
        path = self.store.export_path(art["id"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_text(encoding="utf-8"), "graph TD; A-->B;")
        self.assertTrue(path.name.endswith(".mmd"))

    def test_invalid_type_rejected(self) -> None:
        with self.assertRaises(ArtifactError):
            self.store.upsert(artifact_type="react-preview", content="x")

    def test_type_change_rejected(self) -> None:
        self.store.upsert(artifact_type="html", content="<p>a</p>", artifact_id="x1")
        with self.assertRaises(ArtifactError):
            self.store.upsert(artifact_type="markdown", content="a", artifact_id="x1")


class DetectFencedArtifactsTest(unittest.TestCase):
    def test_extracts_html_and_mermaid(self) -> None:
        text = (
            "Intro\n"
            "```html\n"
            "<div>Hi</div>\n"
            "```\n"
            "\n"
            "```mermaid\n"
            "graph TD; A-->B;\n"
            "```\n"
        )
        found = extract_fenced_artifacts(text)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0]["type"], "html")
        self.assertIn("<div>Hi</div>", found[0]["content"])
        self.assertEqual(found[1]["type"], "mermaid")
        self.assertTrue(found[0]["id"].startswith("auto-html-"))

    def test_upsert_fenced_writes_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text = "```html\n<p>x</p>\n```"
            results = upsert_fenced_artifacts("chat-detect", text, root=root)
            self.assertEqual(len(results), 1)
            store = ArtifactStore("chat-detect", root=root)
            listed = store.list()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["type"], "html")


class PresentArtifactToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_emits_upsert_and_select(self) -> None:
        bus = _Bus()
        tool = PresentArtifactTool(bus=bus)
        ctx = RequestContext(channel="websocket", chat_id="chat-tool")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch("navin.artifacts.store.artifacts_root", return_value=root),
                request_context(ctx),
            ):
                result = await tool.execute(
                    type="html",
                    title="Card",
                    content="<p>hello</p>",
                )

        self.assertNotIn("Error", str(result))
        self.assertGreaterEqual(len(bus.sent), 2)
        events = [outbound_event_from_message(msg) for msg in bus.sent]
        upserts = [e for e in events if isinstance(e, ArtifactUpsertEvent)]
        selects = [e for e in events if isinstance(e, ArtifactSelectEvent)]
        self.assertEqual(len(upserts), 1)
        self.assertEqual(upserts[0].artifact["title"], "Card")
        self.assertEqual(upserts[0].artifact["content"], "<p>hello</p>")
        self.assertEqual(len(selects), 1)
        self.assertEqual(selects[0].artifact_id, upserts[0].artifact["id"])

    async def test_an_invented_report_name_presents_the_stamped_report(self) -> None:
        """A mis-guessed artifact name recovers instead of dead-ending.

        Models invent descriptive names ("review-report-my-topic.html") for
        reports the tooling wrote under a stamped name; presenting the obvious
        stand-in, disclosed in the result, beats a bare "file not found".
        """
        bus = _Bus()
        ctx = RequestContext(channel="websocket", chat_id="chat-recover")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "review-report-20260810-1123.html").write_text(
                "<html>real report</html>", encoding="utf-8"
            )
            tool = PresentArtifactTool(bus=bus, working_dir=str(root))
            with (
                patch("navin.artifacts.store.artifacts_root", return_value=root),
                request_context(ctx),
            ):
                result = await tool.execute(
                    type="file", path="review-report-navin-code-vs-cursor.html"
                )

        self.assertNotIn("Error", str(result))
        self.assertIn("closest match", str(result))
        self.assertIn("review-report-20260810-1123.html", str(result))

    async def test_refuses_non_websocket(self) -> None:
        tool = PresentArtifactTool(bus=MagicMock())
        ctx = RequestContext(channel="telegram", chat_id="chat-x")
        with request_context(ctx):
            result = await tool.execute(type="html", content="<p>x</p>")
        self.assertIn("WebUI", str(result))

    def test_tool_is_auto_discovered(self) -> None:
        discovered = {cls.__name__ for cls in ToolLoader().discover()}
        self.assertIn("PresentArtifactTool", discovered)


class WebsocketArtifactPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_send_artifact_upsert_payload_shape(self) -> None:
        import json

        from navin.channels.websocket import WebSocketChannel

        sent: list[str] = []

        class _Conn:
            async def send(self, raw: str) -> None:
                sent.append(raw)

        channel = WebSocketChannel.__new__(WebSocketChannel)
        channel._subs = {"chat-ws": (_Conn(),)}  # type: ignore[attr-defined]

        async def _safe_send_to(connection, raw, label=""):
            await connection.send(raw)

        channel._safe_send_to = _safe_send_to  # type: ignore[method-assign]
        await channel.send_artifact_upsert(
            "chat-ws",
            ArtifactUpsertEvent(
                artifact={
                    "id": "art-1",
                    "type": "html",
                    "title": "T",
                    "content": "<p>x</p>",
                }
            ),
        )
        self.assertEqual(len(sent), 1)
        body = json.loads(sent[0])
        self.assertEqual(body["event"], "artifact_upsert")
        self.assertEqual(body["chat_id"], "chat-ws")
        self.assertEqual(body["artifact"]["id"], "art-1")

        await channel.send_artifact_select(
            "chat-ws",
            ArtifactSelectEvent(artifact_id="art-1"),
        )
        body2 = json.loads(sent[1])
        self.assertEqual(body2["event"], "artifact_select")
        self.assertEqual(body2["artifact_id"], "art-1")


if __name__ == "__main__":
    unittest.main()
