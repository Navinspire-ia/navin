# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""AST-backed Vision 360 checks for Python (high-confidence findings).

Regex scans are useful across languages, but for Python we can prove the
call sits in an ``async def``, takes a non-literal argument, etc. Findings
from this module are meant to be actionable - not noisy heuristics.
"""

from __future__ import annotations

import ast
import re
from typing import Any

_SECRET_NAME = re.compile(
    r"(?i)^(password|passwd|passphrase|secret|api_?key|credential|token)$"
)
_SECRET_TOKEN = re.compile(
    r"(?i)\b(password|passwd|passphrase|secret|api[_-]?key|credential)\b"
)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parts: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


def _is_async_def(node: ast.AST) -> bool:
    return isinstance(node, (ast.AsyncFunctionDef,))


def _lineno(node: ast.AST) -> int:
    return int(getattr(node, "lineno", 1) or 1)


def _snippet_from_lines(lines: list[str], lineno: int) -> str:
    if lineno < 1 or lineno > len(lines):
        return ""
    return lines[lineno - 1].strip()[:200]


def _finding(
    *,
    finding_id: str,
    severity: str,
    category: str,
    message: str,
    recommendation: str,
    file: str,
    line: int,
    snippet: str,
    confidence: str,
    evidence: str,
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity,
        "category": category,
        "message": message,
        "recommendation": recommendation,
        "file": file,
        "line": line,
        "snippet": snippet,
        "confidence": confidence,
        "evidence": evidence,
    }


def _arg_is_dynamic(node: ast.AST | None) -> bool:
    """True when the argument is not a plain constant / compile() of a const."""
    if node is None:
        return False
    if isinstance(node, ast.Constant):
        return False
    if isinstance(node, ast.JoinedStr):  # f-string - often user-influenced
        return True
    if isinstance(node, ast.Call):
        name = _call_name(node.func)
        if name == "compile" and node.args:
            return _arg_is_dynamic(node.args[0])
        return True
    return True


def _kw_false(node: ast.Call, name: str) -> bool:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value is False
    return False


def _kw_true(node: ast.Call, name: str) -> bool:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value is True
    return False


class _PythonAuditVisitor(ast.NodeVisitor):
    def __init__(self, *, rel: str, lines: list[str]) -> None:
        self.rel = rel
        self.lines = lines
        self.findings: list[dict[str, Any]] = []
        self._async_depth = 0
        self._func_stack: list[str] = []

    def _add(self, **kwargs: Any) -> None:
        self.findings.append(_finding(file=self.rel, **kwargs))

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._async_depth += 1
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()
        self._async_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = _call_name(node.func) or ""
        line = _lineno(node)
        snippet = _snippet_from_lines(self.lines, line)
        in_async = self._async_depth > 0

        if name in {"eval", "exec"}:
            arg0 = node.args[0] if node.args else None
            # exec(compile(...)) is the intentional ``python -c`` pattern.
            if name == "exec" and isinstance(arg0, ast.Call) and _call_name(arg0.func) == "compile":
                pass
            elif _arg_is_dynamic(arg0):
                self._add(
                    finding_id="eval-exec",
                    severity="high",
                    category="Exécution de code",
                    message=f"Appel `{name}()` sur une valeur dynamique (risque d'exécution de code)",
                    recommendation="Remplacer par un parsing explicite (ast.literal_eval, JSON, allow-list).",
                    line=line,
                    snippet=snippet,
                    confidence="high",
                    evidence=(
                        f"AST: `{name}(...)` dans "
                        f"`{self._func_stack[-1] if self._func_stack else '<module>'}` "
                        "avec un argument non constant."
                    ),
                )

        if name in {"pickle.load", "pickle.loads"}:
            self._add(
                finding_id="pickle",
                severity="high",
                category="Désérialisation",
                message="Désérialisation pickle (exécution de code arbitraire possible)",
                recommendation="Ne jamais désérialiser des données non fiables ; préférer JSON.",
                line=line,
                snippet=snippet,
                confidence="high",
                evidence=f"AST: appel `{name}()` détecté.",
            )

        if name == "yaml.load":
            has_loader = any(k.arg == "Loader" for k in node.keywords if k.arg)
            if not has_loader:
                self._add(
                    finding_id="yaml-load",
                    severity="medium",
                    category="Désérialisation",
                    message="yaml.load sans Loader explicite",
                    recommendation="Utiliser yaml.safe_load.",
                    line=line,
                    snippet=snippet,
                    confidence="high",
                    evidence="AST: `yaml.load(...)` sans keyword Loader.",
                )

        if name.startswith("subprocess.") and _kw_true(node, "shell"):
            self._add(
                finding_id="shell-true",
                severity="high",
                category="Commandes système",
                message="subprocess avec shell=True (risque d'injection de commande)",
                recommendation="Passer une liste d'arguments sans shell=True.",
                line=line,
                snippet=snippet,
                confidence="high",
                evidence=f"AST: `{name}(..., shell=True)`.",
            )

        if name == "os.system":
            self._add(
                finding_id="os-system",
                severity="medium",
                category="Commandes système",
                message="os.system (préférer subprocess avec arguments séparés)",
                recommendation="Remplacer par subprocess.run([...]) sans shell.",
                line=line,
                snippet=snippet,
                confidence="high",
                evidence="AST: appel `os.system(...)`.",
            )

        if name in {"hashlib.md5", "hashlib.sha1"}:
            # Only when hashing something that looks like a secret parameter.
            secretish = False
            for arg in node.args:
                if isinstance(arg, ast.Attribute) and _SECRET_NAME.match(arg.attr or ""):
                    secretish = True
                if isinstance(arg, ast.Name) and _SECRET_NAME.match(arg.id):
                    secretish = True
                if isinstance(arg, ast.Call) and arg.args:
                    inner = arg.args[0]
                    if isinstance(inner, ast.Name) and _SECRET_NAME.match(inner.id):
                        secretish = True
                    if isinstance(inner, ast.Attribute) and _SECRET_NAME.match(inner.attr or ""):
                        secretish = True
            # Also look at nearby source for password= context on same function.
            window = "\n".join(
                self.lines[max(0, line - 4) : min(len(self.lines), line + 2)]
            )
            if secretish or _SECRET_TOKEN.search(window):
                algo = name.split(".")[-1].upper()
                self._add(
                    finding_id="weak-hash",
                    severity="medium",
                    category="Cryptographie",
                    message=f"{algo} utilisé dans un contexte secret / mot de passe",
                    recommendation="Utiliser bcrypt/argon2 pour les mots de passe.",
                    line=line,
                    snippet=snippet,
                    confidence="high" if secretish else "medium",
                    evidence=f"AST: `{name}(...)` avec contexte secret détecté.",
                )

        if in_async and name == "time.sleep":
            self._add(
                finding_id="sleep-in-async",
                severity="high",
                category="Event loop",
                message="time.sleep directement dans une coroutine async (bloque l'event loop)",
                recommendation="Utiliser await asyncio.sleep(...) ou asyncio.to_thread.",
                line=line,
                snippet=snippet,
                confidence="high",
                evidence=(
                    f"AST: `time.sleep(...)` dans async def "
                    f"`{self._func_stack[-1] if self._func_stack else '?'}`."
                ),
            )

        if in_async and name.startswith("requests."):
            self._add(
                finding_id="requests-in-async",
                severity="medium",
                category="Event loop",
                message="Appel HTTP synchrone (requests) dans une coroutine async",
                recommendation="Préférer httpx.AsyncClient ou déporter via asyncio.to_thread.",
                line=line,
                snippet=snippet,
                confidence="high",
                evidence=f"AST: `{name}(...)` dans une coroutine async.",
            )

        if in_async and name in {
            "subprocess.run",
            "subprocess.call",
            "subprocess.check_output",
            "subprocess.check_call",
            "subprocess.getoutput",
            "subprocess.getstatusoutput",
        }:
            self._add(
                finding_id="sync-subprocess-async",
                severity="medium",
                category="Event loop",
                message="subprocess synchrone directement dans une coroutine async",
                recommendation="Utiliser asyncio.create_subprocess_exec ou asyncio.to_thread.",
                line=line,
                snippet=snippet,
                confidence="high",
                evidence=f"AST: `{name}(...)` dans une coroutine async.",
            )

        # verify=False only when not clearly a documented TLS fallback retry.
        if _kw_false(node, "verify") or (
            name.endswith("request") and _kw_false(node, "verify")
        ):
            window = "\n".join(
                self.lines[max(0, line - 18) : min(len(self.lines), line + 1)]
            )
            if not re.search(
                r"(?i)CERTIFICATE_VERIFY_FAILED|verify\s*=\s*True|SSL verification failed",
                window,
            ):
                self._add(
                    finding_id="tls-verify-off",
                    severity="high",
                    category="TLS",
                    message="Vérification TLS désactivée (verify=False)",
                    recommendation="Réactiver la vérification des certificats ; utiliser un bundle CA interne si nécessaire.",
                    line=line,
                    snippet=snippet,
                    confidence="high",
                    evidence="AST: appel avec keyword verify=False, sans fallback TLS documenté juste au-dessus.",
                )

        self.generic_visit(node)


def scan_python_file_ast(rel: str, text: str) -> list[dict[str, Any]]:
    """Return high-confidence security/perf findings for one Python file."""
    if not text.strip():
        return []
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError:
        return []
    visitor = _PythonAuditVisitor(rel=rel, lines=text.splitlines())
    visitor.visit(tree)
    return visitor.findings
