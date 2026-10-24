# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""/loop: parsing, fixed cron loops, dynamic self-paced loops, list/stop."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from navin.agent.tools.context import RequestContext, request_context
from navin.agent.tools.cron import CronTool
from navin.bus.events import InboundMessage
from navin.command.builtin import (
    BUILTIN_COMMAND_SPECS,
    is_agent_turn_command,
    register_builtin_commands,
)
from navin.command.loop import (
    LOOP_USAGE,
    dynamic_tick_message,
    loop_name,
    parse_loop_args,
)
from navin.command.router import CommandContext, CommandRouter
from navin.cron.service import CronService


@pytest.mark.parametrize(
    "text,prompt,every,cron_expr",
    [
        # Leading interval.
        ("5m /foo", "/foo", 300, None),
        ("30s check status", "check status", 30, None),
        ("2h run report", "run report", 7200, None),
        ("1d summarize the inbox", "summarize the inbox", 86400, None),
        ("10 minutes check the queue", "check the queue", 600, None),
        ("every 15m poll CI", "poll CI", 900, None),
        ("every hour check prices", "check prices", 3600, None),
        # Trailing interval, English and French.
        ("check deploy every 5m", "check deploy", 300, None),
        ("run tests every 10 minutes", "run tests", 600, None),
        ("check the build every hour", "check the build", 3600, None),
        ("verifie le deploiement toutes les 10 minutes", "verifie le deploiement", 600, None),
        ("fais le point toutes les heures", "fais le point", 3600, None),
        ("sauvegarde tous les 2 jours", "sauvegarde", 172800, None),
        ("relance la veille chaque minute", "relance la veille", 60, None),
        # Below the floor: clamped to 30s.
        ("5s ping", "ping", 30, None),
        # Cron-style phrases.
        ("standup every weekday at 9am", "standup", None, "0 9 * * 1-5"),
        ("every weekday at 9am standup", "standup", None, "0 9 * * 1-5"),
        ("post the report every day at 17:30", "post the report", None, "30 17 * * *"),
        ("review backlog every monday at 10am", "review backlog", None, "0 10 * * 1"),
        ("daily at 8pm send digest", "send digest", None, "0 20 * * *"),
        ("point equipe tous les jours à 9h", "point equipe", None, "0 9 * * *"),
        ("rapport chaque vendredi à 16h30", "rapport", None, "30 16 * * 5"),
        ("en semaine à 8h30 revue des PR", "revue des PR", None, "30 8 * * 1-5"),
        # No interval: dynamic.
        ("watch the CI run", "watch the CI run", None, None),
        ("surveille le deploiement", "surveille le deploiement", None, None),
        ("5 apples to buy", "5 apples to buy", None, None),
    ],
)
def test_parse_loop_args_table(text, prompt, every, cron_expr):
    spec = parse_loop_args(text)
    assert spec is not None
    assert spec.prompt == prompt
    assert spec.every_seconds == every
    assert spec.cron_expr == cron_expr
    assert spec.mode == ("fixed" if (every or cron_expr) else "dynamic")


@pytest.mark.parametrize("text", ["", "   "])
def test_parse_loop_args_empty(text):
    assert parse_loop_args(text) is None


def test_loop_name_is_stable_slug():
    assert loop_name("Check the deploy status") == "loop-check-deploy-status"
    assert loop_name("Check the deploy status") == loop_name("check  THE deploy status")
    assert loop_name("/board status") == "loop-board-status"
    assert loop_name("Vérifie le déploiement") == "loop-verifie-deploiement"
    assert loop_name("!!!") == "loop-task"


def test_loop_is_registered_and_lifecycle():
    assert any(spec.command == "/loop" for spec in BUILTIN_COMMAND_SPECS)
    assert is_agent_turn_command("/loop 5m check deploy")
    assert is_agent_turn_command("/loop watch the CI")
    assert not is_agent_turn_command("/loop")
    assert not is_agent_turn_command("/loop list")
    assert not is_agent_turn_command("/loop stop all")


def _harness(tmp_path):
    cron = CronService(tmp_path / "cron" / "jobs.json")
    loop = SimpleNamespace(cron_service=cron, context=SimpleNamespace(timezone="Europe/Paris"))
    router = CommandRouter()
    register_builtin_commands(router)
    return cron, loop, router


async def _send(router, loop, text, *, session=True, chat_id="c1"):
    msg = InboundMessage(channel="websocket", sender_id="u", chat_id=chat_id, content=text)
    ctx = CommandContext(
        msg=msg,
        session=object() if session else None,
        key=msg.session_key,
        raw=text,
        loop=loop,
        is_user_turn=True,
    )
    return await router.dispatch(ctx), msg


@pytest.mark.asyncio
async def test_empty_loop_returns_usage(tmp_path):
    _cron, loop, router = _harness(tmp_path)
    reply, _ = await _send(router, loop, "/loop")
    assert reply is not None and reply.content == LOOP_USAGE


