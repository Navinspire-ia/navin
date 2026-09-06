"""Failures the agent has to be able to act on, and the one boundary it must not cross.

An error that names only its exception class, or a "not found" that lists no
alternative, ends the agent's turn: it has nothing to correct, so it retries the
identical call. These cover the paths where that used to happen, plus the raw CDP
escape hatch, which was the one way to reach the disk without a path check.
"""

import asyncio
import unittest

from navin.agent.tools.browser import BrowserTool, shutdown_browser_sessions
from navin.agent.tools.mcp import _describe_failure
from navin.agent.tools.self import MyTool
from navin.agent.tools.web import _SUPPORTED_SEARCH_PROVIDERS


def _run(coro) -> str:
    result = asyncio.run(coro)
    return str(getattr(result, "content", result))


class _RpcError:
    def __init__(self, message: str, code: int) -> None:
        self.message = message
        self.code = code


class _McpError(Exception):
    """Stands in for mcp.shared.exceptions.McpError, which carries .error."""

    def __init__(self, message: str, code: int) -> None:
        self.error = _RpcError(message, code)
        super().__init__(message)


class McpFailureTest(unittest.TestCase):
    def test_the_server_message_reaches_the_agent(self) -> None:
        exc = _McpError("Invalid params: missing required property 'path'", -32602)
        described = _describe_failure(exc)
        self.assertIn("missing required property 'path'", described)
        self.assertIn("-32602", described)

    def test_a_plain_exception_keeps_its_detail(self) -> None:
        described = _describe_failure(ValueError("port must be an integer"))
        self.assertIn("port must be an integer", described)
        self.assertIn("ValueError", described)

    def test_an_empty_message_falls_back_to_the_class(self) -> None:
        self.assertEqual(_describe_failure(TimeoutError()), "TimeoutError")

    def test_a_long_message_is_bounded(self) -> None:
        described = _describe_failure(RuntimeError("x" * 5000))
        self.assertLess(len(described), 1000)


class CdpBoundaryTest(unittest.TestCase):
    """Raw CDP forwards agent values to the browser, bypassing every path check."""

    def _restricted(self) -> BrowserTool:
        return BrowserTool(restrict_to_workspace=True)

    def _unrestricted(self) -> BrowserTool:
        return BrowserTool(restrict_to_workspace=False)

    def test_a_download_path_is_refused_under_the_boundary(self) -> None:
        refusal = self._restricted()._cdp_refusal(
            "Browser.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": "/etc"},
        )
        self.assertIsNotNone(refusal)
        assert refusal is not None
        self.assertIn("write_file", refusal)

    def test_reading_local_files_into_an_input_is_refused(self) -> None:
        refusal = self._restricted()._cdp_refusal(
            "DOM.setFileInputFiles",
            {"files": ["/etc/passwd"], "nodeId": 1},
        )
        self.assertIsNotNone(refusal)

    def test_the_method_name_is_matched_case_insensitively(self) -> None:
        self.assertIsNotNone(
            self._restricted()._cdp_refusal("browser.setdownloadbehavior", {})
        )

    def test_a_file_url_is_refused_however_deeply_it_is_nested(self) -> None:
        refusal = self._restricted()._cdp_refusal(
            "Page.navigate",
            {"outer": {"inner": [{"url": "file:///etc/passwd"}]}},
        )
        self.assertIsNotNone(refusal)
        assert refusal is not None
        self.assertIn("read_file", refusal)

    def test_a_file_url_is_refused_whatever_its_casing_or_padding(self) -> None:
        self.assertIsNotNone(
            self._restricted()._cdp_refusal("Runtime.evaluate", {"e": "  FILE://x  "})
        )

    def test_ordinary_cdp_commands_still_pass(self) -> None:
        tool = self._restricted()
        self.assertIsNone(tool._cdp_refusal("Network.enable", {}))
        self.assertIsNone(tool._cdp_refusal("Runtime.evaluate", {"expression": "1+1"}))
        self.assertIsNone(
            tool._cdp_refusal("Page.navigate", {"url": "http://localhost:3000/a"})
        )

    def test_nothing_is_refused_when_access_is_unrestricted(self) -> None:
        tool = self._unrestricted()
        self.assertIsNone(
            tool._cdp_refusal("Browser.setDownloadBehavior", {"downloadPath": "/etc"})
        )
        self.assertIsNone(tool._cdp_refusal("Page.navigate", {"url": "file:///etc"}))

    def test_the_refusal_travels_through_execute(self) -> None:
        """Guard against the check being added but never wired into the action."""

        async def attempt() -> str:
            tool = self._restricted()
            try:
                result = await tool.execute(
                    action="cdp",
                    method="DOM.setFileInputFiles",
                    params={"files": ["/etc/passwd"]},
                )
            finally:
                await shutdown_browser_sessions()
            return str(getattr(result, "content", result))

        out = asyncio.run(attempt())
        if "playwright" in out.lower():
            self.skipTest("no browser available on this runner")
        self.assertIn("Error", out)
        self.assertIn("restricted", out)


