#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Find blocking calls written directly in async bodies anywhere in navin/.

A blocking call on the event loop freezes every chat, terminal and subagent of
the engine at once. This scanner lists the direct offenders (not wrapped in
``asyncio.to_thread`` / ``run_in_executor``, not inside a nested ``def``):
sync subprocesses, ``time.sleep``, whole-tree walks, file reads and writes,
sync HTTP clients. Usage: ``python scripts/scan_blocking_async.py [paths...]``.
Exit code 1 when offenders are found, so it can run as a check.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SUBPROCESS = {"run", "check_output", "check_call", "call", "Popen", "getoutput", "getstatusoutput"}
PATH_IO = {"read_text", "write_text", "read_bytes", "write_bytes", "iterdir", "rglob", "glob", "walk"}
SHUTIL = {"rmtree", "copytree", "move", "copy", "copy2", "copyfile", "which", "disk_usage"}
OS_CALLS = {"walk", "listdir", "scandir", "system", "popen"}
HTTP_SYNC = {"get", "post", "put", "delete", "patch", "head", "request"}
HTTP_MODULES = {"requests", "httpx", "urllib3"}
OFF_LOOP_WRAPPERS = {"to_thread", "run_in_executor", "run_sync", "run_off_loop", "run_blocking"}


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    return ""


def _is_wrapper(call: ast.Call) -> bool:
    func = call.func
    return isinstance(func, ast.Attribute) and func.attr in OFF_LOOP_WRAPPERS


def classify(call: ast.Call) -> str | None:
    func = call.func
    name = _name_of(func)
    if not isinstance(func, ast.Attribute):
        if isinstance(func, ast.Name) and func.id == "open":
            return "open()"
        return None
    base, attr = _name_of(func.value), func.attr
    root = base.split(".")[0]
    if root == "subprocess" and attr in SUBPROCESS:
        return "sync subprocess"
    if root == "time" and attr == "sleep":
        return "time.sleep"
    if root == "os" and attr in OS_CALLS:
        return f"os.{attr}"
    if root == "shutil" and attr in SHUTIL:
        return f"shutil.{attr}"
    if root in HTTP_MODULES and attr in HTTP_SYNC:
        return f"sync http {name}"
    if attr in PATH_IO and root not in {"self", "cls"}:
        # Path(...).read_text(), path.write_text(), folder.rglob(...)
        return f"path.{attr}"
    if attr in {"read_text", "write_text", "read_bytes", "write_bytes"}:
        return f"path.{attr}"
    return None


def scan_async_body(fn: ast.AsyncFunctionDef) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return  # a nested def is usually the body handed to to_thread
        if isinstance(node, ast.Call):
            if _is_wrapper(node):
                return  # whatever is inside runs off the loop
            kind = classify(node)
            if kind:
                found.append((node.lineno, kind))
        for child in ast.iter_child_nodes(node):
            visit(child)

    for stmt in fn.body:
        visit(stmt)
    return found


BLOCKING_OK = "# blocking-ok:"


def scan_file(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as exc:
        return [f"{path}: cannot parse: {exc}"]
    lines = source.splitlines()
    rows: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for line, kind in scan_async_body(node):
                # A documented exception carries its reason on the line above
                # or on the line itself: "# blocking-ok: <why>".
                above = lines[line - 2] if line >= 2 else ""
                here = lines[line - 1] if line >= 1 else ""
                if BLOCKING_OK in above or BLOCKING_OK in here:
                    continue
                rows.append(f"{path}:{line}: {kind} in async def {node.name}")
    return rows


def main(argv: list[str]) -> int:
    roots = [Path(p) for p in argv[1:]] or [Path("navin")]
    rows: list[str] = []
    for root in roots:
        files = [root] if root.is_file() else sorted(root.rglob("*.py"))
        for file in files:
            if "tests" in file.parts and root.name != "tests":
                continue
            rows.extend(scan_file(file))
    for row in rows:
        print(row)
    print(f"{len(rows)} blocking call(s) in async bodies", file=sys.stderr)
    return 1 if rows else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
