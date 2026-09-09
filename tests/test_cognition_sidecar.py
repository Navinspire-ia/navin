# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""The cognition sidecar is invisible until a project opts in.

Two guarantees are pinned here. Off (the default): no tool is registered, the
turn hook factory answers None, nothing is written under .navin, and the
check costs one stat. On: a finished turn leaves one compact line in
episodes.jsonl and `recall` finds it again, while the heartbeat and
ephemeral turns stay out. A last test keeps the sidecar out of the hot path
files by construction.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from navin.agent.hook import AgentRunHookContext, AgentTurnHookContext
from navin.agent.hooks import DEFAULT_HOOK_FACTORIES, create_episode_journal_hook
from navin.agent.tools.context import RequestContext, ToolContext, request_context
from navin.agent.tools.loader import ToolLoader
from navin.agent.tools.recall import RecallTool
from navin.agent.tools.registry import ToolRegistry
from navin.cognition import (
    SETTINGS_NAME,
    cognition_enabled,
    cognition_state,
    read_settings,
    settings_path,
    update_settings,
    write_settings,
)
from navin.cognition.episodes import (
    build_episode,
    episodes_path,
    flush_episodes,
    search_episodes,
)
from navin.cognition.hook import EpisodeJournalHook
from navin.cognition.settings import clear_settings_cache
from navin.config.schema import ToolsConfig

REPO = Path(__file__).resolve().parents[1]


def _loaded_tools(workspace: Path) -> set[str]:
    ctx = ToolContext(config=ToolsConfig(), workspace=str(workspace))
    return set(ToolLoader().load(ctx, ToolRegistry()))


def _turn(workspace: Path | None, **overrides) -> AgentTurnHookContext:
    base = dict(
        workspace=workspace,
        channel="webui",
        chat_id="chat-1",
        session_key="webui:chat-1",
        metadata={"product_module": "tenders"},
        ephemeral=False,
    )
    base.update(overrides)
    return AgentTurnHookContext(**base)


def _run(user: str, reply: str, tools: list[str] | None = None) -> AgentRunHookContext:
    return AgentRunHookContext(
        messages=[
            {"role": "system", "content": "you are navin"},
            {"role": "user", "content": user},
            {"role": "assistant", "content": reply},
        ],
        final_content=reply,
        tools_used=list(tools or []),
        stop_reason="end_turn",
    )


class _Workspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(clear_settings_cache)
        self.workspace = Path(self._tmp.name)
        # The machine-level project index must never touch the real ~/.navin.
        import navin.cognition.registration as registration

        self.index_file = Path(self._tmp.name) / "machine" / "cognition-projects.json"
        original = registration.index_path
        registration.index_path = lambda: self.index_file
        self.addCleanup(setattr, registration, "index_path", original)

    def _enable(self, **kwargs) -> None:
        write_settings(self.workspace, enabled=True, **kwargs)


class FlagOffTest(_Workspace):
    """The default: nothing registered, nothing written, nothing built."""

    def test_no_flag_means_off(self) -> None:
        self.assertFalse(cognition_enabled(self.workspace))
        self.assertFalse(cognition_enabled(self.workspace, "episodes"))
        self.assertFalse(cognition_enabled(self.workspace, "recall"))
        self.assertFalse(cognition_enabled(None))

    def test_recall_is_not_registered(self) -> None:
        self.assertNotIn("recall", _loaded_tools(self.workspace))

    def test_factory_returns_none_and_touches_nothing(self) -> None:
        self.assertIsNone(create_episode_journal_hook(_turn(self.workspace)))
        self.assertIsNone(create_episode_journal_hook(_turn(None)))
        self.assertFalse((self.workspace / ".navin").exists())
        self.assertFalse(episodes_path(self.workspace).exists())

    def test_default_factories_include_the_sidecar_once(self) -> None:
        names = [f.__name__ for f in DEFAULT_HOOK_FACTORIES]
        self.assertEqual(names.count("create_episode_journal_hook"), 1)
        self.assertEqual(names[0], "create_file_edit_activity_hook")

    def test_malformed_or_partial_flag_stays_off(self) -> None:
        path = settings_path(self.workspace)
        path.parent.mkdir(parents=True)
        for raw in ("not json", "[]", '{"enabled": "yes"}', '{"episodes": true}', ""):
            path.write_text(raw, encoding="utf-8")
            clear_settings_cache()
            self.assertFalse(cognition_enabled(self.workspace), raw)
            self.assertNotIn("recall", _loaded_tools(self.workspace))

    def test_off_path_is_one_stat_cheap(self) -> None:
        turn = _turn(self.workspace)
        loops = 20_000
        start = time.perf_counter()
        for _ in range(loops):
            create_episode_journal_hook(turn)
        elapsed = time.perf_counter() - start
        # Real cost is a few microseconds; the bound only guards against a
        # regression that would parse, allocate or log on every turn.
        self.assertLess(elapsed / loops, 50e-6, f"{elapsed / loops * 1e6:.1f} us per call")


