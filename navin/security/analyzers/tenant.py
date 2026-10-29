# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Multi-tenant isolation analyzer.

It first learns which tables and models are tenant-scoped (SQL ``CREATE
TABLE``, Alembic ``create_table``, SQLAlchemy and Django models, Prisma
schema) and which tables the database itself protects with Row Level
Security. ``tenant_id``, ``organization_id`` and ``workspace_id`` always mark
a tenant; ``account_id`` and ``company_id`` also name a customer record in a
CRM, so they mark a tenant only when users or members carry them. When the
same table name is defined in several places (two SQLite books, two
services), a query is judged against the definition closest to it in the
tree. Then it flags:

- a function that reads or writes a tenant-scoped table or model and never
  mentions the tenant (nor a membership check);
- a tenant id the client chooses: body field, query parameter, ``X-Tenant``
  header, or a URL segment with no membership check;
- a Prisma call on a tenant-scoped model whose ``where`` has no tenant field.

With no tenant-scoped schema in the project, the first and last checks have
nothing to compare against and say so in the notes.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from navin.security.analyzers.common import (
    TENANT_RE,
    SourceSet,
    all_args,
    const_str,
    dotted,
    functions,
    line_of,
    line_text,
    route_of,
    short,
)
from navin.security.analyzers.dataflow import TaintEngine, route_seeds
from navin.security.model import Evidence, Finding
from navin.security.verification.probe import redact

ANALYZER_ID = "navin_tenant"
CHECKED_CONTROLS = frozenset({"C2.09"})

# Column names compared lowercased, without underscores.
STRONG_COLUMNS = frozenset({"tenantid", "organizationid", "organisationid", "orgid", "workspaceid"})
WEAK_COLUMNS = frozenset({"accountid", "companyid"})
_MEMBER_NAME = re.compile(r"user|member", re.I)
_RELATION_CALLS = frozenset({"ForeignKey", "relationship", "OneToOneField"})
_ORM_BASE = re.compile(r"(?:^|\.)(?:Base|Model|SQLModel|DeclarativeBase|Document|Entity|AbstractUser|AbstractBaseUser)$")
_MEMBERSHIP = re.compile(
    r"\b(?:is|has|ensure|check|require|verify|assert)_\w*(?:member|tenant|org|organization|access|permission)\w*|"
    r"\bmembership\b|\bbelongs_to\b|\b(?:current_)?user\.(?:tenant|org|organization|workspace|account)\w*|"
    r"\bauthorize\w*\(|\bhas_access\b", re.I)
_CREATE_TABLE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?:[\w\"`]+\.)?[\"`]?(\w+)[\"`]?\s*\((.*?)\)\s*;", re.I | re.S)
_CONSTRAINT = re.compile(r"^(?:PRIMARY|FOREIGN|UNIQUE|CHECK|CONSTRAINT|INDEX|KEY|EXCLUDE)\b", re.I)
_ALEMBIC_TABLE = re.compile(r"create_table\(\s*['\"](\w+)['\"](.{0,4000}?)\n\s*\)", re.S)
_ALEMBIC_COLUMN = re.compile(r"Column\(\s*['\"](\w+)['\"]")
_RLS = re.compile(r"ALTER\s+TABLE\s+(?:ONLY\s+)?(?:[\w\"`]+\.)?[\"`]?(\w+)[\"`]?\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY", re.I)
_SQL_STATEMENT = re.compile(r"^\s*(?:SELECT|UPDATE|DELETE|INSERT|WITH)\b", re.I)
_SQL_TABLES = re.compile(r"\b(?:FROM|UPDATE|JOIN|INTO)\s+(?:[\w\"`]+\.)?[\"`]?(\w+)", re.I)
_PRISMA_MODEL = re.compile(r"^model\s+(\w+)\s*\{(.*?)^\}", re.M | re.S)
_PRISMA_FIELD = re.compile(r"^\s*(\w+)\s+[A-Z]\w*", re.M)
_PRISMA_CALL = re.compile(r"\bprisma\.(\w+)\.(findUnique|findUniqueOrThrow|findFirst|findFirstOrThrow|findMany|update|updateMany|delete|deleteMany|upsert)\(")
_SCHEMA_BASES = re.compile(r"BaseModel|Schema|TypedDict|Serializer|Form|Enum")
_SQL_METHODS = frozenset({"execute", "executemany", "raw", "exec_driver_sql", "read_sql", "read_sql_query"})
_SQL_FUNCS = frozenset({"text", "sqlalchemy.text", "sa.text", "RawSQL"})
_ORM_ENTRY = frozenset({"get", "get_or_404", "get_object_or_404", "query", "select", "delete", "update", "insert", "filter_by"})


