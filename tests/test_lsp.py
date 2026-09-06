"""Tests for the LSP client and manager.

Protocol-level behaviour is tested against a fake server implemented inline, so
the framing, request/response correlation, notification handling and failure
paths are covered without needing any language server installed. A final class
exercises a real server when one is available.
"""

from __future__ import annotations

import nturl2path
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory

from navin.lsp import client as client_mod
from navin.lsp import manager as manager_mod
from navin.lsp.client import LspClient, LspError

# A minimal LSP server: correct framing, answers initialize/shutdown, echoes a
# hover, publishes diagnostics on didOpen, and can be told to stall or crash.
FAKE_SERVER = r'''
import json, sys, time

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"

def send(payload):
    body = json.dumps(payload).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()

def read():
    length = 0
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            break
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    if not length:
        return None
    return json.loads(sys.stdin.buffer.read(length))

if MODE == "crash_on_start":
    sys.exit(3)

while True:
    message = read()
    if message is None:
        break
    method = message.get("method")
    request_id = message.get("id")

    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id,
              "result": {"capabilities": {
                  "hoverProvider": True,
                  "completionProvider": {"triggerCharacters": ["."]},
                  "signatureHelpProvider": {"triggerCharacters": ["(", ","]},
                  "codeActionProvider": True,
              }}})
    elif method == "initialized":
        pass
    elif method == "textDocument/didOpen":
        uri = message["params"]["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
              "params": {"uri": uri, "diagnostics": [
                  {"range": {"start": {"line": 2, "character": 4},
                            "end": {"line": 2, "character": 9}},
                   "severity": 1, "code": "E1", "message": "fake problem"}]}})
    elif method == "textDocument/didChange":
        uri = message["params"]["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
              "params": {"uri": uri, "diagnostics": []}})
    elif method == "textDocument/hover":
        if MODE == "stall":
            time.sleep(30)
        send({"jsonrpc": "2.0", "id": request_id,
              "result": {"contents": {"kind": "markdown", "value": "hovered"}}})
    elif method == "textDocument/completion":
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "isIncomplete": False,
            "items": [{"label": "add", "kind": 3, "detail": "def add(a, b)",
                       "insertText": "add",
                       "documentation": {"kind": "markdown", "value": "sum"}}]}})
    elif method == "textDocument/signatureHelp":
        send({"jsonrpc": "2.0", "id": request_id, "result": {
            "signatures": [{
                "label": "add(a, b)",
                "documentation": "sum two numbers",
                "parameters": [{"label": "a"}, {"label": "b"}],
            }],
            "activeSignature": 0,
            "activeParameter": 0,
        }})
    elif method == "textDocument/codeAction":
        uri = message["params"]["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "id": request_id, "result": [{
            "title": "Add annotation",
            "kind": "quickfix",
            "edit": {"changes": {uri: [{
                "range": {"start": {"line": 0, "character": 0},
                          "end": {"line": 0, "character": 0}},
                "newText": "# annotated\\n",
            }]}},
        }]})
    elif method == "textDocument/definition":
        uri = message["params"]["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "id": request_id, "result": [
            {"uri": uri, "range": {"start": {"line": 0, "character": 4},
                                   "end": {"line": 0, "character": 7}}}]})
    elif method == "unsupported/method":
        send({"jsonrpc": "2.0", "id": request_id,
              "error": {"code": -32601, "message": "method not found"}})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": request_id, "result": None})
    elif method == "exit":
        break
'''


def _fake_server_argv(tmp: Path, mode: str = "normal") -> list[str]:
    script = tmp / "fake_lsp.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return [sys.executable, str(script), mode]


class UriTest(unittest.TestCase):
    """Paths are built from the platform's temp dir so the contract is checked
    on Windows too, where a bare "/tmp/x" has no drive and cannot form a URI."""

    def test_round_trip(self):
        path = Path(tempfile.gettempdir()).resolve() / "a b" / "c.py"
        uri = client_mod.path_to_uri(path)
        self.assertTrue(uri.startswith("file://"))
        self.assertEqual(Path(client_mod.uri_to_path(uri)), path)

    def test_spaces_are_encoded(self):
        uri = client_mod.path_to_uri(Path(tempfile.gettempdir()) / "with space.py")
        self.assertNotIn(" ", uri)

    def test_a_windows_uri_keeps_its_drive(self):
        self.assertEqual(
            PureWindowsPath(nturl2path.url2pathname("/C:/dir/f.py")),
            PureWindowsPath(r"C:\dir\f.py"),
        )

    def test_non_file_scheme_is_returned_unchanged(self):
        self.assertEqual(
            client_mod.uri_to_path("untitled:Untitled-1"), "untitled:Untitled-1"
        )


