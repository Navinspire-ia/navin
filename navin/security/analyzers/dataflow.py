# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Dataflow analyzer: user input that reaches a dangerous sink.

Python is analyzed on the AST, one function at a time. Sources are request
fields (``request.args``, ``request.json``, ``await request.form()``...) and
the parameters of a route handler that are not injected services; taint
follows assignments, string building (f-strings, ``+``, ``%``, ``format``,
``join``), container access and path joins. A recognized sanitizer stops it
(``int()``, ``shlex.quote`` for a shell, ``secure_filename`` for a path). An
unknown function call also stops it: the trace is intra-procedural, so
validation done inside a helper is not seen and never invented.

A complete trace from a source to a sink is dataflow evidence and confirms
the finding. JavaScript and TypeScript get line rules where the request field
sits inside the sink call itself; those stay potential.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

from navin.security.analyzers.common import (
    SourceSet,
    all_args,
    const_str,
    dotted,
    functions,
    keyword,
    line_of,
    line_text,
    route_of,
    short,
)
from navin.security.model import Evidence, Finding
from navin.security.verification.engine import confirm_dataflow
from navin.security.verification.probe import redact

ANALYZER_ID = "navin_dataflow"


@dataclass(frozen=True)
class Taint:
    steps: tuple[str, ...]
    cleared: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Sink:
    kind: str
    family: str
    cwe: str
    control: str
    severity: str
    title: str
    impact: str


SINKS: dict[str, Sink] = {
    "sql": Sink("sql", "injection", "CWE-89", "C1.01", "high", "SQL injection: user input builds a query", "exfiltration"),
    "command": Sink("command", "injection", "CWE-78", "C1.04", "critical", "Command injection: user input reaches a shell", "compromise"),
    "code": Sink("code", "code_execution", "CWE-94", "C1.04", "critical", "Code injection: user input reaches eval or exec", "compromise"),
    "path": Sink("path", "files", "CWE-22", "C1.06", "high", "Path traversal: user input chooses the file", "disclosure"),
    "deserialization": Sink("deserialization", "code_execution", "CWE-502", "C1.05", "critical", "Insecure deserialization of user input", "compromise"),
    "ssrf": Sink("ssrf", "network", "CWE-918", "C3.06", "high", "SSRF: user input chooses the URL the server fetches", "exfiltration"),
    "template": Sink("template", "injection", "CWE-1336", "C1.03", "high", "Template injection: user input is rendered as a template", "compromise"),
}

#: Controls this analyzer really checks: a clean run says something only about these.
CHECKED_CONTROLS = frozenset(s.control for s in SINKS.values())

_REQUEST_ATTRS = frozenset({
    "args", "form", "values", "json", "get_json", "data", "get_data", "files", "cookies", "headers",
    "query_params", "path_params", "GET", "POST", "FILES", "COOKIES", "META", "body", "stream", "query",
    "params", "match_info", "post", "view_args",
})
_SERVICE_PARAMS = frozenset({
    "self", "cls", "request", "req", "db", "session", "conn", "connection", "cursor", "background_tasks",
    "response", "settings", "config", "app", "current_user", "user", "principal", "claims", "token_data",
    "websocket", "ws", "logger", "cache", "redis",
})
_SERVICE_TYPES = re.compile(r"\b(Request|Response|Session|AsyncSession|BackgroundTasks|WebSocket|HTTPConnection|Connection|Engine|Redis|Logger)\b")
_SCALAR_TYPES = re.compile(r"^(?:Annotated\[\s*)?(?:Optional\[\s*)?(?:int|float|bool|UUID|uuid\.UUID|Decimal|date|datetime|PositiveInt|conint\([^)]*\))\b")

# The tables below name the dangerous calls this analyzer looks for in the
# audited code. Nothing in this module calls them.

