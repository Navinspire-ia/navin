# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Every child process navin starts must stay invisible on Windows.

The desktop build has no console, so Windows hands each child a fresh one. It
appears and closes in the same instant, which the user sees as a flicker. The
editor polls - git status, the git panel, the review pane - so a single missing
flag turns into a window blinking every few seconds for as long as the Code
module is open.

Nothing about that is reproducible on Linux, where these flags do not exist and
every spawn behaves identically. So instead of running the processes, this walks
the syntax tree and asserts that each spawn site passes the flags, which fails
here the moment a new one forgets them.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = _REPO_ROOT / "navin"

# Calls that create a process, per module. ``subprocess.list2cmdline`` only
# builds a command line, and ``asyncio.run`` merely drives an event loop, so
# the attribute names are matched against the module they are read from.
_SPAWN_ATTRS = {
    "subprocess": {"run", "Popen", "call", "check_call", "check_output"},
    "asyncio": {"create_subprocess_exec", "create_subprocess_shell"},
    # Not a subprocess call, but it starts a process all the same, and it was
    # the one spawn site in the package that nothing here looked at.
    "PtyProcess": {"spawn"},
}
_HELPERS = {"no_window_kwargs", "detached_no_window_kwargs"}

# Spawn sites that must keep a console, with the reason each one is safe. Keyed
# by "<path>::<qualified name>" so moving code around does not silently expand
# the exemption the way a line number would.
_EXEMPT: dict[str, str] = {
    "navin/cli/commands.py::_open_webui_browser": (
        "opens the browser window the user asked for, from a CLI that already "
        "owns a console"
    ),
    "navin/webui/terminal_ws.py::UnixTerminalSession.__init__": (
        "the interactive terminal the user is looking at, and POSIX-only: "
        "Windows terminals go through ConPTY in WindowsTerminalSession"
    ),
    "navin/webui/terminal_ws.py::WindowsTerminalSession.__init__": (
        "ConPTY, which pywinpty gives no way to pass creationflags to. The "
        "console it would otherwise allocate and hide per session - the flicker "
        "this whole module exists to prevent - is pre-empted by the single "
        "hidden console _ensure_hidden_console allocates before the first spawn"
    ),
}


def _called_names(node: ast.AST) -> set[str]:
    """Every function name called anywhere inside ``node``."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name:
                names.add(name)
    return names


def _local_wrappers(tree: ast.AST) -> set[str]:
    """Module-local functions that stand in for the canonical helpers.

    A module that runs outside navin's own environment cannot import from
    ``navin.utils.proc`` unconditionally, so it wraps the helper behind a lazy
    import with a ``creationflags`` fallback. Accepting the wrapper by name alone
    would let any function called ``_no_window_kwargs`` wave a spawn site
    through, so it only counts once its body is seen to delegate.
    """
    wrappers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in _HELPERS:
            continue
        if _called_names(node) & _HELPERS:
            wrappers.add(node.name)
    return wrappers


def _hides_console(call: ast.Call, helpers: frozenset[str]) -> bool:
    """True when the call passes the no-window flags."""
    for keyword in call.keywords:
        # creationflags=..., spelled out rather than via the helper.
        if keyword.arg == "creationflags":
            return True
        # **no_window_kwargs()
        if keyword.arg is None:
            value = keyword.value
            if isinstance(value, ast.Call):
                func = value.func
                name = getattr(func, "id", None) or getattr(func, "attr", None)
                if name in helpers:
                    return True
            # **self._popen_platform_kwargs() and other computed mappings are
            # not inspected here; those live in _EXEMPT with their reason.
    return False


def _spawn_name(call: ast.Call) -> str | None:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    root = func.value
    base = getattr(root, "id", None) or getattr(root, "attr", None)
    if func.attr not in _SPAWN_ATTRS.get(base, ()):
        return None
    return f"{base}.{func.attr}"


class _Visitor(ast.NodeVisitor):
    """Collect spawn calls together with the scope that contains them."""

    def __init__(self, helpers: frozenset[str] = frozenset(_HELPERS)) -> None:
        self.helpers = helpers
        self.scope: list[str] = []
        self.found: list[tuple[str, str, int]] = []

    def _enter(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node, node.name)

    def visit_Call(self, node: ast.Call) -> None:
        name = _spawn_name(node)
        if name is not None and not _hides_console(node, self.helpers):
            self.found.append((".".join(self.scope) or "<module>", name, node.lineno))
        self.generic_visit(node)


def _unguarded_spawns(path: Path) -> list[tuple[str, str, int]]:
    """Spawn sites in one file that do not hide their console."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    visitor = _Visitor(frozenset(_HELPERS | _local_wrappers(tree)))
    visitor.visit(tree)
    return visitor.found