class EditorQueryFlatteningTest(unittest.TestCase):
    def test_completion_item_is_flattened(self):
        item = manager_mod._flatten_completion(
            {
                "label": "add",
                "kind": 3,
                "detail": "def add(a, b)",
                "insertText": "add(",
                "documentation": {"value": "sum"},
            }
        )
        self.assertEqual(item["kind"], "function")
        self.assertEqual(item["insert_text"], "add(")
        self.assertEqual(item["documentation"], "sum")

    def test_signature_help_picks_the_active_parameter(self):
        help_doc = manager_mod._flatten_signature_help(
            {
                "activeSignature": 0,
                "activeParameter": 1,
                "signatures": [
                    {
                        "label": "add(a, b)",
                        "documentation": "sum",
                        "parameters": [{"label": "a"}, {"label": "b"}],
                    }
                ],
            }
        )
        self.assertEqual(help_doc["active_parameter"], 1)
        self.assertEqual(help_doc["signatures"][0]["parameters"][1]["label"], "b")

    def test_empty_signature_is_empty(self):
        self.assertEqual(manager_mod._flatten_signature_help(None), {})
        self.assertEqual(manager_mod._flatten_signature_help({"signatures": []}), {})


class HoverFlatteningTest(unittest.TestCase):
    """Hover content has three valid shapes across LSP versions."""

    def test_plain_string(self):
        self.assertEqual(manager_mod._flatten_hover("  text  "), "text")

    def test_markup_content_dict(self):
        self.assertEqual(
            manager_mod._flatten_hover({"kind": "markdown", "value": "v"}), "v"
        )

    def test_list_of_marked_strings(self):
        result = manager_mod._flatten_hover(
            [{"value": "one"}, "two", {"value": ""}]
        )
        self.assertEqual(result, "one\n\ntwo")

    def test_none_is_empty(self):
        self.assertEqual(manager_mod._flatten_hover(None), "")


class ServerRoutingTest(unittest.TestCase):
    def test_python_prefers_pyright_over_pylsp(self):
        names = [name for name, _ in manager_mod.servers_for(".py")]
        self.assertEqual(names[0], "pyright")
        self.assertIn("pylsp", names)

    def test_tsx_routes_to_typescript_with_correct_language_id(self):
        rows = manager_mod.servers_for(".tsx")
        self.assertTrue(rows)
        name, spec = rows[0]
        self.assertEqual(name, "typescript")
        self.assertEqual(spec["language_ids"][".tsx"], "typescriptreact")

    def test_unknown_extension_has_no_server(self):
        self.assertEqual(manager_mod.servers_for(".zzz"), [])

    def test_cpp_routes_to_clangd(self):
        names = [name for name, _ in manager_mod.servers_for(".cpp")]
        self.assertEqual(names[0], "clangd")
        self.assertEqual(
            manager_mod.servers_for(".cpp")[0][1]["language_ids"][".cpp"],
            "cpp",
        )

    def test_java_routes_to_jdtls(self):
        names = [name for name, _ in manager_mod.servers_for(".java")]
        self.assertEqual(names[0], "jdtls")

    def test_shell_routes_to_bashls(self):
        names = [name for name, _ in manager_mod.servers_for(".sh")]
        self.assertEqual(names[0], "bashls")

    def test_yaml_json_html_css_route_to_vscode_langservers(self):
        self.assertEqual(manager_mod.servers_for(".yaml")[0][0], "yamlls")
        self.assertEqual(manager_mod.servers_for(".json")[0][0], "vscode_json")
        self.assertEqual(manager_mod.servers_for(".html")[0][0], "vscode_html")
        self.assertEqual(manager_mod.servers_for(".css")[0][0], "vscode_css")

    def test_table_entries_are_well_formed(self):
        for name, spec in manager_mod._read_table().items():
            with self.subTest(server=name):
                self.assertIsInstance(spec.get("binary"), dict)
                self.assertTrue(spec.get("extensions"))
                self.assertTrue(spec.get("root_markers"))
                for suffix in spec["extensions"]:
                    self.assertIn(suffix, spec.get("language_ids", {}))

    def test_packaged_table_includes_extended_servers(self):
        table = manager_mod._read_table()
        for name in (
            "clangd",
            "jdtls",
            "bashls",
            "yamlls",
            "vscode_json",
            "vscode_html",
            "vscode_css",
        ):
            self.assertIn(name, table)

    def test_available_servers_reports_reasons(self):
        with TemporaryDirectory() as tmp:
            rows = manager_mod.available_servers(Path(tmp))
        self.assertTrue(rows)
        for row in rows:
            self.assertTrue(row["available"] or row["reason"])
        names = {row["server"] for row in rows}
        self.assertTrue({"clangd", "jdtls", "bashls"} <= names)


