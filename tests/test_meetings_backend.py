from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from navin.meetings.services import calendar_payload, docx_payload
from navin.meetings.store import MeetingStore
from navin.providers.transcription import assemblyai_labelled_text, assemblyai_utterances
from navin.webui.meeting_bot import MeetingBot


class MeetingStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.store = MeetingStore(Path(self.temp.name) / "meetings")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_crud_search_audio_audit_and_export(self) -> None:
        record = self.store.create(
            {
                "id": "weekly",
                "meta": {"title": "Weekly sync"},
                "transcript": "Ana: ship the desktop build",
                "notes": "Friday",
            }
        )
        self.assertEqual(record["meta"]["title"], "Weekly sync")
        self.store.update("weekly", {"summary": "Desktop ships Friday"})
        self.assertEqual(self.store.search("desktop friday")[0]["id"], "weekly")
        audio = self.store.save_audio_segment("weekly", b"RIFFdata")
        self.assertEqual(audio["bytes"], 8)
        segments = self.store.list_audio_segments("weekly")
        self.assertEqual(segments[0]["id"], audio["id"])
        metadata, restored_audio = self.store.read_audio_segment(
            "weekly", audio["id"]
        )
        self.assertEqual(metadata["file"], audio["file"])
        self.assertEqual(restored_audio, b"RIFFdata")
        self.assertTrue(self.store.read_audit(meeting_id="weekly"))
        archive = self.store.emergency_export()
        with zipfile.ZipFile(BytesIO(archive)) as exported:
            self.assertIn("records/weekly/meta.json", exported.namelist())
        self.store.delete("weekly")
        self.assertEqual(self.store.list(), [])

    def test_migration_is_versioned_and_idempotent(self) -> None:
        payload = {"meetings": [{"id": "legacy", "title": "Imported", "notes": "local"}]}
        first = self.store.migrate_local_storage(payload, migration_id="browser-v2")
        second = self.store.migrate_local_storage(payload, migration_id="browser-v2")
        self.assertEqual(first["imported"], ["legacy"])
        self.assertTrue(second["already_applied"])
        self.assertEqual(len(self.store.list()), 1)

    def test_templates_calendar_and_bot_restart_state(self) -> None:
        self.store.put_template("exec", {"name": "Executive"})
        self.assertEqual(self.store.list_templates()[0]["id"], "exec")
        self.store.set_calendar({"events": [{"id": "one"}]})
        self.assertEqual(self.store.get_calendar()["events"][0]["id"], "one")
        self.store.save_bot_state("bot-one", {"state": "live", "segments": [{"text": "hi"}]})
        reopened = MeetingStore(self.store.root)
        self.assertEqual(reopened.load_bot_state("bot-one")["state"], "live")


class MeetingServicesTest(unittest.TestCase):
    def test_assemblyai_native_utterances(self) -> None:
        payload = {
            "text": "hello world",
            "utterances": [
                {"speaker": "A", "text": "hello", "start": 0, "end": 300},
                {"speaker": "B", "text": "world", "start": 400, "end": 800},
            ],
        }
        self.assertEqual(assemblyai_utterances(payload)[1]["speaker"], "Speaker B")
        self.assertEqual(assemblyai_labelled_text(payload), "Speaker A: hello\nSpeaker B: world")

    def test_docx_is_deterministic_and_readable(self) -> None:
        first = docx_payload(title="Weekly", report="## Decision\nShip")
        second = docx_payload(title="Weekly", report="## Decision\nShip")
        self.assertEqual(first["data_base64"], second["data_base64"])
        with zipfile.ZipFile(BytesIO(__import__("base64").b64decode(first["data_base64"]))) as doc:
            self.assertIn("word/document.xml", doc.namelist())

    def test_calendar_has_offline_ics_fallback(self) -> None:
        result = calendar_payload(
            [{"id": "x", "title": "Sync", "start": "20260823T080000Z"}],
            provider="google",
            credentials={"client_id": "mock"},
        )
        self.assertFalse(result["synced"])
        self.assertIn("BEGIN:VEVENT", result["ics"])
        self.assertNotIn("\u2014", json.dumps(result))
        self.assertNotIn("\u2013", json.dumps(result))


class MeetingTranslationTest(unittest.IsolatedAsyncioTestCase):
    async def test_translation_and_cleanup_keep_explicit_semantics(self) -> None:
        from navin.meetings import services

        original = services._ask

        async def fake_ask(system: str, user: str, **_kwargs: object):
            self.assertIn("timecode", system)
            self.assertTrue(user)
            return "Speaker 1: Bonjour", "mock", "docs"

        services._ask = fake_ask
        try:
            translated = await services.translate_payload(
                text="Speaker 1: Hello", target_language="French"
            )
            cleaned = await services.cleanup_payload(transcript="speaker 1 hello")
        finally:
            services._ask = original
        self.assertEqual(translated["text"], "Speaker 1: Bonjour")
        self.assertFalse(cleaned["acoustic_diarization"])
        self.assertEqual(cleaned["accuracy"], "llm_text_cleanup")


class _FakeLocator:
    def __init__(self, page: "_FakePage", selector: str):
        self.page = page
        self.selector = selector

    @property
    def first(self) -> "_FakeLocator":
        return self

    async def click(self, **_kwargs: object) -> None:
        if not any(token in self.selector for token in self.page.click_tokens):
            raise RuntimeError("not found")
        self.page.clicked.append(self.selector)

    async def fill(self, value: str, **_kwargs: object) -> None:
        if not any(token in self.selector for token in self.page.fill_tokens):
            raise RuntimeError("not found")
        self.page.filled = value


class _FakePage:
    def __init__(self, fill_tokens: tuple[str, ...], click_tokens: tuple[str, ...]):
        self.fill_tokens = fill_tokens
        self.click_tokens = click_tokens
        self.clicked: list[str] = []
        self.filled = ""
        self.url = ""

    async def goto(self, url: str, **_kwargs: object) -> None:
        self.url = url

    def locator(self, selector: str) -> _FakeLocator:
        return _FakeLocator(self, selector)

    async def screenshot(self, **_kwargs: object) -> None:
        return None


class MeetingBotJoinMockTest(unittest.IsolatedAsyncioTestCase):
    async def test_zoom_meet_and_teams_guest_flows(self) -> None:
        cases = (
            (
                "https://zoom.us/j/123",
                ("#input-for-name",),
                ("Join", "Audio by Computer"),
                "_join_zoom",
            ),
            (
                "https://meet.google.com/abc-defg-hij",
                ('input[aria-label*="name"',),
                ("Ask to join",),
                "_join_meet",
            ),
            (
                "https://teams.microsoft.com/l/meetup-join/test",
                ("prejoin-display-name-input",),
                ("Continue on this browser", "prejoin-join-button"),
                "_join_teams",
            ),
        )
        for url, fill_tokens, click_tokens, method in cases:
            with self.subTest(method=method):
                bot = MeetingBot("mock-bot", url, "Navin Test")
                page = _FakePage(fill_tokens, click_tokens)
                await getattr(bot, method)(page)
                self.assertEqual(page.filled, "Navin Test")
                self.assertTrue(page.clicked)


if __name__ == "__main__":
    unittest.main()