_GENERIC_SANITIZERS = frozenset({"int", "float", "bool", "len", "abs", "round", "ord", "hash", "uuid.UUID", "UUID", "Decimal"})
_KIND_SANITIZERS = {
    "shlex.quote": "command", "pipes.quote": "command", "quote": "command",
    "os.path.basename": "path", "secure_filename": "path", "werkzeug.utils.secure_filename": "path",
}
_PROPAGATING_FUNCS = frozenset({
    "str", "repr", "bytes", "dict", "list", "tuple", "os.path.join", "os.path.normpath", "os.path.abspath",
    "os.path.expanduser", "posixpath.join", "urljoin", "urllib.parse.urljoin", "parse.urljoin", "Path",
    "pathlib.Path", "PurePath", "json.loads", "unquote", "urllib.parse.unquote", "base64.b64decode", "b64decode",
})
_PROPAGATING_METHODS = frozenset({
    "strip", "lstrip", "rstrip", "lower", "upper", "title", "capitalize", "casefold", "replace", "split",
    "rsplit", "splitlines", "partition", "rpartition", "encode", "decode", "get", "getlist", "pop", "copy",
    "removeprefix", "removesuffix", "zfill", "ljust", "rjust", "center", "translate", "items", "values",
    "model_dump", "dict", "to_dict", "read", "get_json", "json", "form", "body",
})
_SQL_METHODS = frozenset({"execute", "executemany", "executescript", "raw", "mogrify", "exec_driver_sql", "read_sql", "read_sql_query"})
_SQL_FUNCS = frozenset({"text", "sqlalchemy.text", "sa.text", "RawSQL", "sql.SQL"})
_SHELL_FUNCS = frozenset({"os.system", "os.popen", "commands.getoutput", "subprocess.getoutput", "subprocess.getstatusoutput", "asyncio.create_subprocess_shell"})
_PROCESS_FUNCS = frozenset({
    "subprocess.run", "subprocess.call", "subprocess.check_call", "subprocess.check_output", "subprocess.Popen",
    "run", "call", "check_call", "check_output", "Popen",
})
_CODE_FUNCS = frozenset({"eval", "exec", "compile"})
_PATH_FUNCS = frozenset({"open", "io.open", "send_file", "flask.send_file", "FileResponse", "os.remove", "os.unlink", "shutil.rmtree", "aiofiles.open"})
_PATH_METHODS = frozenset({"read_text", "read_bytes", "write_text", "write_bytes", "unlink", "open"})
_PICKLE_FUNCS = frozenset({"pickle.loads", "pickle.load", "cPickle.loads", "marshal.loads", "dill.loads", "jsonpickle.decode", "yaml.unsafe_load"})
_YAML_FUNCS = frozenset({"yaml.load", "yaml.load_all"})
_HTTP_MODULES = ("requests.", "httpx.", "urllib.request.urlopen", "urlopen", "urllib3.request", "aiohttp.request")
_TEMPLATE_FUNCS = frozenset({"render_template_string", "flask.render_template_string", "Template", "jinja2.Template", "mako.template.Template"})


class TaintEngine:
    """Which names of one function carry user input, and through which steps."""

    def __init__(self, rel: str, func: ast.FunctionDef | ast.AsyncFunctionDef, *, seeds: dict[str, Taint] | None = None):
        self.rel = rel
        self.func = func
        self.tainted: dict[str, Taint] = dict(seeds or {})
        self._propagate()

    # -- sources -----------------------------------------------------------

    def source(self, node: ast.AST) -> Taint | None:
        attrs: list[str] = []
        cur = node
        while isinstance(cur, (ast.Attribute, ast.Subscript, ast.Call, ast.Await)):
            if isinstance(cur, ast.Attribute):
                attrs.append(cur.attr)
                cur = cur.value
            elif isinstance(cur, ast.Call):
                cur = cur.func
            else:
                cur = cur.value
        if not isinstance(cur, ast.Name):
            return None
        attrs.reverse()
        if cur.id == "self" and attrs[:1] == ["request"]:
            attrs = attrs[1:]
        elif cur.id != "request":
            return None
        if not attrs or attrs[0] not in _REQUEST_ATTRS:
            return None
        if isinstance(node, ast.Call) and short(keyword(node, "type")) in {"int", "float", "bool"}:
            return None
        return Taint((f"{self.rel}:{getattr(node, 'lineno', self.func.lineno)} source {short(node)}",))

    # -- propagation -------------------------------------------------------

    def eval(self, node: ast.AST | None) -> Taint | None:
        if node is None:
            return None
        found = self.source(node)
        if found:
            return found
        if isinstance(node, ast.Name):
            return self.tainted.get(node.id)
        if isinstance(node, ast.Call):
            return self._eval_call(node)
        if isinstance(node, ast.JoinedStr):
            return self.any(v.value for v in node.values if isinstance(v, ast.FormattedValue))
        if isinstance(node, ast.FormattedValue):
            return self.eval(node.value)
        if isinstance(node, ast.BinOp):
            return self.any((node.left, node.right))
        if isinstance(node, (ast.Subscript, ast.Attribute, ast.Starred, ast.Await)):
            return self.eval(node.value)
        if isinstance(node, ast.IfExp):
            return self.any((node.body, node.orelse))
        if isinstance(node, ast.BoolOp):
            return self.any(node.values)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return self.any(node.elts)
        if isinstance(node, ast.Dict):
            return self.any(v for v in node.values if v is not None)
        return None

    def any(self, nodes) -> Taint | None:
        """Taint of a value built from several parts.

        A sink kind counts as sanitized only when every tainted part was
        sanitized for it: ``join(base, secure_filename(a), b)`` stays tainted.
        """
        parts = [found for found in (self.eval(item) for item in nodes) if found]
        if not parts:
            return None
        cleared = frozenset.intersection(*(part.cleared for part in parts))
        return Taint(parts[0].steps, cleared)

    def _eval_call(self, node: ast.Call) -> Taint | None:
        name = dotted(node.func)
        if name in _GENERIC_SANITIZERS:
            return None
        if short(keyword(node, "type")) in {"int", "float", "bool"}:
            return None  # request.args.get("id", type=int): Flask hands over a number
        arguments = [*node.args, *(k.value for k in node.keywords)]
        kind = _KIND_SANITIZERS.get(name)
        if kind:
            inner = self.any(arguments)
            return Taint(inner.steps, inner.cleared | {kind}) if inner else None
        if isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr in {"format", "format_map", "join"}:
                return self.any((node.func.value, *arguments))
            if attr in _PROPAGATING_METHODS:
                return self.eval(node.func.value)
        if name in _PROPAGATING_FUNCS:
            return self.any(arguments)
        return None

    def _propagate(self) -> None:
        writes: list[tuple[int, ast.AST, list[str]]] = []
        for node in ast.walk(self.func):
            if isinstance(node, ast.Assign):
                writes.append((node.lineno, node.value, _names(node.targets)))
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
                writes.append((node.lineno, node.value, _names([node.target])))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                writes.append((node.lineno, node.iter, _names([node.target])))
            elif isinstance(node, ast.NamedExpr):
                writes.append((node.lineno, node.value, _names([node.target])))
        writes.sort(key=lambda item: item[0])
        for _ in range(4):
            changed = False
            for line, value, targets in writes:
                found = self.eval(value)
                if not found:
                    continue
                for name in targets:
                    if name not in self.tainted:
                        step = f"{self.rel}:{line} {name} = {short(value, 100)}"
                        self.tainted[name] = Taint(found.steps + (step,), found.cleared)
                        changed = True
            if not changed:
                break


