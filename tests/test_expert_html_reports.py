"""Expert Review / Security / Debug: HTML report + choice plan + real examples."""

from __future__ import annotations

import asyncio
import queue
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from navin.agent.model_routes import WORKFLOW_ROUTE_ROLES, workflow_role_for_content
from navin.agent.tools.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from navin.agent.tools.set_composer_mode import SetComposerModeTool
from navin.bus.outbound_events import ComposerModeRequestedEvent
from navin.command.builtin import (
    _COMPOSER_MODE_BY_COMMAND,
    _DELIVERY_RUN_CLAUSE,
    _DELIVERY_WORKFLOWS,
    _EVIDENCE_ONLY_CLAUSE,
    _EXPERT_REPORT_CLAUSE,
    _EXPERT_REPORT_WORKFLOWS,
    _HTML_REPORT_CLAUSE,
    _HTML_REPORT_WORKFLOWS,
    _WORKFLOW_BRIEFS,
    _workflow_handler,
)
from navin.command.modules import (
    EVIDENCE_ONLY_METADATA_KEY,
    PRELOAD_SKILLS_METADATA_KEY,
    REQUIRES_TOOL_DELIVERY_METADATA_KEY,
)

_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _ROOT / "navin" / "skills" / "studio-html-report" / "SKILL.md"

_EXPECTED = {
    "/inspect": {
        "mode": "review",
        "role": "review",
        "report": "review-report-",
        "set_mode": "set_composer_mode(mode=review)",
        "shown": "The editor already shows Review mode",
    },
    "/fortify": {
        "mode": "security",
        "role": "security",
        "report": "security-report-",
        "set_mode": "set_composer_mode(mode=security)",
        "shown": "The editor already shows Security mode",
    },
    "/debug": {
        "mode": "debug",
        "role": "deep",
        "report": "debug-report-",
        "set_mode": "set_composer_mode(mode=debug)",
        "shown": "The editor already shows Debug mode",
    },
}


def _run_brief(command: str, *, channel: str = "cli", with_bus: bool = False):
    outbound: queue.Queue = queue.Queue()
    bus = SimpleNamespace(outbound=outbound) if with_bus else None
    loop = SimpleNamespace(bus=bus) if with_bus else None
    msg = SimpleNamespace(
        content="",
        metadata={},
        channel=channel,
        chat_id="chat-expert",
    )
    ctx = SimpleNamespace(
        args="auth module",
        raw=f"{command} auth module",
        msg=msg,
        loop=loop,
    )
    asyncio.run(_workflow_handler(command)(ctx))  # type: ignore[arg-type]
    return msg.content, dict(msg.metadata), outbound


class ExpertWorkflowWiringTest(unittest.TestCase):
    def test_expert_sets_are_consistent(self) -> None:
        self.assertEqual(
            _EXPERT_REPORT_WORKFLOWS,
            frozenset({"/inspect", "/fortify", "/debug"}),
        )
        self.assertTrue(_EXPERT_REPORT_WORKFLOWS <= _HTML_REPORT_WORKFLOWS)
        self.assertTrue(_EXPERT_REPORT_WORKFLOWS <= _DELIVERY_WORKFLOWS)
        self.assertTrue(_EXPERT_REPORT_WORKFLOWS <= set(_WORKFLOW_BRIEFS))

    def test_each_expert_brief_demands_html_real_examples_and_choice(self) -> None:
        markers = (
            "File Preview",
            "REAL",
            "which #",
        )
        for command, expect in _EXPECTED.items():
            with self.subTest(command=command):
                title, skills, brief = _WORKFLOW_BRIEFS[command]
                self.assertTrue(title)
                self.assertTrue(skills)
                for marker in markers:
                    self.assertIn(marker, brief, f"{command} missing {marker}")
                self.assertIn(expect["report"], brief)
                # The server tints the composer when the workflow starts
                # (test_websocket_start_emits_composer_mode_event); the brief
                # must not spend a model round-trip re-asking for it.
                self.assertNotIn(expect["set_mode"], brief)
                self.assertIn(expect["shown"], brief)
                if command != "/debug":
                    self.assertIn("file:line", brief)
                else:
                    self.assertIn("REAL evidence", brief)

    def test_handler_injects_all_clauses_skills_and_delivery(self) -> None:
        for command, expect in _EXPECTED.items():
            with self.subTest(command=command):
                content, meta, _ = _run_brief(command)
                self.assertIn(_DELIVERY_RUN_CLAUSE, content)
                self.assertIn(_HTML_REPORT_CLAUSE, content)
                self.assertIn(_EXPERT_REPORT_CLAUSE, content)
                self.assertIn(_EVIDENCE_ONLY_CLAUSE, content)
                self.assertIn("REAL example", content)
                self.assertIn("Start with #1", content)
                self.assertIn(expect["report"].rstrip("-"), content)
                self.assertTrue(meta.get(REQUIRES_TOOL_DELIVERY_METADATA_KEY))
                self.assertTrue(meta.get(EVIDENCE_ONLY_METADATA_KEY))
                skills = meta.get(PRELOAD_SKILLS_METADATA_KEY) or []
                self.assertIn("studio-html-report", skills)
                self.assertEqual(meta.get("composer_mode"), expect["mode"])
                self.assertEqual(meta.get("original_command"), command)

    def test_websocket_start_emits_composer_mode_event(self) -> None:
        for command, expect in _EXPECTED.items():
            with self.subTest(command=command):
                _content, meta, outbound = _run_brief(
                    command, channel="websocket", with_bus=True
                )
                self.assertEqual(meta.get("composer_mode"), expect["mode"])
                self.assertFalse(outbound.empty())
                msg = outbound.get_nowait()
                event = getattr(msg, "event", None)
                self.assertIsInstance(event, ComposerModeRequestedEvent)
                self.assertEqual(event.mode, expect["mode"])