class InspectKeyTest(unittest.TestCase):
    """`my` walks attribute paths; a miss used to name no alternative."""

    class _Stub:
        def __init__(self) -> None:
            self._runtime_vars: dict[str, object] = {}
            self.settings = {"alpha": 1, "beta": 2}

    def test_a_missing_dict_key_lists_the_ones_that_exist(self) -> None:
        tool = MyTool(runtime_state=self._Stub())
        out = _run(tool.execute(action="inspect", key="settings.gamma"))
        self.assertIn("alpha", out)
        self.assertIn("beta", out)

    def test_an_empty_dict_says_so_rather_than_listing_nothing(self) -> None:
        stub = self._Stub()
        stub.settings = {}
        out = _run(MyTool(runtime_state=stub).execute(action="inspect", key="settings.x"))
        self.assertIn("empty", out)


class BoardUpdateTest(unittest.TestCase):
    def test_a_rejected_update_names_the_offending_field(self) -> None:
        import tempfile
        from pathlib import Path

        from navin.board.store import BoardError, ProjectBoardStore

        with tempfile.TemporaryDirectory() as tmp:
            store = ProjectBoardStore(Path(tmp))
            task = store.create_task(title="first", actor="a", actor_type="human")
            with self.assertRaises(BoardError) as caught:
                store.update_task(
                    task_id=task["id"],
                    actor="a",
                    actor_type="human",
                    fields={"title": "   "},
                )
            self.assertIn("title", caught.exception.message)


class SqlitePathPolicyTest(unittest.TestCase):
    """A config path is the operator's choice; an ad-hoc path is the model's."""

    def setUp(self) -> None:
        import sqlite3
        import tempfile
        from pathlib import Path

        self._proj = tempfile.TemporaryDirectory()
        self._out = tempfile.TemporaryDirectory()
        self.addCleanup(self._proj.cleanup)
        self.addCleanup(self._out.cleanup)
        self.project = Path(self._proj.name).resolve()
        self.outside = Path(self._out.name).resolve() / "app.db"
        for target in (self.outside, self.project / "local.db"):
            con = sqlite3.connect(target)
            con.execute("create table t(x)")
            con.execute("insert into t values(42)")
            con.commit()
            con.close()

    def _tool(self, path: str):
        from navin.agent.tools.database import (
            DatabaseConnectionConfig,
            DatabaseTool,
            DatabaseToolConfig,
        )

        config = DatabaseToolConfig(
            connections={"prod": DatabaseConnectionConfig(engine="sqlite", path=path)}
        )
        return DatabaseTool(config=config, workspace=str(self.project))

    def test_a_configured_connection_may_live_outside_the_project(self) -> None:
        out = _run(
            self._tool(str(self.outside)).execute(
                query="select x from t", connection="prod"
            )
        )
        self.assertIn("42", out)

    def test_a_relative_configured_path_still_anchors_to_the_project(self) -> None:
        out = _run(self._tool("local.db").execute(query="select x from t", connection="prod"))
        self.assertIn("42", out)

    def test_an_ad_hoc_path_outside_the_project_is_still_refused(self) -> None:
        out = _run(
            self._tool("local.db").execute(
                query="select x from t", sqlite_path=str(self.outside)
            )
        )
        self.assertIn("Error", out)
        self.assertIn("sqlite_path", out)
        self.assertIn("named connection", out)

    def test_a_missing_configured_database_names_the_connection(self) -> None:
        out = _run(
            self._tool(str(self.outside.parent / "absent.db")).execute(
                query="select 1", connection="prod"
            )
        )
        self.assertIn("prod", out)


class SearchProviderTest(unittest.TestCase):
    def test_every_dispatched_provider_is_listed(self) -> None:
        """The message must not drift from the branches it describes."""
        import re
        from pathlib import Path as _Path

        import navin.agent.tools.web as web_mod

        source = _Path(web_mod.__file__).read_text(encoding="utf-8")
        dispatched = set(re.findall(r'provider == "([a-z]+)"', source))
        self.assertTrue(dispatched)
        self.assertEqual(dispatched - set(_SUPPORTED_SEARCH_PROVIDERS), set())


if __name__ == "__main__":
    unittest.main()