def _names(targets: list[ast.expr]) -> list[str]:
    out: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            out.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            out.extend(_names(list(target.elts)))
        elif isinstance(target, ast.Starred):
            out.extend(_names([target.value]))
    return out


def route_seeds(rel: str, func: ast.FunctionDef | ast.AsyncFunctionDef) -> dict[str, Taint]:
    """Parameters of a route handler that the client fills (path, query, body)."""
    route = route_of(func)
    if route is None:
        return {}
    seeds: dict[str, Taint] = {}
    for arg, default in all_args(func):
        if arg.arg in _SERVICE_PARAMS:
            continue
        if re.search(rf"<(?:int|float|uuid):{re.escape(arg.arg)}>", route.path):
            continue  # Flask converters hand over a number or a UUID, not raw text
        if isinstance(default, ast.Call) and dotted(default.func).rsplit(".", 1)[-1] in {"Depends", "Security"}:
            continue
        annotation = short(arg.annotation, 300) if arg.annotation is not None else ""
        if "Depends(" in annotation or "Security(" in annotation or _SERVICE_TYPES.search(annotation):
            continue
        if _SCALAR_TYPES.match(annotation):
            continue  # FastAPI validates int, UUID, date...: no text reaches a sink
        seeds[arg.arg] = Taint((f"{rel}:{func.lineno} parameter {arg.arg} of {route.endpoint}",))
    return seeds


def sinks_of(call: ast.Call) -> list[tuple[str, ast.expr]]:
    """The dangerous argument(s) of *call*, by sink kind."""
    name = dotted(call.func)
    last = name.rsplit(".", 1)[-1]
    first = call.args[0] if call.args else None
    out: list[tuple[str, ast.expr]] = []
    if isinstance(call.func, ast.Attribute) and last in _SQL_METHODS and first is not None:
        out.append(("sql", first))
    elif name in _SQL_FUNCS and first is not None:
        out.append(("sql", first))
    elif name in _SHELL_FUNCS and first is not None:
        out.append(("command", first))
    elif name in _PROCESS_FUNCS:
        shell = keyword(call, "shell")
        command = first if first is not None else keyword(call, "args")
        if isinstance(shell, ast.Constant) and shell.value is True and command is not None:
            out.append(("command", command))
    elif name in _CODE_FUNCS and first is not None:
        out.append(("code", first))
    elif name in _PATH_FUNCS and (first is not None or keyword(call, "path") is not None):
        out.append(("path", first if first is not None else keyword(call, "path")))
    elif isinstance(call.func, ast.Attribute) and last in _PATH_METHODS and not (
        isinstance(call.func.value, ast.Name) and call.func.value.id == "self"
    ):
        out.append(("path", call.func.value))  # Path(user_value).read_text(): the receiver is the path
    elif name in _PICKLE_FUNCS and first is not None:
        out.append(("deserialization", first))
    elif name in _YAML_FUNCS and first is not None:
        loader = short(keyword(call, "Loader"))
        if "Safe" not in loader:
            out.append(("deserialization", first))
    elif name.startswith(_HTTP_MODULES):
        url = keyword(call, "url")
        if url is None and call.args:
            url = call.args[1] if last == "request" and len(call.args) > 1 else call.args[0]
        if url is not None:
            out.append(("ssrf", url))
    elif (name in _TEMPLATE_FUNCS or last == "from_string") and first is not None:
        out.append(("template", first))
    return out


