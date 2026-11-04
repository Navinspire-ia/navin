# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Collaboration Mode in the CLI: /team, the footer label, teammate replies."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from textual.widgets import Static

from navin.collab.runtime import SESSION_TEAM_KEY, current_collab
from navin.config.loader import load_config
from navin.session.manager import SessionManager
from navin.tui.app import _TUI_SLASH, NavinApp
from navin.tui.runtime import UiTeamMessage, team_face, team_job
from navin.tui.screens import HELP_MARKDOWN
from navin.tui.settled import SettledMarkdown
from navin.tui.team_view import TeamReply
from navin.tui.widgets import SystemNote
from tests.test_tui_queue import make_app

MARK = {"id": "mark", "name": "Mark", "title": "Senior Developer", "role": "developer",
        "avatar": "round", "color": "#3b82f6"}
SALMA = {"id": "salma", "name": "Salma", "title": "QA Engineer", "role": "tester",
         "avatar": "visor", "color": "#10b981"}
LEAD = {"id": "lead", "name": "Navin", "title": "", "role": "lead", "avatar": "navin", "color": "#6366f1"}
USER = {"id": "user", "name": "You", "title": "", "role": "user", "avatar": "user", "color": "#6b7280"}


def _team_app(tmp_path):
    app = make_app(tmp_path)
    sessions = SessionManager(tmp_path)
    app.runtime.agent_loop = SimpleNamespace(sessions=sessions)
    return app, sessions


def _footer(app) -> Static:
    return app.query_one("#meta-team", Static)


def _notes(app) -> list[str]:
    return [str(note.content) for note in app.query(SystemNote)]


def test_team_face_names_the_teammate_and_its_job():
    face = team_face(MARK, with_job=True)
    assert face == "[b #3b82f6]◉ Mark[/] [#3b82f6]· Senior Developer[/]"
    # Without a title the role is the job; the lead and the user have none.
    assert team_job({**MARK, "title": ""}) == "Developer"
    assert team_job({**MARK, "title": "", "role": "custom"}) == ""
    assert team_face(LEAD, with_job=True) == "[b #6366f1]◆ Navin[/]"
    assert team_face(MARK) == "[b #3b82f6]◉ Mark[/]"
    # A name with brackets stays literal text, never markup.
    assert team_face({"name": "[x]"}) == "[b]◉ \\[x][/b]"


def test_team_is_in_the_slash_menu_and_the_help():
    row = next(entry for entry in _TUI_SLASH if entry["command"] == "/team")
    assert row["accepts_args"] is True
    assert row["arg_hint"] == "[on|off|<team>|none|settings]"
    assert "`/team`" in HELP_MARKDOWN
    assert "`/team none`" in HELP_MARKDOWN
    assert "`/team settings`" in HELP_MARKDOWN


def test_team_on_saves_the_desktop_switch_and_shows_the_team(tmp_path):
    async def run():
        app, _sessions = _team_app(tmp_path)
        async with app.run_test(size=(120, 36)) as pilot:
            assert current_collab().enabled is False
            await app.submit_text("/team")
            await pilot.pause()
            assert "Team mode:[/b] off" in _notes(app)[-1]
            assert _footer(app).display is False
            await app.submit_text("/team on")
            await pilot.pause()
            # Saved where the desktop reads it, and seen by the engine at once.
            assert load_config().collab.enabled is True
            assert current_collab().enabled is True
            status = _notes(app)[-1]
            assert "[$success]on[/]" in status
            assert "This chat:[/b] Dev [dim](default team)[/dim]" in status
            assert "Mark - Senior Developer" in status
            assert "Salma - QA Engineer" in status
            assert "Michel - Software Architect" in status
            assert "/team on | /team off | /team <team> | /team none" in status
            assert str(_footer(app).content) == "Team: Dev"
            assert _footer(app).display is True
            # Nothing reached the engine: /team is the CLI's own command.
            assert app.runtime.bus.inbound_size == 0
            await app.submit_text("/team off")
            await pilot.pause()
            assert load_config().collab.enabled is False
            assert _footer(app).display is False
            assert "Team mode:[/b] off" in _notes(app)[-1]
    asyncio.run(run())


