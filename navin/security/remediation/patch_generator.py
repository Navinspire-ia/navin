# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Mechanical fixes Navin writes itself, as unified diffs to review.

Only rewrites whose meaning is unambiguous are generated:

- ``yaml.load`` / ``yaml.load_all`` / ``yaml.unsafe_load`` on untrusted data
  become ``yaml.safe_load`` / ``yaml.safe_load_all``;
- a shell command built from a fixed prefix and whole-word values
  (``"ping -c 1 " + host``, ``f"ping -c 1 {host}"``) becomes an argument list
  without ``shell=True``;
- a JWT decode that accepts the ``none`` algorithm loses it, and disabled
  checks (``verify_exp``, ``verify_aud``...) are turned back on.

Everything else (SQL placeholders depend on the driver, tenant filters on the
model, auth on the framework) is left to a person or the agent, and the
generator says why instead of guessing. A generated diff is a proposal: it
goes through ``validate_patch`` and a rescan before anyone calls it a fix.
"""

from __future__ import annotations

import ast
import difflib
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from navin.security.analyzers.common import const_str, dotted, keyword
from navin.security.model import Finding

_SHELL_META = re.compile(r"[|&;<>()$`\\\"'*?\[\]#~=%{}!]")
_YAML_SAFE = {"yaml.load": "yaml.safe_load", "yaml.unsafe_load": "yaml.safe_load", "yaml.load_all": "yaml.safe_load_all"}
_JWT_CHECKS = frozenset({"verify_exp", "verify_aud", "verify_iss", "verify_nbf", "verify_iat"})


@dataclass
class ProposedPatch:
    finding: str  # fingerprint of the finding it addresses
    status: str  # generated | manual
    summary: str = ""
    diff: str = ""
    reason: str = ""

    @property
    def generated(self) -> bool:
        return self.status == "generated"


Fixer = Callable[[ast.Call, str], "tuple[str, str] | str"]


def _segment(source: str, node: ast.AST) -> str:
    text = ast.get_source_segment(source, node)
    if text is None:
        raise ValueError("no source segment")
    return text


def _call_source(source: str, call: ast.Call, *, func: str | None = None, args: list[str] | None = None,
                 drop: frozenset[str] = frozenset(), replace: dict[str, str] | None = None) -> str:
    parts = args if args is not None else [_segment(source, a) for a in call.args]
    parts = list(parts)
    for item in call.keywords:
        if item.arg in drop:
            continue
        if item.arg is None:
            parts.append(f"**{_segment(source, item.value)}")
        elif replace and item.arg in replace:
            parts.append(f"{item.arg}={replace[item.arg]}")
        else:
            parts.append(f"{item.arg}={_segment(source, item.value)}")
    return f"{func or _segment(source, call.func)}({', '.join(parts)})"


def _fix_yaml(call: ast.Call, source: str) -> tuple[str, str] | str:
    name = dotted(call.func)
    if name not in _YAML_SAFE or not call.args:
        return "not a yaml.load call with a document argument"
    return (_call_source(source, call, func=_YAML_SAFE[name], args=[_segment(source, call.args[0])], drop=frozenset({"Loader"})),
            f"{name} -> {_YAML_SAFE[name]}")


def _command_tokens(node: ast.expr, source: str) -> list[str] | None:
    """Argument list source for a command built from fixed words and whole-word values."""
    pieces: list[tuple[str, str]] = []  # ("text", value) or ("expr", source)

    def walk(part: ast.expr) -> bool:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            pieces.append(("text", part.value))
            return True
        if isinstance(part, ast.JoinedStr):
            for value in part.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    pieces.append(("text", value.value))
                elif isinstance(value, ast.FormattedValue) and value.conversion == -1 and value.format_spec is None:
                    pieces.append(("expr", _segment(source, value.value)))
                else:
                    return False
            return True
        if isinstance(part, ast.BinOp) and isinstance(part.op, ast.Add):
            return walk(part.left) and walk(part.right)
        if isinstance(part, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
            pieces.append(("expr", _segment(source, part)))
            return True
        return False

    if not walk(node) or not any(kind == "expr" for kind, _ in pieces):
        return None
    tokens: list[str] = []
    fixed_words: list[str] = []
    glued = False  # the previous piece ended inside a word
    for index, (kind, value) in enumerate(pieces):
        if kind == "text":
            if _SHELL_META.search(value):
                return None
            if glued and value[:1] not in (" ", "\t", ""):
                return None  # text glued to the end of a value: "x{a}y" is not a whole word
            words = value.split()
            if index + 1 < len(pieces) and value and not value[-1].isspace():
                return None  # a value glued to the end of fixed text
            tokens.extend(repr(word) for word in words)
            fixed_words.extend(words)
            glued = False
        else:
            if glued:
                return None  # two values glued together
            tokens.append(value)
            glued = True
    if not tokens or pieces[0][0] != "text" or not fixed_words:
        return None  # the program itself must be fixed text
    # shlex agrees with the plain split: no quoting or escaping was involved.
    fixed = " ".join(fixed_words)
    if shlex.split(fixed) != fixed.split():
        return None
    return tokens


def _fix_shell(call: ast.Call, source: str) -> tuple[str, str] | str:
    shell = keyword(call, "shell")
    if not (isinstance(shell, ast.Constant) and shell.value is True) or not call.args:
        return "not a shell=True call with a command argument"
    tokens = _command_tokens(call.args[0], source)
    if tokens is None:
        return "the command uses shell syntax or glues values to text: rewrite it as an argument list by hand"
    return (_call_source(source, call, args=[f"[{', '.join(tokens)}]", *(_segment(source, a) for a in call.args[1:])],
                         drop=frozenset({"shell"})),
            "shell command -> argument list without shell=True")


def _fix_jwt(call: ast.Call, source: str) -> tuple[str, str] | str:
    if not dotted(call.func).endswith("jwt.decode"):
        return "not a jwt.decode call"
    drop: set[str] = set()
    replace: dict[str, str] = {}
    notes: list[str] = []
    verify = keyword(call, "verify")
    if isinstance(verify, ast.Constant) and verify.value is False:
        return "signature verification is off: the fix needs the signing key and algorithm"
    algorithms = keyword(call, "algorithms")
    if isinstance(algorithms, (ast.List, ast.Tuple, ast.Set)):
        kept = [e for e in algorithms.elts if (const_str(e) or "").lower() != "none"]
        if len(kept) != len(algorithms.elts):
            if not kept:
                return "only the 'none' algorithm is accepted: the fix needs the real algorithm"
            if isinstance(algorithms, ast.List):
                open_, close = "[", "]"
            elif isinstance(algorithms, ast.Set):
                open_, close = "{", "}"
            else:
                open_, close = "(", ",)" if len(kept) == 1 else ")"
            replace["algorithms"] = open_ + ", ".join(_segment(source, e) for e in kept) + close
            notes.append("'none' algorithm removed")
    options = keyword(call, "options")
    if isinstance(options, ast.Dict):
        kept_items: list[str] = []
        for key_node, value in zip(options.keys, options.values):
            key = const_str(key_node) if key_node is not None else None
            if key == "verify_signature" and isinstance(value, ast.Constant) and value.value is False:
                return "signature verification is off: the fix needs the signing key and algorithm"
            if key in _JWT_CHECKS and isinstance(value, ast.Constant) and value.value is False:
                notes.append(f"{key} back on")
                continue
            kept_items.append(f"**{_segment(source, value)}" if key_node is None else f"{_segment(source, key_node)}: {_segment(source, value)}")
        if len(kept_items) != len(options.keys):
            if kept_items:
                replace["options"] = "{" + ", ".join(kept_items) + "}"
            else:
                drop.add("options")
    if not notes:
        return "nothing in this jwt.decode call can be fixed mechanically"
    return _call_source(source, call, drop=frozenset(drop), replace=replace), "JWT: " + ", ".join(notes)


_FIXERS: dict[str, Fixer] = {
    "navin-dataflow-deserialization": _fix_yaml,
    "navin-dataflow-command": _fix_shell,
    "navin-auth-jwt": _fix_jwt,
}


def _call_at(tree: ast.Module, line: int, fixer: Fixer, source: str) -> tuple[ast.Call, tuple[str, str]] | str:
    reason = "no call found at that line"
    candidates = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and n.lineno <= line <= (n.end_lineno or n.lineno)]
    for call in sorted(candidates, key=lambda n: (n.end_lineno or n.lineno) - n.lineno):  # innermost first
        result = fixer(call, source)
        if isinstance(result, tuple):
            return call, result
        reason = result
    return reason


def _replace_span(source: str, node: ast.AST, new: str) -> str:
    data = source.encode("utf-8")
    starts = [0]
    for raw in data.splitlines(keepends=True):
        starts.append(starts[-1] + len(raw))
    begin = starts[node.lineno - 1] + node.col_offset
    end = starts[node.end_lineno - 1] + node.end_col_offset
    return (data[:begin] + new.encode("utf-8") + data[end:]).decode("utf-8")


def propose_patch(root: str | Path, finding: Finding) -> ProposedPatch:
    """A reviewed-before-applied diff for *finding*, or the reason there is none."""
    fingerprint = finding.fingerprint()
    fixer = _FIXERS.get(finding.id)
    if fixer is None:
        return ProposedPatch(fingerprint, "manual", reason=f"no mechanical fix for {finding.id}: {finding.remediation or 'see the remediation'}")
    if not finding.file_path.endswith(".py") or not finding.line:
        return ProposedPatch(fingerprint, "manual", reason="automatic fixes only cover Python files with a known line")
    base = Path(root).resolve()
    path = (base / finding.file_path).resolve()
    if base not in path.parents or not path.is_file():
        return ProposedPatch(fingerprint, "manual", reason=f"{finding.file_path} is not a file of the project")
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
        found = _call_at(tree, finding.line, fixer, source)
    except (SyntaxError, ValueError) as exc:
        return ProposedPatch(fingerprint, "manual", reason=f"{finding.file_path} could not be read as Python: {exc}")
    if isinstance(found, str):
        return ProposedPatch(fingerprint, "manual", reason=found)
    call, (replacement, summary) = found
    updated = _replace_span(source, call, replacement)
    rel = path.relative_to(base).as_posix()
    diff = "".join(difflib.unified_diff(source.splitlines(True), updated.splitlines(True), f"a/{rel}", f"b/{rel}"))
    return ProposedPatch(fingerprint, "generated", summary=summary, diff=diff)


def propose_patches(root: str | Path, findings: list[Finding]) -> list[ProposedPatch]:
    return [propose_patch(root, item) for item in findings]