@pytest.mark.asyncio
async def test_fixed_loop_creates_named_cron_job_and_runs_now(tmp_path):
    cron, loop, router = _harness(tmp_path)
    reply, msg = await _send(router, loop, "/loop 5m check deploy")
    assert reply is None  # the first tick runs as the normal agent turn
    assert "check deploy" in msg.content
    assert msg.metadata["original_content"] == "/loop 5m check deploy"
    assert msg.metadata["loop_mode"] == "fixed"

    jobs = cron.list_jobs()
    assert [j.name for j in jobs] == ["loop-check-deploy"]
    job = jobs[0]
    assert job.schedule.kind == "every" and job.schedule.every_ms == 300_000
    assert job.payload.session_key == "websocket:c1"
    assert job.payload.origin_channel == "websocket"
    assert job.payload.origin_chat_id == "c1"
    assert "check deploy" in job.payload.message
    assert "do not create, change or remove cron jobs" in job.payload.message


@pytest.mark.asyncio
async def test_fixed_loop_same_prompt_replaces_existing(tmp_path):
    cron, loop, router = _harness(tmp_path)
    await _send(router, loop, "/loop 5m check deploy")
    first_id = cron.list_jobs()[0].id
    _reply, msg = await _send(router, loop, "/loop check deploy every 10 minutes")
    jobs = cron.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].id != first_id
    assert jobs[0].schedule.every_ms == 600_000
    assert "updated" in msg.content


@pytest.mark.asyncio
async def test_cron_phrase_loop_uses_loop_timezone(tmp_path):
    cron, loop, router = _harness(tmp_path)
    await _send(router, loop, "/loop standup every weekday at 9am")
    job = cron.list_jobs()[0]
    assert job.schedule.kind == "cron"
    assert job.schedule.expr == "0 9 * * 1-5"
    assert job.schedule.tz == "Europe/Paris"


@pytest.mark.asyncio
async def test_loop_list_and_stop(tmp_path):
    cron, loop, router = _harness(tmp_path)
    await _send(router, loop, "/loop 5m check deploy")
    await _send(router, loop, "/loop 1h run report")
    await _send(router, loop, "/loop 1h other chat task", chat_id="c2")

    reply, _ = await _send(router, loop, "/loop list")
    assert "loop-check-deploy" in reply.content
    assert "loop-run-report" in reply.content
    assert "loop-other-chat-task" not in reply.content  # another chat's loop

    reply, _ = await _send(router, loop, "/loop stop check-deploy")
    assert "loop-check-deploy" in reply.content
    names = {j.name for j in cron.list_jobs()}
    assert names == {"loop-run-report", "loop-other-chat-task"}

    reply, _ = await _send(router, loop, "/loop stop nope")
    assert "No loop named loop-nope" in reply.content

    reply, _ = await _send(router, loop, "/loop stop all")
    assert "loop-run-report" in reply.content
    assert {j.name for j in cron.list_jobs()} == {"loop-other-chat-task"}

    reply, _ = await _send(router, loop, "/loop list")
    assert "No loops in this chat" in reply.content


@pytest.mark.asyncio
async def test_loop_list_works_without_session(tmp_path):
    # list/stop are side-channel: they answer inline even mid-turn.
    _cron, loop, router = _harness(tmp_path)
    reply, _ = await _send(router, loop, "/loop list", session=False)
    assert reply is not None and "No loops" in reply.content


@pytest.mark.asyncio
async def test_dynamic_loop_injects_rearm_instructions_and_no_job(tmp_path):
    cron, loop, router = _harness(tmp_path)
    reply, msg = await _send(router, loop, "/loop watch the CI run")
    assert reply is None
    assert cron.list_jobs() == []
    assert msg.metadata["loop_mode"] == "dynamic"
    assert msg.metadata["preload_skills"] == ["cron"]
    content = msg.content
    assert "first tick" in content
    assert 'cron(action="add", name="loop-watch-ci-run"' in content
    assert "copied verbatim" in content
    assert "1-5 minutes" in content and "15-60 minutes" in content
    assert "do not re-arm" in content
    assert "/loop stop loop-watch-ci-run" in content


def test_dynamic_tick_message_is_self_contained():
    message = dynamic_tick_message("loop-x", "watch it")
    assert message.startswith("Dynamic loop loop-x. Task: watch it")
    assert "this whole message, copied verbatim" in message


@pytest.mark.asyncio
async def test_loop_without_cron_service(tmp_path):
    router = CommandRouter()
    register_builtin_commands(router)
    reply, _ = await _send(router, SimpleNamespace(cron_service=None), "/loop 5m x")
    assert "scheduler" in reply.content


@pytest.mark.asyncio
async def test_dynamic_rearm_from_cron_tick_replaces_pending(tmp_path):
    cron = CronService(tmp_path / "cron" / "jobs.json")
    tool = CronTool(cron, default_timezone="UTC")
    at = (datetime.now() + timedelta(minutes=5)).replace(microsecond=0).isoformat()
    message = dynamic_tick_message("loop-watch", "watch")
    token = tool.set_cron_context(True)
    try:
        with request_context(
            RequestContext(channel="websocket", chat_id="c1", session_key="websocket:c1")
        ):
            first = await tool.execute(action="add", name="loop-watch", message=message, at=at)
            second = await tool.execute(action="add", name="loop-watch", message=message, at=at)
            refused = await tool.execute(action="add", name="other", message="x", at=at)
            refused_every = await tool.execute(
                action="add", name="loop-watch", message=message, every_seconds=60
            )
    finally:
        tool.reset_cron_context(token)
    assert "Created job" in first and "Created job" in second
    assert "cannot schedule" in refused
    assert "cannot schedule" in refused_every
    jobs = cron.list_jobs()
    assert [j.name for j in jobs] == ["loop-watch"]
    assert jobs[0].delete_after_run