def test_team_picks_this_chats_team_none_and_lists_the_valid_ones(tmp_path):
    async def run():
        app, sessions = _team_app(tmp_path)
        async with app.run_test(size=(120, 36)) as pilot:
            await app.submit_text("/team on")
            await app.submit_text("/team Sales")
            await pilot.pause()
            key = app.runtime.session_key
            assert sessions.get_or_create(key).metadata[SESSION_TEAM_KEY] == "sales"
            # Saved on disk, like the desktop's team_select.
            assert SessionManager(tmp_path).get_or_create(key).metadata[SESSION_TEAM_KEY] == "sales"
            assert str(_footer(app).content) == "Team: Sales"
            assert "Jack - Sales Developer" in _notes(app)[-1]
            await app.submit_text("/team nowhere")
            await pilot.pause()
            refusal = _notes(app)[-1]
            assert "No team nowhere." in refusal
            assert "dev, sales, marketing, seo, ads, devops, infra" in refusal
            assert sessions.get_or_create(key).metadata[SESSION_TEAM_KEY] == "sales"
            await app.submit_text("/team none")
            await pilot.pause()
            assert sessions.get_or_create(key).metadata[SESSION_TEAM_KEY] == "off"
            assert "This chat:[/b] no team" in _notes(app)[-1]
            assert _footer(app).display is False
            # Turned on again in a chat set to no team: the default team.
            await app.submit_text("/team off")
            await app.submit_text("/team on")
            await pilot.pause()
            assert sessions.get_or_create(key).metadata[SESSION_TEAM_KEY] == "dev"
            assert str(_footer(app).content) == "Team: Dev"
    asyncio.run(run())


def test_the_footer_follows_the_chat_on_screen(tmp_path):
    async def run():
        app, sessions = _team_app(tmp_path)
        async with app.run_test(size=(120, 36)) as pilot:
            await app.submit_text("/team on")
            other = sessions.get_or_create("cli:other")
            other.metadata[SESSION_TEAM_KEY] = "devops"
            sessions.save(other)
            await app.runtime.switch_session("cli:other")
            # The real side refresh, as after every chat switch.
            NavinApp._refresh_side(app)
            await pilot.pause()
            assert str(_footer(app).content) == "Team: DevOps"
            none = sessions.get_or_create("cli:none")
            none.metadata[SESSION_TEAM_KEY] = "off"
            sessions.save(none)
            await app.runtime.switch_session("cli:none")
            NavinApp._refresh_side(app)
            await pilot.pause()
            assert _footer(app).display is False
    asyncio.run(run())


def test_a_teammate_report_and_question_read_as_its_own_reply(tmp_path):
    async def run():
        app = make_app(tmp_path)
        async with app.run_test(size=(120, 40)) as pilot:
            await app._on_runtime_event(UiTeamMessage("assign", LEAD, MARK, "Fix the login form", None))
            await app._on_runtime_event(UiTeamMessage("message", MARK, SALMA, "Can you test it?", None))
            await app._on_runtime_event(
                UiTeamMessage("result", MARK, LEAD, "Fixed **login**.\n\n- form\n- tests", "ok"),
            )
            await app._on_runtime_event(UiTeamMessage("message", SALMA, USER, "Which browser?", None))
            await app._on_runtime_event(UiTeamMessage("result", SALMA, LEAD, "Stopped: no access", "error"))
            await pilot.pause()
            # Assignments and peer messages stay compact rows, with the job.
            rows = _notes(app)
            assert rows[0].startswith("[b #6366f1]◆ Navin[/] [dim]→[/dim] [b #3b82f6]◉ Mark[/]  [dim]task[/dim]")
            assert rows[1].startswith(
                "[b #3b82f6]◉ Mark[/] [#3b82f6]· Senior Developer[/] [dim]→[/dim] [b #10b981]▣ Salma[/]"
            )
            assert len(rows) == 2
            replies = list(app.query(TeamReply))
            assert [reply.sender["name"] for reply in replies] == ["Mark", "Salma", "Salma"]
            report, question, stopped = replies
            head = report.query_one(".team-reply-head", Static)
            assert str(head.content) == (
                "[b #3b82f6]◉ Mark[/] [#3b82f6]· Senior Developer[/]  [dim]report[/dim]"
            )
            # The content is Markdown, rendered like an answer.
            assert report.query_one(SettledMarkdown).markdown == "Fixed **login**.\n\n- form\n- tests"
            assert report.copy_text() == "Fixed **login**.\n\n- form\n- tests"
            assert report.speaker == "Mark - Senior Developer"
            assert report.styles.border_left[1].hex.lower() == "#3b82f6"
            assert str(question.query_one(".team-reply-head", Static).content).endswith("[dim]to you[/dim]")
            assert stopped.has_class("-stopped")
            assert str(stopped.query_one(".team-reply-head", Static).content).endswith("[dim]stopped[/dim]")
            # Off screen it is kept as data and comes back the same.
            record = report.snapshot_record()
            assert record is not None
            assert isinstance(record, TeamReply)
            assert record.speaker == "Mark - Senior Developer"
            assert record.copy_text() == report.copy_text()
            rebuilt = record.build()
            assert (rebuilt.sender, rebuilt.markdown, rebuilt.tag) == (MARK, report.markdown, "report")
    asyncio.run(run())