def norm(name: str) -> str:
    """``tenant_id``, ``tenantId`` and ``Tenant-Id`` compare equal."""
    return re.sub(r"[\W_]", "", name).lower()


def _shared_dirs(a: str, b: str) -> int:
    count = 0
    for left, right in zip(a.split("/")[:-1], b.split("/")[:-1]):
        if left != right:
            break
        count += 1
    return count


@dataclass
class TenantSchema:
    tables: dict[str, list[tuple[str, bool]]] = field(default_factory=dict)  # name -> [(defining file, scoped)]
    models: dict[str, list[tuple[str, bool]]] = field(default_factory=dict)
    prisma_models: set[str] = field(default_factory=set)
    rls: set[str] = field(default_factory=set)
    weak_markers: set[str] = field(default_factory=set)

    @property
    def empty(self) -> bool:
        defined = [*self.tables.values(), *self.models.values()]
        return not self.prisma_models and not any(scoped for entries in defined for _, scoped in entries)

    def is_marker(self, column: str) -> bool:
        key = norm(column)
        return key in STRONG_COLUMNS or key in self.weak_markers

    def tenant_key(self, key: str) -> bool:
        """A request field, a parameter or an ``X-Tenant``-style header that names the tenant."""
        if key[:2].lower() == "x-":
            base = norm(key[2:])
            return self.is_marker(base if base.endswith("id") else base + "id")
        return self.is_marker(key)

    def scoped(self, kind: str, name: str, rel: str) -> bool:
        """Whether *name* is tenant-scoped as seen from *rel*: the closest definition decides."""
        entries = (self.tables if kind == "table" else self.models).get(name)
        if not entries:
            return False
        best = max(_shared_dirs(rel, other) for other, _ in entries)
        return all(scoped for other, scoped in entries if _shared_dirs(rel, other) == best)


def _sql_columns(body: str) -> set[str]:
    columns: set[str] = set()
    for part in re.split(r",(?![^(]*\))", body):
        part = part.strip()
        if not part or _CONSTRAINT.match(part):
            continue
        match = re.match(r"[\"`\[]?(\w+)", part)
        if match:
            columns.add(norm(match.group(1)))
    return columns


def _model_columns(cls: ast.ClassDef) -> tuple[str, set[str]] | None:
    """(table name, column names) of an ORM model class; None for any other class."""
    table = ""
    columns: set[str] = set()
    for stmt in cls.body:
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            targets, value = [stmt.target], stmt.value
        else:
            continue
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "__tablename__":
                table = const_str(value) or ""
            elif not target.id.startswith("_"):
                columns.add(norm(target.id))
                if isinstance(value, ast.Call) and dotted(value.func).rsplit(".", 1)[-1] in _RELATION_CALLS:
                    columns.add(norm(target.id) + "id")  # Django: organization = ForeignKey(...) stores organization_id
    looks_orm = bool(table) or any(_ORM_BASE.search(short(base)) for base in cls.bases)
    return (table, columns) if looks_orm and columns else None