class FlagOnTest(_Workspace):
    def test_one_line_form_enables_both_features(self) -> None:
        settings_path(self.workspace).parent.mkdir(parents=True)
        settings_path(self.workspace).write_text('{"enabled": true}', encoding="utf-8")
        settings = read_settings(self.workspace)
        self.assertTrue(settings.enabled)
        self.assertTrue(settings.episodes)
        self.assertTrue(settings.recall)

    def test_features_can_be_split(self) -> None:
        self._enable(episodes=True, recall=False)
        self.assertTrue(cognition_enabled(self.workspace, "episodes"))
        self.assertFalse(cognition_enabled(self.workspace, "recall"))
        self.assertNotIn("recall", _loaded_tools(self.workspace))
        self.assertIsInstance(create_episode_journal_hook(_turn(self.workspace)), EpisodeJournalHook)

    def test_cache_follows_the_file(self) -> None:
        self._enable()
        self.assertTrue(cognition_enabled(self.workspace))
        path = settings_path(self.workspace)
        path.write_text('{"enabled": false, "padding": "make the size differ"}', encoding="utf-8")
        future = time.time() + 5
        os.utime(path, (future, future))
        self.assertFalse(cognition_enabled(self.workspace))
        write_settings(self.workspace, enabled=True)
        self.assertTrue(cognition_enabled(self.workspace))

    def test_recall_is_registered(self) -> None:
        self._enable()
        self.assertIn("recall", _loaded_tools(self.workspace))

    def test_turn_is_journaled_and_recalled(self) -> None:
        self._enable()
        hook = create_episode_journal_hook(_turn(self.workspace))
        self.assertIsInstance(hook, EpisodeJournalHook)
        asyncio.run(
            hook.after_run(
                _run(
                    "Reprends l'offre pour la mairie de Lyon, budget 250k",
                    "<think>plan</think>Offre Lyon relue: budget 250k, deadline 12 mai.",
                    ["tenders", "read_file", "tenders"],
                )
            )
        )
        self.assertTrue(flush_episodes(5.0))
        lines = episodes_path(self.workspace).read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["channel"], "webui")
        self.assertEqual(record["module"], "tenders")
        self.assertEqual(record["tools"], ["tenders", "read_file"])
        self.assertNotIn("<think>", record["reply"])
        self.assertIn("mairie de Lyon", record["user"])

        tool = RecallTool(workspace=self.workspace)
        ctx = RequestContext(channel="webui", chat_id="chat-1", workspace=self.workspace)
        with request_context(ctx):
            text = asyncio.run(tool.execute(query="offre Lyon budget"))
        self.assertIn("Past turns (1)", text)
        self.assertIn("mairie de Lyon", text)
        self.assertIn("Tools: tenders, read_file", text)
        with request_context(ctx):
            miss = asyncio.run(tool.execute(query="zzqx"))
        self.assertIn("No past turn", miss)

    def test_recall_includes_memory_notes(self) -> None:
        self._enable()
        memory = self.workspace / ".navin" / "memory"
        memory.mkdir(parents=True, exist_ok=True)
        (memory / "MEMORY.md").write_text(
            "# Memory\n\n- Le client Acme veut des factures en euros.\n\n- Autre note.\n",
            encoding="utf-8",
        )
        tool = RecallTool(workspace=self.workspace)
        text = asyncio.run(tool.execute(query="factures Acme"))
        self.assertIn("MEMORY.md:", text)
        self.assertIn("Acme", text)

    def test_heartbeat_and_ephemeral_stay_out(self) -> None:
        self._enable()
        self.assertIsNone(create_episode_journal_hook(_turn(self.workspace, session_key="heartbeat")))
        self.assertIsNone(
            create_episode_journal_hook(_turn(self.workspace, metadata={"heartbeat": True}))
        )
        self.assertIsNone(create_episode_journal_hook(_turn(self.workspace, ephemeral=True)))
        tool = RecallTool(workspace=self.workspace)
        ctx = RequestContext(channel="gateway", chat_id="hb", session_key="heartbeat")
        with request_context(ctx):
            result = asyncio.run(tool.execute(query="anything"))
        self.assertTrue(getattr(result, "is_error", False))

    def test_recall_reports_when_the_turn_project_is_off(self) -> None:
        self._enable()
        other = Path(self._tmp.name) / "other-project"
        other.mkdir()
        tool = RecallTool(workspace=self.workspace)
        ctx = RequestContext(channel="webui", chat_id="c", workspace=other)
        with request_context(ctx):
            text = asyncio.run(tool.execute(query="anything at all"))
        self.assertIn("off for this project", text)
        self.assertFalse((other / ".navin").exists())

    def test_hook_never_raises_on_odd_runs(self) -> None:
        self._enable()
        hook = create_episode_journal_hook(_turn(self.workspace))
        empty = AgentRunHookContext(messages=[], final_content=None, stop_reason="end_turn")
        asyncio.run(hook.after_run(empty))
        weird = AgentRunHookContext(
            messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}],
            final_content="",
        )
        asyncio.run(hook.after_run(weird))
        self.assertTrue(flush_episodes(5.0))
        self.assertFalse(episodes_path(self.workspace).exists())


