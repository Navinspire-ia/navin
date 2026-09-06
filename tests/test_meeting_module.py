"""Wiring and workflow expectations for the Meeting studio."""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import navin.agent.skills  # noqa: F401
from navin.agent.model_routes import WORKFLOW_ROUTE_ROLES
from navin.command.builtin import (
    _DELIVERY_WORKFLOWS,
    _HTML_REPORT_CLAUSE,
    _HTML_REPORT_WORKFLOWS,
    _TRACKED_WORKFLOWS,
    _WORKFLOW_BRIEFS,
    BUILTIN_COMMAND_SPECS,
    _workflow_handler,
    builtin_command_palette,
)
from navin.command.modules import (
    _MODULE_OWNED_COMMANDS,
    CODE_HIDDEN_COMMANDS,
    PRELOAD_SKILLS_METADATA_KEY,
    REQUIRES_TOOL_DELIVERY_METADATA_KEY,
    VALID_PRODUCT_MODULES,
    disabled_skills_for_module,
    exclusive_studio_skills,
    is_command_allowed_for_module,
    module_mismatch_message,
)

ROOT = Path(__file__).resolve().parents[1]
MEETING_CARDS = (
    "meetingMinutes",
    "meetingSummary",
    "meetingImport",
    "meetingFollowup",
    "meetingActions",
    "meetingDiscovery",
)


class MeetingProductModuleTest(unittest.TestCase):
    def test_module_registered(self) -> None:
        self.assertIn("meeting", VALID_PRODUCT_MODULES)
        self.assertEqual(_MODULE_OWNED_COMMANDS["meeting"], frozenset({"/meeting"}))
        self.assertIn("/meeting", CODE_HIDDEN_COMMANDS)
        self.assertTrue(is_command_allowed_for_module("/meeting", "meeting"))
        self.assertFalse(is_command_allowed_for_module("/meeting", "code"))
        self.assertIn("Meeting", module_mismatch_message("/meeting", "code"))

    def test_palette_scoping(self) -> None:
        code = {row["command"] for row in builtin_command_palette("code")}
        meeting = {row["command"] for row in builtin_command_palette("meeting")}
        self.assertNotIn("/meeting", code)
        self.assertIn("/meeting", meeting)
        self.assertNotIn("/leads", meeting)

    def test_meeting_studio_skill_exclusive(self) -> None:
        exclusive = exclusive_studio_skills()
        self.assertIn("meeting-studio", exclusive.get("meeting", set()))
        self.assertIn("meeting-studio", disabled_skills_for_module("code"))
        self.assertNotIn("meeting-studio", disabled_skills_for_module("meeting"))


class MeetingWorkflowTest(unittest.TestCase):
    def test_command_spec_and_brief(self) -> None:
        specs = {spec.command: spec for spec in BUILTIN_COMMAND_SPECS}
        self.assertIn("/meeting", specs)
        self.assertEqual(specs["/meeting"].lifecycle, "agent_turn")
        self.assertEqual(specs["/meeting"].icon, "mic")
        title, skills, brief = _WORKFLOW_BRIEFS["/meeting"]
        self.assertIn("Meeting", title)
        blob = f"{skills}\n{brief}"
        for token in (
            "meeting-studio",
            "meeting-followup",
            "discovery-call-assistant",
            "meeting-report",
            "meetings/",
        ):
            self.assertIn(token, blob, token)
        for skill in (name.strip() for name in skills.split(",") if name.strip()):
            path = ROOT / "navin" / "skills" / skill / "SKILL.md"
            self.assertTrue(path.is_file(), f"missing skill {skill}")

    def test_workflow_bags_and_model_route(self) -> None:
        self.assertIn("/meeting", _DELIVERY_WORKFLOWS)
        self.assertIn("/meeting", _TRACKED_WORKFLOWS)
        self.assertIn("/meeting", _HTML_REPORT_WORKFLOWS)
        self.assertIn("meeting-report", _HTML_REPORT_CLAUSE)
        self.assertEqual(WORKFLOW_ROUTE_ROLES.get("/meeting"), "docs")

    def test_handler_preloads_and_rejects_code(self) -> None:
        msg = SimpleNamespace(
            content="",
            metadata={"product_module": "meeting"},
            channel="cli",
            chat_id="meeting-test",
        )
        ctx = SimpleNamespace(
            args="minutes from transcript",
            raw="/meeting minutes from transcript",
            msg=msg,
            loop=None,
        )
        result = asyncio.run(_workflow_handler("/meeting")(ctx))  # type: ignore[arg-type]
        self.assertIsNone(result)
        meta = dict(msg.metadata)
        self.assertTrue(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY))
        preloaded = meta.get(PRELOAD_SKILLS_METADATA_KEY) or []
        self.assertIn("meeting-studio", preloaded)
        self.assertEqual(meta.get("model_route_role"), "docs")

        bad = SimpleNamespace(
            content="",
            metadata={"product_module": "code"},
            channel="cli",
            chat_id="meeting-test",
        )
        out = asyncio.run(
            _workflow_handler("/meeting")(
                SimpleNamespace(args="x", raw="/meeting x", msg=bad, loop=None)
            )
        )  # type: ignore[arg-type]
        self.assertIsNotNone(out)
        self.assertIn("Meeting", out.content)


