"""Phase 5 DAP workbench: client framing, session ops, HTTP dispatch."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from navin.dap.manager import DebugManager
from navin.dap.session import (
    DebugSession,
    DebugSessionError,
    detect_runtime,
    resolve_adapter_argv,
    runtime_for_path,
)
from navin.security.workspace_access import default_workspace_scope
from navin.webui.debug_api import debug_dispatch

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_dap_adapter.py"


class DapSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self._tmp().name).resolve()
        (self.root / "app.py").write_text(
            "def main():\n    x = 1\n    print(x)\n\nif __name__ == '__main__':\n    main()\n",
            encoding="utf-8",
        )
        self.session = DebugSession(self.root, fake_adapter=str(_FIXTURE))

    def _tmp(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp

    def test_set_breakpoints_are_stored(self) -> None:
        result = self.session.set_breakpoints("app.py", [2, 3, 3])
        self.assertEqual(result["lines"], [2, 3])
        snap = self.session.snapshot()
        self.assertEqual(snap["breakpoints"][0]["path"], "app.py")

    def test_start_stop_and_stack(self) -> None:
        self.session.set_breakpoints("app.py", [3])
        started = self.session.start(program="app.py")
        self.assertIn(started["state"], {"starting", "running", "stopped"})
        # Fake adapter emits stopped shortly after configurationDone.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.session.snapshot()["state"] != "stopped":
            time.sleep(0.02)
        self.assertEqual(self.session.snapshot()["state"], "stopped")
        stack = self.session.stack()
        self.assertTrue(stack["frames"])
        self.assertEqual(stack["frames"][0]["name"], "main")
        scopes = self.session.scopes(int(stack["frames"][0]["id"]))
        self.assertEqual(scopes["scopes"][0]["name"], "Locals")
        variables = self.session.variables(
            int(scopes["scopes"][0]["variablesReference"])
        )
        self.assertEqual(variables["variables"][0]["name"], "x")
        evaluated = self.session.evaluate("1+1")
        self.assertIn("eval(", evaluated["result"])
        self.session.step("over")
        stopped = self.session.stop()
        self.assertEqual(stopped["state"], "idle")

    def test_second_start_while_running_is_conflict(self) -> None:
        self.session.start(program="app.py")
        with self.assertRaises(DebugSessionError) as ctx:
            self.session.start(program="app.py")
        self.assertEqual(ctx.exception.status, 409)
        self.session.stop()

    def test_node_runtime_start_with_fake_adapter(self) -> None:
        (self.root / "server.js").write_text(
            "console.log('hi');\n",
            encoding="utf-8",
        )
        started = self.session.start(program="server.js", runtime="node")
        self.assertIn(started["state"], {"starting", "running", "stopped"})
        self.assertEqual(started["program"], "server.js")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and self.session.snapshot()["state"] != "stopped":
            time.sleep(0.02)
        self.assertEqual(self.session.snapshot()["state"], "stopped")
        self.session.stop()

    def test_node_extension_auto_detects_runtime(self) -> None:
        (self.root / "main.mjs").write_text("export {}\n", encoding="utf-8")
        started = self.session.start(program="main.mjs")
        self.assertEqual(started["program"], "main.mjs")
        self.session.stop()


class RuntimeDetectionTests(unittest.TestCase):
    def test_detect_runtime_from_extension(self) -> None:
        self.assertEqual(detect_runtime("app.py"), "python")
        self.assertEqual(detect_runtime("index.js"), "node")
        self.assertEqual(detect_runtime("index.ts"), "node")
        self.assertEqual(detect_runtime("lib.mjs"), "node")
        self.assertEqual(detect_runtime("main.go"), "go")
        self.assertEqual(detect_runtime("main.rs"), "lldb")
        self.assertEqual(detect_runtime("lib.cpp"), "lldb")
        self.assertEqual(detect_runtime(None, runtime="node"), "node")
        self.assertEqual(detect_runtime(None, runtime="go"), "go")
        self.assertEqual(detect_runtime(None, runtime="lldb"), "lldb")
        self.assertEqual(detect_runtime(None, pytest_node="t.py::x"), "python")

    def test_runtime_for_path_returns_none_for_unsupported(self) -> None:
        self.assertEqual(runtime_for_path("app.py"), "python")
        self.assertEqual(runtime_for_path("main.go"), "go")
        self.assertIsNone(runtime_for_path("README.md"))
        self.assertIsNone(runtime_for_path("style.css"))

    def test_resolve_go_adapter_uses_dlv(self) -> None:
        with patch("navin.dap.session.shutil.which", return_value="/usr/bin/dlv"):
            argv = resolve_adapter_argv(runtime="go")
        self.assertEqual(argv, ["/usr/bin/dlv", "dap"])

    def test_resolve_go_adapter_errors_when_missing(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "NAVIN_DELVE"}
        with patch.dict(os.environ, env, clear=True):
            with patch("navin.dap.session.shutil.which", return_value=None):
                with self.assertRaises(DebugSessionError) as ctx:
                    resolve_adapter_argv(runtime="go")
        self.assertEqual(ctx.exception.status, 503)
        self.assertIn("delve", ctx.exception.message.lower())

    def test_resolve_lldb_adapter_discovers_candidate(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "lldb-dap"
            adapter.write_text("#!/bin/sh\n", encoding="utf-8")
            adapter.chmod(0o755)
            with patch(
                "navin.dap.session._candidate_lldb_dap",
                return_value=[adapter],
            ):
                argv = resolve_adapter_argv(runtime="lldb")
        self.assertEqual(argv, [str(adapter)])

    def test_resolve_lldb_adapter_errors_when_missing(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "NAVIN_LLDB_DAP"}
        with patch.dict(os.environ, env, clear=True):
            with patch("navin.dap.session._candidate_lldb_dap", return_value=[]):
                with self.assertRaises(DebugSessionError) as ctx:
                    resolve_adapter_argv(runtime="lldb")
        self.assertEqual(ctx.exception.status, 503)
        self.assertIn("LLDB", ctx.exception.message)

    def test_resolve_node_adapter_errors_when_missing(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "NAVIN_JS_DEBUG_ADAPTER"}
        with patch.dict(os.environ, env, clear=True):
            with patch("navin.dap.session.shutil.which", return_value="/usr/bin/node"):
                with patch(
                    "navin.dap.session._candidate_js_debug_servers",
                    return_value=[],
                ):
                    with self.assertRaises(DebugSessionError) as ctx:
                        resolve_adapter_argv(runtime="node")
        self.assertEqual(ctx.exception.status, 503)
        self.assertIn("Node debug adapter not found", ctx.exception.message)

    def test_resolve_node_adapter_discovers_candidate(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "dapDebugServer.js"
            adapter.write_text("// fake\n", encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k != "NAVIN_JS_DEBUG_ADAPTER"}
            with patch.dict(os.environ, env, clear=True):
                with patch("navin.dap.session.shutil.which", return_value="/usr/bin/node"):
                    with patch(
                        "navin.dap.session._candidate_js_debug_servers",
                        return_value=[adapter],
                    ):
                        argv = resolve_adapter_argv(runtime="node")
            self.assertEqual(argv[0], "/usr/bin/node")
            self.assertEqual(Path(argv[1]), adapter)

    def test_resolve_node_adapter_uses_env_path(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "dapDebugServer.js"
            adapter.write_text("// fake\n", encoding="utf-8")
            with patch.dict(os.environ, {"NAVIN_JS_DEBUG_ADAPTER": str(adapter)}):
                with patch("navin.dap.session.shutil.which", return_value="/usr/bin/node"):
                    argv = resolve_adapter_argv(runtime="node")
            self.assertEqual(argv[0], "/usr/bin/node")
            self.assertEqual(Path(argv[1]), adapter.resolve())


class DapDispatchTest(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "app.py").write_text("print('hi')\n", encoding="utf-8")
        self.scope = default_workspace_scope(self.root, True)
        self.manager = DebugManager()
        self.manager.configure_fake_adapter(str(_FIXTURE))
        # Patch the global manager used by debug_api.
        import navin.dap.manager as dap_manager
        import navin.webui.debug_api as debug_api

        self._prev = dap_manager._MANAGER
        dap_manager._MANAGER = self.manager
        debug_api.get_debug_manager = lambda: self.manager
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        import navin.dap.manager as dap_manager
        import navin.webui.debug_api as debug_api

        dap_manager._MANAGER = self._prev
        debug_api.get_debug_manager = dap_manager.get_debug_manager

    def test_dispatch_setbreakpoints_and_state(self) -> None:
        payload = debug_dispatch(
            self.scope,
            op="setBreakpoints",
            params={"path": "app.py", "lines": [1]},
        )
        self.assertTrue(payload["ok"])
        state = debug_dispatch(self.scope, op="state", params={})
        self.assertEqual(state["breakpoints"][0]["lines"], [1])

    def test_dispatch_start_and_stop(self) -> None:
        debug_dispatch(
            self.scope,
            op="setBreakpoints",
            params={"path": "app.py", "lines": [1]},
        )
        started = debug_dispatch(
            self.scope, op="start", params={"program": "app.py"},
        )
        self.assertTrue(started["ok"])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = debug_dispatch(self.scope, op="state", params={})
            if state.get("state") == "stopped":
                break
            time.sleep(0.02)
        stopped = debug_dispatch(self.scope, op="stop", params={})
        self.assertEqual(stopped["state"], "idle")

    def test_dispatch_start_node_runtime(self) -> None:
        (self.root / "app.js").write_text("console.log(1)\n", encoding="utf-8")
        started = debug_dispatch(
            self.scope,
            op="start",
            params={"program": "app.js", "runtime": "node"},
        )
        self.assertTrue(started["ok"])
        self.assertEqual(started["program"], "app.js")
        debug_dispatch(self.scope, op="stop", params={})


if __name__ == "__main__":
    unittest.main()