class GuardrailsSettingsTest(_Workspace):
    """What the Guardrails panel writes and reads, one switch at a time."""

    def test_state_is_off_and_empty_by_default(self) -> None:
        state = cognition_state(self.workspace)
        self.assertEqual(
            {k: state[k] for k in ("enabled", "episodes", "recall", "journal_bytes")},
            {"enabled": False, "episodes": False, "recall": False, "journal_bytes": 0},
        )
        self.assertEqual(state["settings_file"], ".navin/cognition.json")
        # Nothing wanted, nothing to restart for; the registry is unknown here.
        self.assertIsNone(state["recall_registered"])
        self.assertFalse(state["recall_requires_restart"])
        self.assertFalse((self.workspace / ".navin").exists())

    def test_master_switch_turns_both_features_on(self) -> None:
        settings = update_settings(self.workspace, {"enabled": True})
        self.assertTrue(settings.enabled and settings.episodes and settings.recall)
        raw = json.loads(settings_path(self.workspace).read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], 1)
        self.assertTrue(raw["enabled"])

    def test_sub_switch_writes_do_not_disable_the_other(self) -> None:
        update_settings(self.workspace, {"enabled": True})
        settings = update_settings(self.workspace, {"recall": False})
        self.assertTrue(settings.episodes)
        self.assertFalse(settings.recall)
        settings = update_settings(self.workspace, {"episodes": False})
        self.assertFalse(settings.episodes)
        self.assertFalse(settings.recall)
        self.assertTrue(settings.enabled)

    def test_turning_the_master_off_keeps_the_choices_for_later(self) -> None:
        update_settings(self.workspace, {"enabled": True})
        update_settings(self.workspace, {"recall": False})
        update_settings(self.workspace, {"enabled": False})
        self.assertFalse(cognition_enabled(self.workspace, "episodes"))
        self.assertNotIn("recall", _loaded_tools(self.workspace))
        settings = update_settings(self.workspace, {"enabled": True})
        self.assertTrue(settings.episodes)
        self.assertFalse(settings.recall)

    def test_bad_fields_are_rejected_without_writing(self) -> None:
        for fields in ({}, {"enabled": "yes"}, {"bogus": True}, "x"):
            with self.assertRaises(ValueError):
                update_settings(self.workspace, fields)  # type: ignore[arg-type]
        self.assertFalse(settings_path(self.workspace).exists())

    def test_state_reports_the_journal_size(self) -> None:
        update_settings(self.workspace, {"enabled": True})
        path = episodes_path(self.workspace)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"user":"x"}\n' * 10, encoding="utf-8")
        self.assertEqual(cognition_state(self.workspace)["journal_bytes"], path.stat().st_size)