class MeetingDocsAndFrontendTest(unittest.TestCase):
    def test_docs_exist(self) -> None:
        paths = (
            ROOT / "docs/navin_meeting/en/README.md",
            ROOT / "docs/navin_meeting/en/actions.md",
            ROOT / "docs/navin_meeting/en/skills.md",
            ROOT / "docs/navin_meeting/fr/README.md",
            ROOT / "site/front/content/docs/navin_meeting/en/README.md",
        )
        for path in paths:
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertTrue(path.is_file())
                text = path.read_text(encoding="utf-8")
                self.assertIn("#/meeting", text)
                self.assertNotIn("\u2014", text)
                self.assertNotIn("\u2013", text)
        readme = (ROOT / "docs/navin_meeting/en/README.md").read_text(encoding="utf-8")
        for token in (
            "Custom summary templates",
            "Speaker identification",
            "Chat with meetings",
            "Calendar integration",
            "audit",
        ):
            self.assertIn(token, readme, token)

    def test_docs_pages_cover_the_module(self) -> None:
        pages = (
            "quickstart",
            "transcription",
            "exports",
            "calendar",
            "privacy",
            "troubleshooting",
        )
        catalog = (ROOT / "site/front/src/lib/docs-catalog.ts").read_text(encoding="utf-8")
        for page in pages:
            for lang in ("en", "fr"):
                path = ROOT / f"docs/navin_meeting/{lang}/{page}.md"
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertTrue(path.is_file())
                    text = path.read_text(encoding="utf-8")
                    self.assertIn("#/meeting", text)
                    self.assertNotIn("\u2014", text)
                    self.assertNotIn("\u2013", text)
            published = ROOT / f"site/front/content/docs/navin_meeting/en/{page}.md"
            with self.subTest(published=str(published.relative_to(ROOT))):
                self.assertTrue(published.is_file(), "run: cd site/front && npm run sync-docs")
            self.assertIn(f'file: "navin_meeting/en/{page}.md"', catalog, page)
            self.assertIn(f'slug: "studio/meeting/{page}"', catalog, page)

        hub = ROOT / "docs/navin_meeting/README.md"
        hub_text = hub.read_text(encoding="utf-8")
        self.assertIn("#/meeting", hub_text)
        for page in pages:
            self.assertIn(f"./en/{page}.md", hub_text, page)
            self.assertIn(f"./fr/{page}.md", hub_text, page)
        self.assertIn(
            "navin_meeting", (ROOT / "docs/README.md").read_text(encoding="utf-8")
        )

    def test_seo_articles_are_registered(self) -> None:
        slugs = (
            "reunions-ia-locale-transcription-comptes-rendus-navin",
            "transcrire-reunion-une-heure-limites-stt-navin",
            "rgpd-comptes-rendus-reunion-audit-local-navin",
        )
        registry = (ROOT / "site/front/src/lib/blog-seo-posts.ts").read_text(encoding="utf-8")
        bodies = (ROOT / "site/front/src/lib/blog-seo-bodies.generated.ts").read_text(
            encoding="utf-8"
        )
        for slug in slugs:
            self.assertIn(f'slug: "{slug}"', registry, slug)
            self.assertIn(f'"{slug}"', bodies, f"{slug}: run generate-blog-bodies.py")
            for locale in ("fr", "en", "ar"):
                path = ROOT / f"site/front/content/blog/{slug}.{locale}.md"
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertTrue(path.is_file())
                    text = path.read_text(encoding="utf-8")
                    self.assertGreater(len(text), 1200)
                    self.assertIn("/docs/studio/meeting", text)
                    self.assertIn("##", text)
                    self.assertNotIn("\u2014", text)
                    self.assertNotIn("\u2013", text)

    def test_brief_covers_pro_capabilities(self) -> None:
        _title, _skills, brief = _WORKFLOW_BRIEFS["/meeting"]
        for token in (
            "template",
            "speaker",
            "chat",
            "DOCX",
            "PDF",
            "high-accuracy",
        ):
            self.assertIn(token, brief, token)

    def test_webui_wires_meeting_surface(self) -> None:
        checks = {
            "webui/src/App.tsx": (
                '"meeting"',
                'path === "/meeting"',
                "onOpenMeetingStudio",
                "MeetingWorkbench",
                # Full-width mode: the desk takes the shell, chat steps aside.
                "workbenchFocus",
                "onToggleWorkbenchFocus",
            ),
            "webui/src/components/Sidebar.tsx": (
                '"meeting"',
                "onOpenMeetingStudio",
                "meetingStudio",
                "Mic",
            ),
            "webui/src/components/studio/StudioWorkspace.tsx": (
                '| "meeting"',
                'command: "/meeting"',
                "meetingGroups",
                "meetingMinutes",
            ),
            "webui/src/components/meeting/MeetingWorkbench.tsx": (
                "MeetingWorkbench",
                "/meeting",
                "onTranscribeAudio",
                "Ask this meeting",
                "Custom summary template",
                "Calendar (ICS)",
                "Identify speakers",
                "High-accuracy pass",
                "exportMarkdownPack",
                # STT plumbing: provider limits drive chunking and long imports
                # are re-cut client side before hitting the ingress.
                "transcription?: SettingsPayload",
                "segmentAudioBlob",
                "convertBlobToWav",
                "incrementalWavFromBlob",
                "maxDurationSec",
                "WAV_ONLY_PROVIDERS",
                "maybeAutoIdentify",
                # Read-aloud + conference link handling.
                "onSpeak",
                "meetingJoinUrl",
                "autoJoin",
                # Workbench shell: tabs plus the full-width toggle that hides
                # the chat column.
                "focusMode",
                "onToggleFocus",
                'meeting.tabs.transcript"',
                # Packaged WebView blocks window.open: conference links must go
                # through the gateway helper.
                "openExternalUrl",
            ),
            "webui/src/lib/tts.ts": (
                "export function speakOnce",
                "startVoiceSession",
                "endVoiceSession",
            ),
            "webui/src/lib/audio.ts": (
                "export async function segmentAudioBlob",
                "export async function convertBlobToWav",
                "export async function incrementalWavFromBlob",
            ),
            "webui/src/components/meeting/meetingTemplates.ts": (
                "BUILTIN_MEETING_TEMPLATES",
                "writeCustomTemplates",
                "executive",
            ),
            "webui/src/components/meeting/meetingIcs.ts": (
                "parseIcsEvents",
                "eventsStartingSoon",
                "export function meetingJoinUrl",
                "X-GOOGLE-CONFERENCE",
            ),
            "webui/src/components/meeting/meetingAudit.ts": (
                "appendAudit",
                "downloadAuditMarkdown",
            ),
            "webui/src/components/thread/ThreadComposer.tsx": (
                "mic:",
                "Mic",
            ),
            "webui/src/lib/navin-client.ts": ('"meeting"',),
        }
        for relative, tokens in checks.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            for token in tokens:
                with self.subTest(file=relative, token=token):
                    self.assertIn(token, text)

    def test_studio_cards_present(self) -> None:
        text = (
            ROOT / "webui/src/components/studio/StudioWorkspace.tsx"
        ).read_text(encoding="utf-8")
        for card_id in MEETING_CARDS:
            with self.subTest(card=card_id):
                self.assertIn(f'"{card_id}"', text)

    def test_i18n_meeting_keys(self) -> None:
        for locale in ("en", "fr"):
            path = ROOT / f"webui/src/i18n/locales/{locale}/common.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            with self.subTest(locale=locale):
                self.assertTrue(data["sidebar"]["meetingStudio"])
                self.assertIn("meeting", data["studio"])
                self.assertTrue(data["studio"]["meeting"]["title"])
                greetings = data["thread"]["empty"]["greetings"]["meeting"]
                self.assertEqual(set(greetings), {"a", "b", "c", "d"})
                self.assertIn("meeting", data["thread"]["sessionInfo"]["createSeed"])
                self.assertIn("meeting", data["thread"]["sessionInfo"]["modules"])
                self.assertIn("meetingCapture", data["studio"]["groups"])
                meeting = data["meeting"]
                self.assertEqual(
                    set(meeting["stt"]),
                    {
                        "title",
                        "ready",
                        "unavailable",
                        "open",
                        "configureModel",
                        "short",
                    },
                )
                # JSON object key order is not a UI contract. The workbench
                # declares the visible tab order explicitly; translations only
                # need to provide the complete key set.
                self.assertEqual(
                    set(meeting["tabs"]),
                    {
                        "transcript",
                        "report",
                        "notes",
                        "actions",
                        "calendar",
                        "templates",
                    },
                )
                for key in (
                    "generate",
                    "regenerate",
                    "print",
                    "summary",
                    "highlights",
                    "owner",
                    "due",
                    "empty",
                    "thin",
                ):
                    self.assertTrue(meeting["report"][key], key)
                for key in (
                    "reportFailed",
                    "noSession",
                    "printFailed",
                    "speakersTruncated",
                ):
                    self.assertTrue(meeting["errors"][key], key)
                for key in (
                    "chatSeed",
                    "speakersIdentify",
                    "speakersHint",
                    "speakerRename",
                    "transcriptEdit",
                    "transcriptRead",
                    "turnCount",
                ):
                    self.assertTrue(meeting[key], key)
                self.assertTrue(meeting["importProgress"])
                self.assertTrue(meeting["speak"])
                self.assertTrue(meeting["speakStop"])
                self.assertTrue(meeting["focusEnter"])
                self.assertTrue(meeting["focusExit"])
                self.assertTrue(meeting["brief"])
                self.assertTrue(meeting["search"])
                self.assertTrue(meeting["chatHint"])
                self.assertTrue(meeting["calendar"]["join"])
                self.assertTrue(meeting["calendar"]["autoJoin"])
                for key in ("sttDisabled", "sttNotConfigured", "tooLong", "tooLarge", "badFormat"):
                    self.assertTrue(meeting["errors"][key])