def analyze_python(sources: SourceSet) -> list[Finding]:
    findings: list[Finding] = []
    for src, tree in sources.python_trees():
        seen: set[tuple[str, int]] = set()
        for func in functions(tree):
            route = route_of(func)
            if route is None and "request" not in src.segment(func):
                continue  # no request object and no route parameter: nothing here comes from the client
            engine = TaintEngine(src.rel, func, seeds=route_seeds(src.rel, func))
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                for kind, argument in sinks_of(node):
                    taint = engine.eval(argument)
                    if not taint or kind in taint.cleared or (kind, node.lineno) in seen:
                        continue
                    seen.add((kind, node.lineno))
                    findings.append(_finding(src.rel, node, SINKS[kind], taint, route.endpoint if route else ""))
    return findings


def _finding(rel: str, call: ast.Call, sink: Sink, taint: Taint, endpoint: str) -> Finding:
    sink_step = f"{rel}:{call.lineno} sink {short(call, 120)}"
    item = Finding(
        id=f"navin-dataflow-{sink.kind}",
        family=sink.family,
        title=sink.title,
        severity=sink.severity,
        cwe=sink.cwe,
        file_path=rel,
        line=call.lineno,
        endpoint=endpoint,
        impact=sink.impact,
        controls=[sink.control],
        confidence=0.8,
        source=ANALYZER_ID,
        description=(
            f"{taint.steps[0].split(' ', 1)[-1]} reaches {short(call, 120)} with no sanitizer Navin recognizes. "
            "The trace stays inside one function: validation done in a called helper is not seen."
        ),
    )
    confirm_dataflow(item, [*taint.steps, sink_step], source=taint.steps[0], sink=sink_step, tool=ANALYZER_ID)
    return item


_JS_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\.(?:query|execute|raw|\$queryRawUnsafe|\$executeRawUnsafe)\(\s*(?:`[^`]*\$\{[^}]*\breq\.(?:query|body|params|headers|cookies)|['\"][^'\"]*['\"]\s*\+\s*req\.(?:query|body|params))"), "sql"),
    (re.compile(r"\b(?:exec|execSync)\(\s*(?:`[^`]*\$\{[^}]*\breq\.|['\"][^'\"]*['\"]\s*\+\s*req\.)"), "command"),
    (re.compile(r"\b(?:eval|new\s+Function)\(\s*req\.(?:query|body|params)"), "code"),
    (re.compile(r"\b(?:res\.sendFile|res\.download|fs\.(?:readFile|readFileSync|createReadStream|unlink))\(\s*(?:req\.(?:query|body|params)|`[^`]*\$\{[^}]*\breq\.|path\.join\([^)]*req\.(?:query|body|params))"), "path"),
    (re.compile(r"\b(?:axios(?:\.(?:get|post|request))?|fetch|got|request)\(\s*(?:req\.(?:query|body|params)|`[^`]*\$\{[^}]*\breq\.(?:query|body|params))"), "ssrf"),
)


def analyze_scripts(sources: SourceSet) -> list[Finding]:
    findings: list[Finding] = []
    for src in sources.scripts:
        if "req." not in src.text:
            continue
        for pattern, kind in _JS_RULES:
            for match in pattern.finditer(src.text):
                sink = SINKS[kind]
                line = line_of(src.text, match.start())
                findings.append(Finding(
                    id=f"navin-dataflow-{kind}",
                    family=sink.family,
                    title=sink.title,
                    severity=sink.severity,
                    cwe=sink.cwe,
                    file_path=src.rel,
                    line=line,
                    impact=sink.impact,
                    controls=[sink.control],
                    confidence=0.6,
                    source=ANALYZER_ID,
                    description="A request field is used inside the sink call itself.",
                    evidence=[Evidence(kind="static", description="request field inside the sink call", tool=ANALYZER_ID,
                                       observed=redact(line_text(src.text, line)), location=f"{src.rel}:{line}")],
                ))
    return findings


def analyze(sources: SourceSet) -> list[Finding]:
    return [*analyze_python(sources), *analyze_scripts(sources)]


__all__ = ["ANALYZER_ID", "CHECKED_CONTROLS", "SINKS", "Taint", "TaintEngine", "analyze", "const_str", "route_seeds", "sinks_of"]