class RecallRegistrationTest(_Workspace):
    """A gateway serves many projects; the tool follows the ones that opted in."""

    def test_index_follows_the_project_switches(self) -> None:
        from navin.cognition.registration import read_index

        project = self.workspace / "project"
        project.mkdir()
        self.assertEqual(read_index(), [])
        update_settings(project, {"enabled": True})
        self.assertEqual([str(p) for p in read_index()], [str(project.resolve())])
        update_settings(project, {"recall": False})
        self.assertEqual(read_index(), [])
        update_settings(project, {"recall": True})
        update_settings(project, {"enabled": False})
        self.assertEqual(read_index(), [])

    def test_a_booting_gateway_registers_recall_for_indexed_projects(self) -> None:
        from navin.cognition import sync_recall_tool

        gateway_ws = self.workspace / "gateway-home"
        project = self.workspace / "project"
        gateway_ws.mkdir()
        project.mkdir()
        update_settings(project, {"enabled": True})
        # No `extra`: the boot path only knows its own workspace and the index.
        registry = ToolRegistry()
        self.assertTrue(sync_recall_tool(registry, workspace=gateway_ws))
        # A stale index entry (flag file gone) is re-checked, not trusted.
        settings_path(project).unlink()
        clear_settings_cache()
        self.assertFalse(sync_recall_tool(ToolRegistry(), workspace=gateway_ws))

    def test_sync_adds_then_drops_the_tool(self) -> None:
        from navin.cognition import recall_registered, sync_recall_tool

        registry = ToolRegistry()
        gateway_ws = self.workspace / "gateway-home"
        project = self.workspace / "project"
        gateway_ws.mkdir()
        project.mkdir()

        self.assertFalse(sync_recall_tool(registry, workspace=gateway_ws, extra=[project]))
        self.assertFalse(recall_registered(registry))

        update_settings(project, {"enabled": True})
        self.assertTrue(sync_recall_tool(registry, workspace=gateway_ws, extra=[project]))
        self.assertTrue(recall_registered(registry))
        # Idempotent: a second sync keeps the same instance.
        tool = registry.get("recall")
        sync_recall_tool(registry, workspace=gateway_ws, extra=[project])
        self.assertIs(registry.get("recall"), tool)

        update_settings(project, {"recall": False})
        self.assertFalse(sync_recall_tool(registry, workspace=gateway_ws, extra=[project]))
        self.assertIsNone(registry.get("recall"))

    def test_sync_never_raises(self) -> None:
        from navin.cognition import sync_recall_tool

        class _Broken:
            def get(self, name):
                raise RuntimeError("boom")

        self.assertFalse(sync_recall_tool(_Broken(), workspace=self.workspace))