def tenant_schema(sources: SourceSet) -> TenantSchema:
    tables: list[tuple[str, str, set[str]]] = []  # (table, defining file, columns)
    models: list[tuple[str, str, set[str]]] = []
    prisma: list[tuple[str, set[str]]] = []
    schema = TenantSchema()
    for src in [*sources.sql, *sources.python]:
        for name, body in _CREATE_TABLE.findall(src.text):
            tables.append((name.lower(), src.rel, _sql_columns(body)))
        schema.rls.update(name.lower() for name in _RLS.findall(src.text))
    for src in sources.python:
        for name, body in _ALEMBIC_TABLE.findall(src.text):
            tables.append((name.lower(), src.rel, {norm(c) for c in _ALEMBIC_COLUMN.findall(body)}))
    for src, tree in sources.python_trees():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or any(_SCHEMA_BASES.search(short(b)) for b in node.bases):
                continue
            learned = _model_columns(node)
            if learned:
                table, columns = learned
                models.append((node.name, src.rel, columns))
                if table:
                    tables.append((table.lower(), src.rel, columns))
    for src in sources.prisma:
        for name, body in _PRISMA_MODEL.findall(src.text):
            prisma.append((name, {norm(f) for f in _PRISMA_FIELD.findall(body)}))
    named = [(t, c) for t, _, c in tables] + [(m, c) for m, _, c in models] + prisma
    # "account" is a tenant when users or members belong to one; otherwise it is a customer record.
    schema.weak_markers = {col for name, cols in named if _MEMBER_NAME.search(name) for col in cols & WEAK_COLUMNS}
    markers = STRONG_COLUMNS | schema.weak_markers
    for table, rel, columns in tables:
        schema.tables.setdefault(table, []).append((rel, bool(columns & markers)))
    for model, rel, columns in models:
        schema.models.setdefault(model, []).append((rel, bool(columns & markers)))
    schema.prisma_models = {name[:1].lower() + name[1:] for name, columns in prisma if columns & markers}
    return schema


def _static_sql(node: ast.AST | None, names: dict[str, str]) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "?" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _static_sql(node.left, names) + _static_sql(node.right, names)
    if isinstance(node, ast.Name):
        return names.get(node.id, "")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return _static_sql(node.func.value, names)
    if isinstance(node, ast.Call) and dotted(node.func) in _SQL_FUNCS and node.args:
        return _static_sql(node.args[0], names)
    return ""


def _unscoped_access(func: ast.FunctionDef | ast.AsyncFunctionDef, schema: TenantSchema, rel: str) -> tuple[int, str] | None:
    names: dict[str, str] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            text = _static_sql(node.value, names)
            if text:
                names[node.targets[0].id] = text
    hits: list[tuple[int, str]] = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        name = dotted(node.func)
        parts = name.split(".") if name else []
        last = parts[-1] if parts else ""
        first = node.args[0] if node.args else None
        if last in _ORM_ENTRY and isinstance(first, ast.Name) and schema.scoped("model", first.id, rel):
            hits.append((node.lineno, f"{short(node)} reaches {first.id}"))
        elif parts and ("query" in parts[1:] or "objects" in parts[1:]) and schema.scoped("model", parts[0], rel):
            hits.append((node.lineno, f"{short(node)} reaches {parts[0]}"))
        elif (last in _SQL_METHODS and isinstance(node.func, ast.Attribute)) or name in _SQL_FUNCS:
            sql = _static_sql(first, names) if first is not None else ""
            if not _SQL_STATEMENT.match(sql):
                continue
            scoped = {t.lower() for t in _SQL_TABLES.findall(sql) if schema.scoped("table", t.lower(), rel)} - schema.rls
            if scoped:
                hits.append((node.lineno, f"query on {', '.join(sorted(scoped))}: {' '.join(sql.split())[:160]}"))
    return min(hits) if hits else None


