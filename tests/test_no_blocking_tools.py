# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""No tool may freeze the engine, on any OS.

Every tool runs as ``async def execute`` on the gateway's single event loop,
shared by every chat, the WebUI and the terminal. A synchronous spawn without a
timeout, a PATH walk or a tree walk written straight into a coroutine, or a
POSIX-only call reached on Windows, is not a slow tool: it is a frozen
application. The audit of 2026-10-29 found and fixed about fifty of those from
the gateway log (403 "event loop blocked" reports); this test is what keeps
them from coming back.

Nothing here can be reproduced on the CI's Linux host, so like
``test_no_console_window.py`` it walks the syntax tree of the audited files and
fails the moment a new site breaks one of the rules below. A site that is safe
for a reason the rules cannot see goes in ``_EXEMPT`` with that reason.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PACKAGE = _REPO_ROOT / "navin"

# The audited perimeter: every tool plus the platform layer they stand on.
_SCOPE_GLOBS = (
    "agent/tools/**/*.py",
    "quality/*.py",
    "lsp/*.py",
    "dap/*.py",
    "index/*.py",
    "security/scanners/*.py",
    "security/verification/*.py",
    "security/analyzers/*.py",
    "security/remediation/*.py",
    "security/reports/*.py",
    "security/orchestrator/*.py",
    "process_runtime.py",
    "utils/proc.py",
    "utils/wsl.py",
    "utils/wsl_health.py",
    "utils/wsl_bulk.py",
    "utils/git_argv.py",
    "utils/git_state.py",
    "utils/git_credentials.py",
    "utils/child_processes.py",
    "webui/terminal_ws.py",
    "webui/project_search.py",
    "webui/fs_browse.py",
    "webui/fs_watch.py",
    "webui/file_preview.py",
    "webui/checkpoints_api.py",
    "webui/git_setup.py",
    "webui/git_clone.py",
    "webui/marketing_api.py",
)