class MeetingSynthesisTest(unittest.IsolatedAsyncioTestCase):
    """Minutes and answers are one tool-less model call, not an agent turn."""

    def test_strip_report_fences(self) -> None:
        from navin.webui.meeting_api import strip_report_fences

        raw = "```markdown\n# Title\n## Goal\nBody\n```"
        self.assertEqual(strip_report_fences(raw), "## Goal\nBody")

    async def test_report_requires_material(self) -> None:
        from navin.webui.meeting_api import MeetingError, report_payload

        with self.assertRaises(MeetingError) as ctx:
            await report_payload(transcript="  ", notes="")
        self.assertEqual(ctx.exception.status, 400)

    async def test_answer_requires_question(self) -> None:
        from navin.webui.meeting_api import MeetingError, answer_payload

        with self.assertRaises(MeetingError):
            await answer_payload(question="", transcript="hello there")

    async def test_report_prompt_carries_template_and_transcript(self) -> None:
        from navin.webui import meeting_api

        seen: dict[str, str] = {}

        async def fake_ask(system: str, user: str, **_kwargs: object):
            seen["system"] = system
            seen["user"] = user
            return "## Goal\nShip it.", "test-model", "docs"

        original = meeting_api._ask
        meeting_api._ask = fake_ask  # type: ignore[assignment]
        try:
            payload = await meeting_api.report_payload(
                title="Weekly sync",
                template_name="Standard minutes",
                template_instructions="Goal, Decisions, Actions",
                transcript="We agreed to ship on Friday.",
                speakers=["Ana", "Bo"],
                language="fr",
            )
        finally:
            meeting_api._ask = original  # type: ignore[assignment]

        self.assertEqual(payload["markdown"], "## Goal\nShip it.")
        self.assertEqual(payload["model"], "test-model")
        self.assertIn("Weekly sync", seen["user"])
        self.assertIn("Goal, Decisions, Actions", seen["user"])
        self.assertIn("We agreed to ship on Friday.", seen["user"])
        self.assertIn("Ana, Bo", seen["user"])
        self.assertIn("Never invent", seen["system"])

    def test_diarization_chunks_stay_under_the_budget(self) -> None:
        from navin.webui.meeting_api import _SPEAKER_CHUNK_CHARS, split_for_diarization

        transcript = "\n".join(f"Line {i} with some spoken content." for i in range(400))
        chunks = split_for_diarization(transcript)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), _SPEAKER_CHUNK_CHARS)
        # No spoken content may be lost by the split.
        self.assertEqual(
            "".join(chunks).replace("\n", ""),
            transcript.replace("\n", ""),
        )

    def test_roster_from_labels_ignores_prose(self) -> None:
        from navin.webui.meeting_api import roster_from_labels

        text = (
            "Speaker 1: hello\n"
            "Aymen Ghadghadi: salut\n"
            "This line has no label at all\n"
            "Speaker 1: again\n"
        )
        self.assertEqual(roster_from_labels(text), ["Speaker 1", "Aymen Ghadghadi"])

    async def test_speakers_pass_carries_the_roster_forward(self) -> None:
        from navin.webui import meeting_api

        prompts: list[str] = []

        async def fake_ask(_system: str, user: str, **_kwargs: object):
            prompts.append(user)
            index = len(prompts)
            return f"Ana Ruiz: turn {index}", "test-model", "docs"

        original = meeting_api._ask
        meeting_api._ask = fake_ask  # type: ignore[assignment]
        try:
            payload = await meeting_api.speakers_payload(
                transcript="\n".join(f"Line {i} of the meeting." for i in range(600)),
            )
        finally:
            meeting_api._ask = original  # type: ignore[assignment]

        self.assertGreater(len(prompts), 1)
        self.assertEqual(payload["speakers"], ["Ana Ruiz"])
        # The second call must know the label the first one chose.
        self.assertIn("Ana Ruiz", prompts[1])
        self.assertFalse(payload["truncated"])

    async def test_speakers_pass_requires_a_transcript(self) -> None:
        from navin.webui.meeting_api import MeetingError, speakers_payload

        with self.assertRaises(MeetingError):
            await speakers_payload(transcript="   ")

    def test_gateway_exposes_the_route(self) -> None:
        text = (ROOT / "navin/webui/ws_http.py").read_text(encoding="utf-8")
        self.assertIn(r"^/api/sessions/([^/]+)/meeting$", text)
        self.assertIn(r"^/api/meeting$", text)
        self.assertIn("_handle_meeting", text)
        self.assertIn("unknown meeting mode", text)