def _client_tenant(rel: str, func: ast.FunctionDef | ast.AsyncFunctionDef, schema: TenantSchema) -> tuple[int, str] | None:
    route = route_of(func)
    engine = TaintEngine(rel, func, seeds=route_seeds(rel, func))
    for node in ast.walk(func):
        key: str | None = None
        base: ast.AST | None = None
        if isinstance(node, ast.Subscript):
            key, base = const_str(node.slice), node.value
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"get", "getlist"} and node.args:
            key, base = const_str(node.args[0]), node.func.value
        elif isinstance(node, ast.Attribute):
            key, base = node.attr, node.value
        if not key or not schema.tenant_key(key):
            continue
        if engine.source(base) or engine.eval(base):
            return node.lineno, f"the tenant comes from the client: {short(node)}"
    if route is not None:
        for arg, default in all_args(func):
            if not schema.tenant_key(arg.arg):
                continue
            if isinstance(default, ast.Call) and dotted(default.func).rsplit(".", 1)[-1] in {"Depends", "Security"}:
                continue
            annotation = short(arg.annotation, 300) if arg.annotation is not None else ""
            if "Depends(" in annotation or "Security(" in annotation:
                continue
            name = arg.arg
            in_path = f"{{{name}}}" in route.path or re.search(rf"<(?:\w+:)?{re.escape(name)}>", route.path)
            where = "the URL" if in_path else "a query parameter"
            return func.lineno, f"the tenant comes from {where} ({name}) of {route.endpoint}"
    return None


def _balanced(text: str, start: int, limit: int = 2000) -> str:
    depth = 0
    for index in range(start, min(len(text), start + limit)):
        char = text[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start: index + 1]
    return text[start: start + limit]


def _finding(rel: str, line: int, text: str, *, kind: str, detail: str, endpoint: str = "") -> Finding:
    unscoped = kind == "unscoped"
    return Finding(
        id=f"navin-tenant-{kind}", family="multitenancy", severity="high",
        cwe="CWE-863" if unscoped else "CWE-639",
        title=("Tenant-scoped data accessed without a tenant filter" if unscoped else "Tenant chosen by the client without a membership check"),
        file_path=rel, line=line, endpoint=endpoint, controls=["C2.09"], impact="exfiltration",
        confidence=0.5 if unscoped else 0.45, source=ANALYZER_ID,
        description=detail + ". Confirm with two tenants: a user of tenant A must not reach tenant B's rows.",
        evidence=[Evidence(kind="static", description=detail, tool=ANALYZER_ID, observed=redact(line_text(text, line)), location=f"{rel}:{line}")],
    )


def analyze(sources: SourceSet) -> list[Finding]:
    schema = tenant_schema(sources)
    if schema.empty:
        sources.notes.append("no tenant-scoped table or model found: unscoped queries were not checked")
    findings: list[Finding] = []
    for src, tree in sources.python_trees():
        seen: set[tuple[str, int]] = set()
        for func in functions(tree):
            text = src.segment(func)
            if _MEMBERSHIP.search(text):
                continue
            route = route_of(func)
            endpoint = route.endpoint if route else ""
            if route is not None or "request" in text:  # nothing else can hand over a client value
                chosen = _client_tenant(src.rel, func, schema)
                if chosen and ("client-chosen", chosen[0]) not in seen:
                    seen.add(("client-chosen", chosen[0]))
                    findings.append(_finding(src.rel, chosen[0], src.text, kind="client-chosen", detail=chosen[1], endpoint=endpoint))
            if not schema.empty and not TENANT_RE.search(text):
                hit = _unscoped_access(func, schema, src.rel)
                if hit and ("unscoped", hit[0]) not in seen:
                    seen.add(("unscoped", hit[0]))
                    findings.append(_finding(src.rel, hit[0], src.text, kind="unscoped", detail=hit[1], endpoint=endpoint))
    for src in sources.scripts:
        if not schema.prisma_models or "prisma." not in src.text:
            continue
        for match in _PRISMA_CALL.finditer(src.text):
            if match.group(1) not in schema.prisma_models:
                continue
            call = _balanced(src.text, match.end() - 1)
            if TENANT_RE.search(call):
                continue
            line = line_of(src.text, match.start())
            findings.append(_finding(src.rel, line, src.text, kind="unscoped",
                                     detail=f"prisma.{match.group(1)}.{match.group(2)} has no tenant field in its where"))
    return findings