class NoConsoleWindowTest(unittest.TestCase):
    def test_every_spawn_site_hides_its_console(self) -> None:
        offenders: list[str] = []
        for path in sorted(_PACKAGE.rglob("*.py")):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for qualname, call, lineno in _unguarded_spawns(path):
                if f"{rel}::{qualname}" in _EXEMPT:
                    continue
                offenders.append(f"  {rel}:{lineno} in {qualname}() calls {call}()")

        self.assertEqual(
            offenders,
            [],
            "these spawn a child process that would open a console window on "
            "Windows; pass **no_window_kwargs() (or "
            "**detached_no_window_kwargs() when the child outlives navin) from "
            "navin.utils.proc, or add the site to _EXEMPT with a reason:\n"
            + "\n".join(offenders),
        )

    def test_exemptions_still_point_at_real_code(self) -> None:
        """An exemption left behind after a refactor hides a real spawn site."""
        stale: list[str] = []
        for key in _EXEMPT:
            rel, _, qualname = key.partition("::")
            path = _REPO_ROOT / rel
            if not path.exists():
                stale.append(f"{key} (file is gone)")
                continue
            if not any(found == qualname for found, _, _ in _unguarded_spawns(path)):
                stale.append(f"{key} (no unguarded spawn there any more)")
        self.assertEqual(stale, [], f"remove these stale exemptions: {stale}")


class WrapperRecognitionTest(unittest.TestCase):
    """A local wrapper may stand in for the helper, but only if it delegates."""

    @staticmethod
    def _offenders(source: str) -> list[str]:
        tree = ast.parse(source)
        visitor = _Visitor(frozenset(_HELPERS | _local_wrappers(tree)))
        visitor.visit(tree)
        return [f"{qualname}:{call}" for qualname, call, _ in visitor.found]

    def test_a_delegating_wrapper_is_accepted(self) -> None:
        """The html2pptx shape: lazy import behind a module-local wrapper."""
        source = (
            "import subprocess\n"
            "def _no_window_kwargs():\n"
            "    from navin.utils.proc import no_window_kwargs\n"
            "    return no_window_kwargs()\n"
            "def spawn():\n"
            "    subprocess.run(['x'], **_no_window_kwargs())\n"
        )
        self.assertEqual(self._offenders(source), [])

    def test_a_wrapper_that_hides_nothing_is_still_caught(self) -> None:
        """Naming a function after the helper must not launder a spawn site."""
        source = (
            "import subprocess\n"
            "def _no_window_kwargs():\n"
            "    return {}\n"
            "def spawn():\n"
            "    subprocess.run(['x'], **_no_window_kwargs())\n"
        )
        self.assertEqual(self._offenders(source), ["spawn:subprocess.run"])

    def test_a_bare_spawn_is_still_caught(self) -> None:
        source = "import subprocess\ndef spawn():\n    subprocess.run(['x'])\n"
        self.assertEqual(self._offenders(source), ["spawn:subprocess.run"])

    def test_the_real_chromium_launcher_is_guarded(self) -> None:
        """The site that regressed, asserted against the file itself."""
        path = _PACKAGE / "documents" / "html2pptx.py"
        self.assertEqual(
            [f"{name} ({call})" for name, call, _ in _unguarded_spawns(path)],
            [],
        )


class HelperTest(unittest.TestCase):
    def test_the_helper_is_inert_off_windows(self) -> None:
        from navin.utils import proc

        if proc.is_windows():
            self.skipTest("behaviour under test is the POSIX one")
        self.assertEqual(proc.no_window_kwargs(), {})
        self.assertEqual(proc.detached_no_window_kwargs(), {"start_new_session": True})

    def test_windows_gets_the_no_window_flag(self) -> None:
        """CREATE_NO_WINDOW is absent from the module on POSIX, so patch both."""
        from unittest import mock

        from navin.utils import proc

        with mock.patch.object(proc, "is_windows", return_value=True), mock.patch.object(
            proc, "_CREATE_NO_WINDOW", 0x08000000
        ):
            self.assertEqual(proc.no_window_kwargs(), {"creationflags": 0x08000000})
            flags = proc.detached_no_window_kwargs()["creationflags"]
            self.assertTrue(flags & 0x08000000, "CREATE_NO_WINDOW must be set")


if __name__ == "__main__":
    unittest.main()