class ExpertModelRouteTest(unittest.TestCase):
    def test_routes_match_expert_modes(self) -> None:
        for command, expect in _EXPECTED.items():
            with self.subTest(command=command):
                self.assertEqual(WORKFLOW_ROUTE_ROLES.get(command), expect["role"])
                self.assertEqual(
                    workflow_role_for_content(f"{command} scope"),
                    expect["role"],
                )
                self.assertEqual(
                    _COMPOSER_MODE_BY_COMMAND.get(command),
                    expect["mode"],
                )


class StudioHtmlReportSkillTest(unittest.TestCase):
    def test_skill_file_covers_expert_missions(self) -> None:
        self.assertTrue(_SKILL.is_file(), f"missing {_SKILL}")
        text = _SKILL.read_text(encoding="utf-8")
        for name in (
            "review-report-",
            "security-report-",
            "debug-report-",
            "open_file_preview",
            "Real example",
            "Remediation plan",
            'Start with #1',
            "PoC",
            "Severity",
            "@media print",
        ):
            self.assertIn(name, text, f"skill missing {name!r}")

        # Expert track: no JS interactivity (File Preview sandboxes scripts).
        lowered = text.lower()
        self.assertIn("no js interactivity", lowered)
        self.assertIn("no javascript", lowered)
        self.assertNotRegex(text, r"<script[\s>]", re.I)

        # Studio / GTM track: interactive Three.js UI, not PDF.
        self.assertIn("Track A", text)
        self.assertIn("@react-three/fiber", text)
        self.assertIn("open_preview", text)
        self.assertIn("not PDF", text)


class SampleExpertHtmlReportTest(unittest.TestCase):
    """Golden HTML shaped like the skill - validates the deliverable contract."""

    def test_sample_report_has_required_structure(self) -> None:
        html = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Security report</title>