class MeetingReportDeskTest(unittest.TestCase):
    """The desk renders and exports the report itself, chat-free."""

    def test_report_module_exports_builders(self) -> None:
        text = (
            ROOT / "webui/src/components/meeting/meetingReport.ts"
        ).read_text(encoding="utf-8")
        for token in (
            "export function analyzeMeeting",
            "export function buildReportMarkdown",
            "export function buildReportHtml",
            "export function detectHighlights",
            "export function buildTurns",
            "export function rosterFromTranscript",
            "export function renameSpeaker",
        ):
            self.assertIn(token, text, token)

    def test_workbench_synthesizes_without_the_chat(self) -> None:
        text = (
            ROOT / "webui/src/components/meeting/MeetingWorkbench.tsx"
        ).read_text(encoding="utf-8")
        for token in (
            "fetchMeetingReport",
            "fetchMeetingAnswer",
            "buildReportMarkdown",
            "buildReportHtml",
            "printReport",
            'tab === "report"',
            "fetchMeetingSpeakers",
            "identifySpeakers",
            "applySpeakerRename",
            "const exportBar",
        ):
            self.assertIn(token, text, token)
        # Exports must sit under the transcript and the notes, not only in the
        # Export tab.
        self.assertGreaterEqual(text.count("{exportBar}"), 2)
        # The old flow asked the agent to build an HTML report and then told the
        # user to print it from File Preview. It must not come back.
        self.assertNotIn("Export PDF in File Preview", text)
        self.assertNotIn("exportPdfViaStudio", text)

    def test_api_client_targets_the_meeting_route(self) -> None:
        text = (ROOT / "webui/src/lib/api.ts").read_text(encoding="utf-8")
        self.assertIn("meetingApiPath", text)
        self.assertIn("/meeting?mode=${mode}", text)
        self.assertIn("/api${suffix}", text)


if __name__ == "__main__":
    unittest.main()