class CognitionRouteTest(_Workspace):
    """The Guardrails panel talks HTTP: the route itself must answer."""

    def _handler(self, registry=None):
        from navin.webui.ws_http import GatewayHTTPHandler

        project = str(self.workspace)

        class _Scope:
            project_path = project

        class _Workspaces:
            def scope_for_session_key(self, key: str):
                return _Scope()

        handler = object.__new__(GatewayHTTPHandler)
        handler.check_api_token = lambda request: True
        handler.bus = None
        handler.workspaces = _Workspaces()
        handler.skills_workspace_path = self.workspace / "gateway-home"
        if registry is not None:
            handler.tool_registry = lambda: registry
        return handler

    def _get(self, handler, path: str):
        class _Request:
            def __init__(self, full_path: str) -> None:
                self.path = full_path
                self.headers = {}

        got = path.split("?", 1)[0]
        return asyncio.run(handler._dispatch_session_routes(_Request(path), got))

    def test_read_then_toggle_over_http(self) -> None:
        handler = self._handler()
        response = self._get(handler, "/api/sessions/websocket%3Aabc/cognition")
        self.assertIsNotNone(response, "route not registered")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.body)["enabled"])

        response = self._get(
            handler,
            '/api/sessions/websocket%3Aabc/cognition?fields={"enabled":true}',
        )
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.body)
        self.assertTrue(body["enabled"] and body["episodes"] and body["recall"])
        self.assertTrue(cognition_enabled(self.workspace, "recall"))

        response = self._get(
            handler,
            '/api/sessions/websocket%3Aabc/cognition?fields={"recall":false}',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(json.loads(response.body)["recall"])
        self.assertTrue(json.loads(response.body)["episodes"])

    def test_toggle_registers_the_tool_live_when_the_registry_is_shared(self) -> None:
        registry = ToolRegistry()
        handler = self._handler(registry)
        response = self._get(handler, "/api/sessions/websocket%3Aabc/cognition")
        body = json.loads(response.body)
        self.assertFalse(body["recall_registered"])
        self.assertFalse(body["recall_requires_restart"])

        response = self._get(
            handler, '/api/sessions/websocket%3Aabc/cognition?fields={"enabled":true}'
        )
        body = json.loads(response.body)
        self.assertTrue(body["recall_registered"])
        self.assertFalse(body["recall_requires_restart"])
        self.assertIsNotNone(registry.get("recall"))

        response = self._get(
            handler, '/api/sessions/websocket%3Aabc/cognition?fields={"enabled":false}'
        )
        body = json.loads(response.body)
        self.assertFalse(body["recall_registered"])
        self.assertIsNone(registry.get("recall"))

    def test_without_a_registry_the_state_is_honest_about_the_restart(self) -> None:
        handler = self._handler()
        response = self._get(
            handler, '/api/sessions/websocket%3Aabc/cognition?fields={"enabled":true}'
        )
        body = json.loads(response.body)
        self.assertIsNone(body["recall_registered"])
        self.assertTrue(body["recall_requires_restart"])

    def test_bad_fields_answer_400(self) -> None:
        handler = self._handler()
        for query in ("fields=nope", 'fields=["x"]', 'fields={"bogus":true}', 'fields={"enabled":"yes"}'):
            response = self._get(handler, f"/api/sessions/websocket%3Aabc/cognition?{query}")
            self.assertEqual(response.status_code, 400, query)
        self.assertFalse(settings_path(self.workspace).exists())

    def test_non_webui_session_is_404(self) -> None:
        response = self._get(self._handler(), "/api/sessions/telegram%3A42/cognition")
        self.assertEqual(response.status_code, 404)


class RecordAndSearchTest(unittest.TestCase):
    def test_record_is_bounded(self) -> None:
        record = build_episode(
            channel="cli",
            chat_id="direct",
            session_key="cli:direct",
            user_text="x" * 5000,
            reply="<think>" + "y" * 9000 + "</think>" + "z" * 5000,
            tools_used=[f"t{i}" for i in range(100)],
            stop_reason="end_turn",
            error="e" * 1000,
            now=0.0,
        )
        assert record is not None
        self.assertLessEqual(len(record["user"]), 600)
        self.assertLessEqual(len(record["reply"]), 900)
        self.assertNotIn("y", record["reply"])
        self.assertEqual(len(record["tools"]), 24)
        self.assertLessEqual(len(record["error"]), 200)
        self.assertEqual(record["ts"], "1970-01-01T00:00:00Z")
        self.assertLess(len(json.dumps(record)), 2500)

    def test_user_text_drops_the_runtime_context_block(self) -> None:
        from navin.cognition.episodes import last_user_text
        from navin.runtime_context import RUNTIME_CONTEXT_TAG

        messages = [
            {"role": "user", "content": "older question"},
            {"role": "assistant", "content": "older answer"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"deploy the api\n\n{RUNTIME_CONTEXT_TAG}\nProject map (4049 files)..."},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            },
            {"role": "assistant", "content": "done"},
        ]
        self.assertEqual(last_user_text(messages).strip(), "deploy the api")

    def test_hook_prefers_the_original_request_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            write_settings(ws, enabled=True)
            hook = create_episode_journal_hook(_turn(ws))
            ctx = RequestContext(
                channel="webui", chat_id="c", workspace=ws,
                original_user_text="the words the user typed",
            )
            with request_context(ctx):
                asyncio.run(hook.after_run(_run("noisy injected copy", "ok")))
            self.assertTrue(flush_episodes(5.0))
            record = json.loads(episodes_path(ws).read_text(encoding="utf-8").splitlines()[-1])
            self.assertEqual(record["user"], "the words the user typed")
            clear_settings_cache()

    def test_empty_turn_yields_nothing(self) -> None:
        self.assertIsNone(
            build_episode(
                channel="cli", chat_id="d", session_key=None,
                user_text="  ", reply="<think>only</think>", tools_used=[], stop_reason=None,
            )
        )

    def test_search_prefers_overlap_then_recency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            path = episodes_path(ws)
            path.parent.mkdir(parents=True)
            rows = [
                {"user": "deploy the api to staging", "reply": "done", "tools": ["exec"]},
                {"user": "rotate the api key", "reply": "rotated", "tools": []},
                {"user": "deploy the docs", "reply": "done", "tools": ["exec"]},
            ]
            path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            hits = search_episodes(ws, "deploy api", limit=5)
            self.assertEqual([r["user"] for _s, r in hits][0], "deploy the api to staging")
            # Same single-token overlap: the newer "deploy the docs" outranks the older key rotation.
            self.assertEqual(
                [r["user"] for _s, r in hits][1:],
                ["deploy the docs", "rotate the api key"],
            )
            self.assertEqual(search_episodes(ws, "!!", limit=5), [])

    def test_search_reads_only_the_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            path = episodes_path(ws)
            path.parent.mkdir(parents=True)
            old = json.dumps({"user": "needle in the old part", "reply": ""})
            new = json.dumps({"user": "recent filler line", "reply": ""})
            path.write_text(old + "\n" + (new + "\n") * 200, encoding="utf-8")
            hits = search_episodes(ws, "needle", limit=3, max_bytes=2048)
            self.assertEqual(hits, [])
            hits = search_episodes(ws, "needle", limit=3)
            self.assertEqual(len(hits), 1)


class HotPathIsolationTest(unittest.TestCase):
    """The sidecar never leaks into the loop, runner, context or memory code."""

    HOT_PATH = (
        "navin/agent/loop.py",
        "navin/agent/runner.py",
        "navin/agent/context.py",
        "navin/agent/memory.py",
        "navin/agent/turn_hooks.py",
        "navin/agent/hook.py",
        "navin/agent/tools/registry.py",
        "navin/agent/tools/loader.py",
        "navin/command/modules.py",
    )

    def test_hot_path_files_do_not_mention_cognition(self) -> None:
        for rel in self.HOT_PATH:
            source = (REPO / rel).read_text(encoding="utf-8")
            self.assertNotIn("cognition", source, rel)
            self.assertNotIn("episode", source.lower(), rel)

    def test_settings_file_name_is_stable(self) -> None:
        self.assertEqual(SETTINGS_NAME, "cognition.json")


if __name__ == "__main__":
    unittest.main()