def test_live_reads_a_teammate_reply_with_its_own_voice():
    """CLI Live: Mark and Salma speak with a voice of their gender, not Navin's;
    a teammate with no gender keeps Navin's voice and is named first."""
    from types import SimpleNamespace

    from navin.audio.models import voice_profile
    from navin.config.schema import Config
    from navin.tui.app import NavinApp
    from navin.tui.live_voice import speech_text
    from navin.tui.runtime import UiTeamMessage
    from navin.webui.voice_api import teammate_voice_for

    config = Config()
    config.voice.tts_provider = "openai"
    config.voice.tts_model = "gpt-4o-mini-tts"
    config.voice.voice = "coral"
    said: list[tuple[str, str]] = []
    app = SimpleNamespace(
        _live=SimpleNamespace(config=config),
        _live_text=lambda key: "Code in the terminal.",
        _live_say=lambda text, voice="": said.append((text, voice)),
    )
    lead = {"id": "lead", "name": "Navin"}
    for sender in (
        {"id": "mark", "name": "Mark", "gender": "male"},
        {"id": "salma", "name": "Salma", "gender": "female"},
        {"id": "sofia", "name": "Sofia"},
    ):
        NavinApp._live_say_teammate(app, UiTeamMessage("result", sender, lead, "Tests are **green**.", "ok"))
    expected = speech_text("Tests are **green**.", code_note="Code in the terminal.")
    (mark_text, mark_voice), (salma_text, salma_voice), (sofia_text, sofia_voice) = said
    assert voice_profile(mark_voice)[0] == "male" and voice_profile(salma_voice)[0] == "female"
    assert "coral" not in {mark_voice, salma_voice}
    assert mark_text == salma_text == expected
    assert (sofia_text, sofia_voice) == (f"Sofia: {expected}", "")
    # No language from the CLI: the lead's own voice decides it (Lea speaks French).
    assert teammate_voice_for("aws/polly-neural", "Lea", chosen="", gender="male", language="", key="mark") in {"Remi", "Liam"}


# -- Settings > Team in the terminal -----------------------------------------


def _hub_rows(hub) -> dict[str, object]:
    return {field.label: field for field in hub._rows}


async def _settle(hub, pilot) -> None:
    await pilot.pause()
    await hub.workers.wait_for_complete()
    await pilot.pause()


def test_team_settings_opens_the_desktops_team_editor(tmp_path):
    from navin.tui.settings import SettingsHub

    async def run():
        app, _sessions = _team_app(tmp_path)
        async with app.run_test(size=(140, 44)) as pilot:
            await app.submit_text("/team settings")
            await pilot.pause()
            hub = app.screen
            assert isinstance(hub, SettingsHub)
            assert hub._section.id == "team"
            # Nothing reached the engine.
            assert app.runtime.bus.inbound_size == 0
            rows = _hub_rows(hub)
            for label in ("Collaboration Mode", "This chat's team", "Default team", "Collaboration pattern",
                          "Maximum rounds", "Token budget per teammate", "Manager email", "Email contacts",
                          "Dev", "Sales", "Mark", "Salma", "New teammate", "New team"):
                assert label in rows, label
            assert "Mark" in rows["Dev"].text and "Salma" in rows["Dev"].text
            assert "Senior Developer" in rows["Mark"].text
            hub.dismiss(False)
            await pilot.pause()
            # /settings team opens the same section.
            await app.submit_text("/settings team")
            await pilot.pause()
            assert isinstance(app.screen, SettingsHub) and app.screen._section.id == "team"
    asyncio.run(run())


def test_team_settings_save_through_the_desktop_save_at_once(tmp_path):
    async def run():
        app, sessions = _team_app(tmp_path)
        async with app.run_test(size=(140, 44)) as pilot:
            await app.submit_text("/team settings")
            await pilot.pause()
            hub = app.screen
            rows = _hub_rows(hub)
            hub._activate(rows["Collaboration Mode"], hub._rows.index(rows["Collaboration Mode"]))
            hub._write(rows["Maximum rounds"], 7)
            hub._write(rows["Email contacts"], "Ana <ana@corp.io>, bob@corp.io")
            await _settle(hub, pilot)
            collab = load_config().collab
            assert collab.enabled is True and current_collab().enabled is True
            assert collab.max_rounds == 7
            assert collab.email_contacts == [
                {"name": "Ana", "email": "ana@corp.io"}, {"name": "bob@corp.io", "email": "bob@corp.io"},
            ]
            assert hub._format(_hub_rows(hub)["Collaboration Mode"]) == "[$success]● on[/]"
            # A refused value says why and saves nothing.
            hub._write(_hub_rows(hub)["Manager email"], "not-an-email")
            await _settle(hub, pilot)
            assert "Team not saved" in str(hub.query_one("#foot .status", Static).content)
            assert load_config().collab.manager_email == ""
            # Ctrl+S of the hub never writes an older team back.
            hub.action_save()
            assert load_config().collab.max_rounds == 7
            # This chat's team, like the desktop's picker.
            hub._write(_hub_rows(hub)["This chat's team"], "sales")
            await _settle(hub, pilot)
            assert sessions.get_or_create(app.runtime.session_key).metadata[SESSION_TEAM_KEY] == "sales"
    asyncio.run(run())