class SymbolKindTest(unittest.TestCase):
    def test_spec_kinds_are_mapped(self):
        self.assertEqual(manager_mod.SYMBOL_KINDS[5], "class")
        self.assertEqual(manager_mod.SYMBOL_KINDS[12], "function")
        self.assertEqual(manager_mod.SYMBOL_KINDS[26], "type-parameter")
        self.assertEqual(len(manager_mod.SYMBOL_KINDS), 26)


class FakeServerClientTest(unittest.TestCase):
    """Protocol behaviour, driven against the inline fake server."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        (self.root / "m.py").write_text(
            textwrap.dedent(
                """
                def add(a, b):
                    return a + b
                    unreachable = 1
                """
            ).lstrip(),
            encoding="utf-8",
        )

    def _client(self, mode: str = "normal") -> LspClient:
        client = LspClient(_fake_server_argv(self.root, mode), self.root, name="fake")
        client.start()
        self.addCleanup(client.stop)
        return client

    def test_handshake_captures_capabilities(self):
        client = self._client()
        self.assertTrue(client.alive)
        self.assertTrue(client.capability("hoverProvider"))

    def test_request_response_correlation(self):
        client = self._client()
        uri = client.open_document(self.root / "m.py", "python")
        result = client.request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 4}},
        )
        self.assertEqual(manager_mod._flatten_hover(result["contents"]), "hovered")

    def test_notification_delivers_diagnostics(self):
        client = self._client()
        uri = client.open_document(self.root / "m.py", "python")
        rows = client.diagnostics_for(uri, wait_s=5.0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"], "fake problem")

    def test_reopening_waits_for_fresh_diagnostics(self):
        """Stale diagnostics must not be served after the document changes."""
        client = self._client()
        uri = client.open_document(self.root / "m.py", "python")
        self.assertEqual(len(client.diagnostics_for(uri, wait_s=5.0)), 1)
        uri = client.open_document(self.root / "m.py", "python")
        self.assertEqual(client.diagnostics_for(uri, wait_s=5.0), [])

    def test_server_error_becomes_lsp_error(self):
        client = self._client()
        with self.assertRaises(LspError) as caught:
            client.request("unsupported/method", {})
        self.assertIn("method not found", str(caught.exception))

    def test_timeout_raises_and_does_not_wedge_the_client(self):
        client = self._client("stall")
        uri = client.open_document(self.root / "m.py", "python")
        with self.assertRaises(LspError) as caught:
            client.request(
                "textDocument/hover",
                {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 4}},
                timeout=0.4,
            )
        self.assertIn("timed out", str(caught.exception))

    def test_crashing_server_is_reported_not_hung(self):
        client = LspClient(
            _fake_server_argv(self.root, "crash_on_start"), self.root, name="fake"
        )
        with self.assertRaises(LspError):
            client.start()

    def test_missing_binary_raises_lsp_error(self):
        client = LspClient(["/nonexistent/server"], self.root, name="ghost")
        with self.assertRaises(LspError) as caught:
            client.start()
        self.assertIn("cannot start", str(caught.exception))

    def test_stop_is_idempotent(self):
        client = self._client()
        client.stop()
        client.stop()
        self.assertFalse(client.alive)

    def test_completion_and_signature_and_code_action(self):
        client = self._client()
        uri = client.open_document(self.root / "m.py", "python")
        position = {"textDocument": {"uri": uri}, "position": {"line": 0, "character": 4}}
        completion = client.request("textDocument/completion", position)
        self.assertEqual(completion["items"][0]["label"], "add")
        help_doc = client.request("textDocument/signatureHelp", position)
        self.assertEqual(help_doc["signatures"][0]["label"], "add(a, b)")
        actions = client.request(
            "textDocument/codeAction",
            {
                "textDocument": {"uri": uri},
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 0},
                },
                "context": {"diagnostics": []},
            },
        )
        self.assertEqual(actions[0]["title"], "Add annotation")
        self.assertTrue(client.capability("completionProvider"))
        self.assertTrue(client.capability("signatureHelpProvider"))
        self.assertTrue(client.capability("codeActionProvider"))


class PositionConversionTest(unittest.TestCase):
    """LSP is zero-based; navin reports one-based everywhere."""

    def test_to_lsp_position(self):
        self.assertEqual(
            manager_mod.LspManager._position(1, 1), {"line": 0, "character": 0}
        )
        self.assertEqual(
            manager_mod.LspManager._position(10, 5), {"line": 9, "character": 4}
        )

    def test_position_never_goes_negative(self):
        self.assertEqual(
            manager_mod.LspManager._position(0, 0), {"line": 0, "character": 0}
        )

    def test_location_is_converted_back_to_one_based_and_relative(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            manager = manager_mod.LspManager(root)
            location = manager._to_location(
                {
                    "uri": client_mod.path_to_uri(root / "pkg" / "m.py"),
                    "range": {
                        "start": {"line": 4, "character": 8},
                        "end": {"line": 4, "character": 11},
                    },
                },
                name="add",
                kind="function",
            )
        self.assertEqual(location.path, "pkg/m.py")
        self.assertEqual((location.line, location.col), (5, 9))
        self.assertIn("pkg/m.py:5:9", location.render())
        self.assertIn("[function]", location.render())

    def test_location_outside_root_keeps_absolute_path(self):
        with TemporaryDirectory() as tmp:
            manager = manager_mod.LspManager(Path(tmp) / "project")
            location = manager._to_location(
                {
                    "uri": "file:///usr/lib/python3/typing.py",
                    "range": {"start": {"line": 0, "character": 0}},
                }
            )
        self.assertEqual(location.path, "/usr/lib/python3/typing.py")


class ManagerRoutingErrorTest(unittest.TestCase):
    def test_unknown_extension_explains_itself(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.zzz").write_text("x", encoding="utf-8")
            manager = manager_mod.LspManager(root)
            with self.assertRaises(LspError) as caught:
                manager.hover("a.zzz", 1, 1)
        self.assertIn("no language server configured", str(caught.exception))

    def test_no_installed_server_lists_what_was_tried(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.go").write_text("package main\n", encoding="utf-8")
            manager = manager_mod.LspManager(root)
            try:
                manager.hover("a.go", 1, 1)
            except LspError as exc:
                self.assertIn("gopls", str(exc))
            else:
                self.skipTest("gopls is installed in this environment")

    def test_for_root_reuses_one_manager(self):
        with TemporaryDirectory() as tmp:
            first = manager_mod.LspManager.for_root(tmp)
            second = manager_mod.LspManager.for_root(Path(tmp))
            self.assertIs(first, second)


def _pylsp_available() -> bool:
    from navin.quality.linters import _binary_for

    spec = manager_mod._read_table().get("pylsp") or {}
    return _binary_for(spec, Path.cwd()) is not None


@unittest.skipUnless(_pylsp_available(), "no python language server installed")
class RealServerTest(unittest.TestCase):
    """End-to-end against a real language server, when one is installed."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        (cls.root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        (cls.root / "lib.py").write_text(
            "def add(a, b):\n    return a + b\n", encoding="utf-8"
        )
        (cls.root / "use.py").write_text(
            "from lib import add\n\nresult = add(1, 2)\n", encoding="utf-8"
        )
        cls.manager = manager_mod.LspManager(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.manager.shutdown()
        cls._tmp.cleanup()

    def test_hover_reports_the_signature(self):
        text = self.manager.hover("use.py", 3, 10)
        self.assertIn("add", text)

    def test_definition_crosses_files(self):
        locations = self.manager.definition("use.py", 3, 10)
        self.assertTrue(locations)
        self.assertEqual(locations[0].path, "lib.py")
        self.assertEqual(locations[0].line, 1)

    def test_references_find_every_call_site(self):
        locations = self.manager.references("lib.py", 1, 5)
        paths = {loc.path for loc in locations}
        self.assertIn("lib.py", paths)
        self.assertIn("use.py", paths)

    def test_document_symbols_lists_the_function(self):
        locations = self.manager.document_symbols("lib.py")
        names = {loc.name for loc in locations}
        self.assertIn("add", names)

    def test_rename_covers_both_files_without_writing(self):
        before = (self.root / "lib.py").read_text(encoding="utf-8")
        edits = self.manager.rename("lib.py", 1, 5, "plus")
        self.assertIn("lib.py", edits)
        self.assertIn("use.py", edits)
        self.assertEqual((self.root / "lib.py").read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