# Rule 1: a synchronous spawn waits for the child; without ``timeout=`` a child
# that never exits (a wedged wsl.exe, a credential prompt) holds the caller
# forever. ``Popen`` itself returns at once and is judged by rule 2.
_SYNC_SPAWNS = {"run", "call", "check_call", "check_output"}
# Rule 2: ``communicate()`` and ``wait()`` on a Popen are the same wait; they
# need ``timeout=`` unless an ``asyncio.wait_for`` bounds them from outside.
# An awaited ``wait()`` is an asyncio process (it never blocks the loop), and a
# ``wait()`` in a function that just killed or terminated the child is bounded
# by that kill; both are accepted. ``wait()`` is only read on process-like
# receivers (``proc``, ``process``, ``child``, ``popen``): events wait too.
_WAITS = {"communicate", "wait"}
_PROCESS_RECEIVERS = ("proc", "process", "child", "popen")
# Rule 3: written straight into a coroutine body, these block the loop (PATH
# walks over /mnt/c took 11 s, tree walks minutes, in the gateway log). A
# nested ``def`` or lambda handed to ``asyncio.to_thread`` is the accepted shape.
_BLOCKING_IN_COROUTINE = {
    ("time", "sleep"),
    ("shutil", "which"),
    ("subprocess", "run"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("subprocess", "Popen"),
    ("os", "walk"),
}
_BLOCKING_METHODS_IN_COROUTINE = {"rglob"}
# Rule 4: these do not exist on Windows; the call must sit in a function (or
# class) that visibly checks the platform, or in a module that does.
_POSIX_ONLY = {
    ("os", "killpg"),
    ("os", "setsid"),
    ("os", "getpgid"),
    ("os", "fork"),
    ("pty", "openpty"),
    ("pty", "fork"),
    ("pty", "spawn"),
    ("select", "select"),
}
_POSIX_ONLY_MODULES = {"fcntl", "termios", "tty", "resource", "pwd", "grp"}
# Names whose presence in a function marks it as platform-aware.
_GUARD_NAMES = {"_IS_WINDOWS", "IS_WINDOWS", "is_windows", "_WINDOWS", "WINDOWS", "_IS_POSIX", "on_windows"}
_GUARD_CONSTANTS = {"win32", "nt", "Windows", "darwin", "linux", "posix"}

# Sites that break a rule for a reason the rules cannot see. Keyed by
# "<path>::<qualified name>" so a refactor cannot silently widen an exemption.
_EXEMPT: dict[str, str] = {
    "navin/webui/terminal_ws.py::_attach_child_tty": (
        "preexec_fn of UnixTerminalSession only: _create_session picks that "
        "class under _IS_WINDOWS, Windows terminals go through ConPTY"
    ),
    "navin/webui/terminal_ws.py::UnixTerminalSession.__init__": (
        "the POSIX terminal class; _create_session instantiates it only when "
        "not _IS_WINDOWS, WindowsTerminalSession is the other branch"
    ),
    "navin/webui/terminal_ws.py::UnixTerminalSession.resize": (
        "same class: TIOCSWINSZ on the PTY that only exists on POSIX"
    ),
    "navin/webui/terminal_ws.py::UnixTerminalSession._teardown": (
        "same class: ends the process group of the POSIX PTY child"
    ),
    "navin/webui/terminal_ws.py::UnixTerminalSession.close": (
        "same class: SIGHUP to the POSIX PTY child's group"
    ),
}


def _attr_pair(func: ast.AST) -> tuple[str | None, str | None]:
    """(base, attr) for ``base.attr`` calls, (None, name) for bare names."""
    if isinstance(func, ast.Attribute):
        base = getattr(func.value, "id", None) or getattr(func.value, "attr", None)
        return base, func.attr
    if isinstance(func, ast.Name):
        return None, func.id
    return None, None


def _has_keyword(call: ast.Call, name: str) -> bool:
    return any(keyword.arg == name for keyword in call.keywords)


def _mentions_guard(node: ast.AST, module_guards: set[str]) -> bool:
    """True when ``node`` visibly tests the platform somewhere inside."""
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id in (_GUARD_NAMES | module_guards):
            return True
        if isinstance(child, ast.Attribute):
            base = getattr(child.value, "id", None)
            if (base, child.attr) in {("sys", "platform"), ("os", "name")}:
                return True
            if base == "platform" and child.attr == "system":
                return True
        if isinstance(child, ast.Constant) and child.value in _GUARD_CONSTANTS:
            return True
    return False


def _module_guards(tree: ast.Module) -> set[str]:
    """Names assigned at module level from a platform test (``_IS_WINDOWS = ...``)."""
    guards: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            if value is not None and _mentions_guard(value, set()):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        guards.add(target.id)
    return guards


class _Scope:
    __slots__ = ("node", "name", "is_async")

    def __init__(self, node: ast.AST, name: str, is_async: bool) -> None:
        self.node = node
        self.name = name
        self.is_async = is_async


class _Visitor(ast.NodeVisitor):
    """Collect rule breaches together with the scope that contains them."""

    def __init__(self, tree: ast.Module) -> None:
        self.module_guards = _module_guards(tree)
        self.parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        self.scopes: list[_Scope] = []
        self.found: list[tuple[str, str, int]] = []

    # -- scopes ------------------------------------------------------------

    def _enter(self, node: ast.AST, name: str, is_async: bool) -> None:
        self.scopes.append(_Scope(node, name, is_async))
        self.generic_visit(node)
        self.scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._enter(node, node.name, False)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node, node.name, False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node, node.name, True)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._enter(node, "<lambda>", False)

    def _qualname(self) -> str:
        return ".".join(scope.name for scope in self.scopes if scope.name != "<lambda>") or "<module>"

    def _report(self, rule: str, node: ast.AST) -> None:
        self.found.append((self._qualname(), rule, node.lineno))

    def _bounded_by_wait_for(self, node: ast.AST) -> bool:
        current = self.parents.get(node)
        while current is not None and not isinstance(
            current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
        ):
            if isinstance(current, ast.Call) and _attr_pair(current.func)[1] == "wait_for":
                return True
            current = self.parents.get(current)
        return False

    def _guarded(self) -> bool:
        return any(_mentions_guard(scope.node, self.module_guards) for scope in self.scopes)

    def _function_kills(self) -> bool:
        """True when the enclosing function kills or terminates a child itself."""
        for scope in reversed(self.scopes):
            if isinstance(scope.node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return any(
                    isinstance(child, ast.Call) and _attr_pair(child.func)[1] in {"kill", "terminate"}
                    for child in ast.walk(scope.node)
                )
        return False

    def _wait_breach(self, node: ast.Call, base: str | None, attr: str) -> bool:
        if attr not in _WAITS:
            return False
        if _has_keyword(node, "timeout") or self._bounded_by_wait_for(node):
            return False
        awaited = isinstance(self.parents.get(node), ast.Await)
        if attr == "wait":
            receiver = (base or "").lower()
            if awaited or not any(word in receiver for word in _PROCESS_RECEIVERS):
                return False
            return not self._function_kills()
        return not (awaited and self._bounded_by_wait_for(node))

    # -- rules -------------------------------------------------------------

    def visit_Import(self, node: ast.Import) -> None:
        # Rule 5: a module-level ``import fcntl`` fails at import time on Windows
        # and takes the whole tool module with it. Under ``if``/``try`` or inside a
        # function it is a deliberate, late, guarded import.
        if not self.scopes and isinstance(self.parents.get(node), ast.Module):
            for alias in node.names:
                if alias.name.split(".")[0] in _POSIX_ONLY_MODULES:
                    self._report(f"posix-only import {alias.name} at module level", node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        base, attr = _attr_pair(node.func)
        in_coroutine = bool(self.scopes) and self.scopes[-1].is_async

        if base == "subprocess" and attr in _SYNC_SPAWNS and not _has_keyword(node, "timeout"):
            self._report(f"subprocess.{attr}() without timeout=", node)
        if self._wait_breach(node, base, attr):
            self._report(f"{base or 'child'}.{attr}() without timeout= (and no asyncio.wait_for around it)", node)
        if in_coroutine and ((base, attr) in _BLOCKING_IN_COROUTINE or attr in _BLOCKING_METHODS_IN_COROUTINE):
            self._report(f"{base or ''}{'.' if base else ''}{attr}() straight in a coroutine (use asyncio.to_thread)", node)
        if (base, attr) in _POSIX_ONLY or (base in _POSIX_ONLY_MODULES and attr):
            if not self._guarded():
                self._report(f"{base}.{attr}() reachable on Windows without a platform check", node)
        self.generic_visit(node)


def _breaches(path: Path) -> list[tuple[str, str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    visitor = _Visitor(tree)
    visitor.visit(tree)
    return visitor.found


def _scope_files() -> list[Path]:
    files: set[Path] = set()
    for pattern in _SCOPE_GLOBS:
        files.update(_PACKAGE.glob(pattern))
    return sorted(path for path in files if path.is_file())


class NoBlockingToolsTest(unittest.TestCase):
    def test_the_audited_perimeter_is_present(self) -> None:
        files = _scope_files()
        self.assertGreater(len(files), 60, "the perimeter shrank: check _SCOPE_GLOBS")

    def test_no_tool_can_freeze_the_engine(self) -> None:
        offenders: list[str] = []
        for path in _scope_files():
            rel = path.relative_to(_REPO_ROOT).as_posix()
            for qualname, rule, lineno in _breaches(path):
                if f"{rel}::{qualname}" in _EXEMPT:
                    continue
                offenders.append(f"  {rel}:{lineno} in {qualname}(): {rule}")
        self.assertEqual(
            offenders,
            [],
            "these can block the shared event loop or fail outright on Windows. "
            "Bound the wait (timeout=, asyncio.wait_for), move the work to "
            "asyncio.to_thread, guard the POSIX-only call with a platform check, "
            "or add the site to _EXEMPT with the reason it is safe:\n"
            + "\n".join(offenders),
        )

    def test_exemptions_still_point_at_real_code(self) -> None:
        """An exemption left behind after a refactor hides a real site."""
        stale: list[str] = []
        for key in _EXEMPT:
            rel, _, qualname = key.partition("::")
            path = _REPO_ROOT / rel
            if not path.exists():
                stale.append(f"{key} (file is gone)")
                continue
            if not any(found == qualname for found, _, _ in _breaches(path)):
                stale.append(f"{key} (no breach there any more)")
        self.assertEqual(stale, [], f"remove these stale exemptions: {stale}")


class RuleRecognitionTest(unittest.TestCase):
    """The rules fire on the shapes the audit found, and not on the fixed shapes."""

    @staticmethod
    def _rules(source: str) -> list[str]:
        tree = ast.parse(source)
        visitor = _Visitor(tree)
        visitor.visit(tree)
        return [f"{qualname}: {rule}" for qualname, rule, _ in visitor.found]

    def test_the_fixed_shapes_pass(self) -> None:
        source = (
            "import asyncio, os, shutil, subprocess, sys, time\n"
            "_IS_WINDOWS = sys.platform == 'win32'\n"
            "def sync_ok():\n"
            "    subprocess.run(['x'], timeout=5)\n"
            "    p = subprocess.Popen(['x'])\n"
            "    p.communicate(timeout=5)\n"
            "async def coro_ok():\n"
            "    def work():\n"
            "        return shutil.which('rg'), list(os.walk('.'))\n"
            "    await asyncio.to_thread(work)\n"
            "    await asyncio.to_thread(lambda: time.sleep(1))\n"
            "    proc = await asyncio.create_subprocess_exec('x')\n"
            "    await asyncio.wait_for(proc.communicate(), timeout=5)\n"
            "    proc.kill()\n"
            "    await proc.wait()\n"
            "def reap(process):\n"
            "    process.kill()\n"
            "    process.wait()\n"
            "async def gate(stop_event):\n"
            "    await stop_event.wait()\n"
            "def kill(pid):\n"
            "    if not _IS_WINDOWS:\n"
            "        os.killpg(pid, 9)\n"
        )
        self.assertEqual(self._rules(source), [])

    def test_the_audited_shapes_are_caught(self) -> None:
        source = (
            "import os, pty, shutil, subprocess, time\n"
            "import fcntl\n"
            "def sync_bad():\n"
            "    subprocess.check_output(['x'])\n"
            "    p = subprocess.Popen(['x'])\n"
            "    p.communicate()\n"
            "def wait_bad(process):\n"
            "    process.wait()\n"
            "async def coro_bad():\n"
            "    shutil.which('rg')\n"
            "    time.sleep(1)\n"
            "    subprocess.run(['x'], timeout=5)\n"
            "def posix_bad(pid):\n"
            "    os.killpg(pid, 9)\n"
            "    pty.openpty()\n"
        )
        rules = self._rules(source)
        self.assertEqual(len(rules), 9, rules)
        self.assertIn("<module>: posix-only import fcntl at module level", rules)
        self.assertIn("sync_bad: subprocess.check_output() without timeout=", rules)
        self.assertIn("sync_bad: p.communicate() without timeout= (and no asyncio.wait_for around it)", rules)
        self.assertIn("wait_bad: process.wait() without timeout= (and no asyncio.wait_for around it)", rules)
        self.assertIn("coro_bad: shutil.which() straight in a coroutine (use asyncio.to_thread)", rules)
        self.assertIn("coro_bad: time.sleep() straight in a coroutine (use asyncio.to_thread)", rules)
        self.assertIn("coro_bad: subprocess.run() straight in a coroutine (use asyncio.to_thread)", rules)
        self.assertIn("posix_bad: os.killpg() reachable on Windows without a platform check", rules)
        self.assertIn("posix_bad: pty.openpty() reachable on Windows without a platform check", rules)


if __name__ == "__main__":
    unittest.main()