<style>
:root{--bg:#0a0e1a;--card:#121826;--text:#e8eefc;--muted:#9aa8c7;
--accent:#5b8cff;--danger:#f07178;--warn:#f0b429}
body{background:var(--bg);color:var(--text);font-family:ui-sans-serif,system-ui,sans-serif}
.card{background:var(--card);border:1px solid #243049;border-radius:14px;padding:1rem;margin:.75rem 0}
.sev-critical{color:var(--danger)}.sev-high{color:var(--warn)}.num{color:var(--accent);font-weight:700}
pre{background:#0d1117;padding:.75rem;overflow:auto;white-space:pre-wrap}
@media print{body{background:#fff;color:#000}}
</style></head><body>
<header><h1>Security report</h1><p>Scope: auth · 2026-08-02</p></header>
<section class="summary">
  <h2>Executive summary</h2>
  <ul><li>Critical: 1</li><li>High: 1</li><li>Medium: 0</li><li>Low: 0</li></ul>
</section>
<article class="card finding">
  <span class="sev-critical">Critical</span>
  <h3>SQL injection in login</h3>
  <p>Location: api/auth.py:42</p>
  <p>Impact: full account takeover via OR 1=1</p>
  <p>Real example:</p>
  <pre>query = f"SELECT * FROM users WHERE email='{email}'"
# PoC: email = ' OR 1=1--</pre>
  <p>Fix: use parameterized query</p>
</article>
<section class="plan">
  <h2>Remediation plan - choose where to start</h2>
  <p class="hint">Reply in chat with the number, e.g. "Start with #1".</p>
  <article class="choice card">
    <header><span class="num">#1</span> <span class="sev-critical">Critical</span> Parameterize login SQL</header>
    <p>Effort: S · Risk if delayed: dumpable users table</p>
    <p>First step: replace f-string query in api/auth.py:42</p>
  </article>
  <article class="choice card">
    <header><span class="num">#2</span> <span class="sev-high">High</span> Add CSRF on session cookie</header>
    <p>Effort: M · Risk if delayed: state-changing CSRF</p>
    <p>First step: enable CSRF middleware on POST /login</p>
  </article>
</section>
<section class="deliverables">
  <h2>Deliverables</h2>
  <table><tr><th>Path</th><th>Format</th><th>Contents</th></tr>
  <tr><td>security-report-20260802-134500.html</td><td>HTML</td><td>Full report</td></tr>
  </table>
</section>
<footer>Generated by Navin</footer>
</body></html>"""
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("Executive summary", html)
        self.assertIn("Critical: 1", html)
        self.assertIn("api/auth.py:42", html)
        self.assertIn("Real example", html)
        self.assertIn("OR 1=1", html)
        self.assertIn('class="plan"', html)
        self.assertIn("#1", html)
        self.assertIn("#2", html)
        self.assertIn("Start with #1", html)
        self.assertIn("Deliverables", html)
        self.assertIn("@media print", html)
        self.assertNotRegex(html, r"<script[\s>]", re.I)
        # Downloadable standalone: inline CSS only
        self.assertIn("<style>", html)
        self.assertNotIn('href="http', html)


class ToolDiscoveryTest(unittest.TestCase):
    def test_set_composer_mode_is_auto_discovered(self) -> None:
        from navin.agent.tools.loader import ToolLoader

        discovered = {cls.__name__ for cls in ToolLoader().discover()}
        self.assertIn("SetComposerModeTool", discovered)


class WebsocketComposerModePayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_send_composer_mode_request_shape(self) -> None:
        import json

        from navin.channels.websocket import WebSocketChannel

        sent: list[str] = []

        class _Conn:
            async def send(self, raw: str) -> None:
                sent.append(raw)

        channel = WebSocketChannel.__new__(WebSocketChannel)
        channel._subs = {"chat-1": (_Conn(),)}  # type: ignore[attr-defined]

        async def _safe(connection, raw, label=""):
            await connection.send(raw)

        channel._safe_send_to = _safe  # type: ignore[method-assign]
        await channel.send_composer_mode_request(
            "chat-1",
            ComposerModeRequestedEvent(mode="review"),
        )
        self.assertEqual(len(sent), 1)
        body = json.loads(sent[0])
        self.assertEqual(
            body,
            {
                "event": "composer_mode_request",
                "chat_id": "chat-1",
                "mode": "review",
            },
        )


class SetComposerModeToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_emits_outbound_event_on_websocket(self) -> None:
        outbound: queue.Queue = queue.Queue()
        bus = SimpleNamespace(outbound=outbound)
        tool = SetComposerModeTool(bus=bus)
        token = bind_request_context(
            RequestContext(channel="websocket", chat_id="c1", sender_id="u1")
        )
        try:
            result = await tool.execute(mode="security")
        finally:
            reset_request_context(token)
        self.assertIn("Security", str(result))
        msg = outbound.get_nowait()
        self.assertIsInstance(msg.event, ComposerModeRequestedEvent)
        self.assertEqual(msg.event.mode, "security")

    async def test_rejects_invalid_mode(self) -> None:
        tool = SetComposerModeTool(bus=MagicMock())
        result = await tool.execute(mode="ask")
        self.assertTrue(getattr(result, "is_error", False) or "Error" in str(result))


if __name__ == "__main__":
    unittest.main()