def test_team_settings_add_a_teammate_to_a_team_then_remove_it(tmp_path):
    from textual.widgets import Input, Select

    from navin.tui.screens import FormScreen, PickerScreen

    async def run():
        app, _sessions = _team_app(tmp_path)
        async with app.run_test(size=(140, 50)) as pilot:
            await app.submit_text("/team settings")
            await pilot.pause()
            hub = app.screen
            hub.run_worker(hub._team_action("member_new", ""), exclusive=False)
            await pilot.pause()
            form = app.screen
            assert isinstance(form, FormScreen)
            form.query_one("#f-name", Input).value = "Sofia"
            form.query_one("#f-title", Input).value = "Backend Developer"
            form.query_one("#f-team", Select).value = "sales"
            form._submit()
            await _settle(hub, pilot)
            collab = load_config().collab
            sofia = next(m for m in collab.members if m.name == "Sofia")
            assert sofia.role == "developer" and sofia.title == "Backend Developer"
            assert "write_file" in sofia.tools and sofia.prompt
            assert sofia.id in next(t for t in collab.teams if t.id == "sales").members
            assert "Sofia" in _hub_rows(hub)
            hub.run_worker(hub._team_action("member_delete", sofia.id), exclusive=False)
            await pilot.pause()
            assert isinstance(app.screen, PickerScreen)
            app.screen.dismiss("yes")
            await _settle(hub, pilot)
            collab = load_config().collab
            assert all(m.name != "Sofia" for m in collab.members)
            assert sofia.id not in next(t for t in collab.teams if t.id == "sales").members
    asyncio.run(run())


def test_team_forms_follow_the_role_and_keep_customisations():
    from navin.collab.team import ROLE_PROMPTS, ROLE_TOOLS, default_member
    from navin.tui.team_settings import (
        apply_member_answer,
        apply_team_answer,
        format_contacts,
        parse_contacts,
        team_form_fields,
    )

    mark = default_member("developer", id="mark", name="Mark")
    answer = {"name": "Mark", "role": "tester", "tools": ", ".join(mark["tools"]), "skills": "git, custom-skill",
              "reasoning_effort": "medium", "enabled": "true"}
    moved = apply_member_answer(mark, answer)
    # Defaults follow the new role; a customised list stays.
    assert moved["tools"] == list(ROLE_TOOLS["tester"])
    assert moved["prompt"] == ROLE_PROMPTS["tester"]
    assert moved["skills"] == ["git", "custom-skill"]
    assert moved["id"] == "mark"
    fields = team_form_fields({"id": "dev", "name": "Dev", "members": ["mark"]}, [mark])
    assert [f.name for f in fields] == ["name", "m_mark"] and fields[1].value == "true"
    assert apply_team_answer({"id": "dev"}, {"name": "Core", "m_mark": "false"}, [mark]) == {
        "id": "dev", "name": "Core", "members": [],
    }
    contacts = parse_contacts("Ana <ana@corp.io>, bob@corp.io")
    assert format_contacts(contacts) == "Ana <ana@corp.io>, bob@corp.io"


def test_the_cli_teammate_card_offers_the_efforts_of_the_default_team():
    """Mark runs at xhigh and Michel at max: the card must show and keep them."""
    from navin.collab.team import MEMBER_EFFORTS, default_member
    from navin.tui.team_settings import apply_member_answer, member_form_fields

    mark = default_member("developer", id="mark", name="Mark", reasoning_effort="xhigh")
    effort = next(f for f in member_form_fields(mark, {}) if f.name == "reasoning_effort")
    assert [key for key, _ in effort.options] == list(MEMBER_EFFORTS)
    assert effort.value == "xhigh" and "max" in dict(effort.options)
    kept = apply_member_answer(mark, {"name": "Mark", "role": "developer", "reasoning_effort": "xhigh"})
    assert kept["reasoning_effort"] == "xhigh"
