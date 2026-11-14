# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""PostgreSQL / Supabase row-level-security analysis of SQL migrations.

The migrations are replayed in sorted path order into a small model of the
schema (tables, columns, RLS flags, policies, functions, views, grants,
roles); later statements override earlier ones. The rules then follow the
PostgreSQL semantics: permissive policies are OR-ed and restrictive ones
AND-ed, INSERT is checked by WITH CHECK only, an UPDATE without WITH CHECK
reuses USING, table owners and BYPASSRLS roles skip RLS, and a view runs with
its owner's rights unless ``security_invoker`` is on (PostgreSQL 15+).
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field

from navin.security.analyzers.common import TENANT_RE, SourceFile, SourceSet, line_text
from navin.security.model import Evidence, Finding

ANALYZER_ID = "navin_database"
CHECKED_CONTROLS = frozenset({"C2.09"})

SYSTEM_SCHEMAS = frozenset({
    "pg_catalog", "information_schema", "auth", "storage", "extensions", "realtime", "graphql",
    "graphql_public", "pgsodium", "pgsodium_masks", "vault", "cron", "net", "supabase_functions",
    "supabase_migrations", "pgbouncer", "_realtime", "_analytics", "pg_toast",
})
API_ROLES = frozenset({"anon", "authenticated", "public"})
OWNER_RE = re.compile(r"\b(?:user|owner|author|created_by|profile|member)_?id\b", re.I)
SCOPE_RE = re.compile(r"\bauth\s*\.\s*(?:uid|jwt|role|email)\s*\(|\bcurrent_setting\s*\(|\bcurrent_user\b|\bsession_user\b", re.I)
_BUILTIN_CALLS = frozenset({
    "coalesce", "nullif", "lower", "upper", "exists", "in", "any", "all", "array", "not", "and", "or",
    "now", "length", "trim", "cast", "greatest", "least", "select", "case", "when", "then", "using", "check",
})
_IDENT = r'(?:"(?:[^"]|"")+"|[A-Za-z_][\w$]*)'
QNAME = rf"{_IDENT}(?:\s*\.\s*{_IDENT})?"
_WRITE_PRIVS = frozenset({"insert", "update", "delete", "truncate", "all"})
CLIENT_DIRS = ("src/", "app/", "pages/", "components/", "public/")
PUBLIC_ENV_RE = re.compile(r"\b(?:NEXT_PUBLIC_|VITE_|REACT_APP_|EXPO_PUBLIC_)\w*SERVICE_ROLE\w*", re.I)
SERVICE_ROLE_RE = re.compile(r"\bSUPABASE_SERVICE_ROLE\w*|\bservice_role(?:_key)?\b", re.I)
JWT_RE = re.compile(r"\beyJ[\w-]{8,}\.(eyJ[\w-]{8,})\.[\w-]{8,}")


# ---------------------------------------------------------------------------
# SQL splitting
# ---------------------------------------------------------------------------

@dataclass
class Statement:
    file: str
    line: int
    text: str  # comments blanked out, literals kept
    raw: str

    @property
    def lower(self) -> str:
        return self.text.lower()


def split_sql(text: str) -> list[tuple[int, str, str]]:
    """Split on top-level ``;``; returns (line, cleaned text, raw text).

    Comments (``--`` and nested ``/* */``) are blanked with spaces so offsets
    and lines stay right; single quotes, quoted identifiers and dollar-quoted
    bodies are skipped as a whole.
    """
    out: list[tuple[int, str, str]] = []
    clean: list[str] = []
    n = len(text)
    i = 0
    start = 0
    while i < n:
        ch = text[i]
        if ch == "-" and text.startswith("--", i):
            end = text.find("\n", i)
            end = n if end < 0 else end
            clean.append(" " * (end - i))
            i = end
            continue
        if ch == "/" and text.startswith("/*", i):
            depth, j = 1, i + 2
            while j < n and depth:
                if text.startswith("/*", j):
                    depth, j = depth + 1, j + 2
                elif text.startswith("*/", j):
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            clean.append(re.sub(r"[^\n]", " ", text[i:j]))
            i = j
            continue
        if ch in "'\"":
            j = i + 1
            while j < n:
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            clean.append(text[i:j + 1])
            i = j + 1
            continue
        if ch == "$":
            m = re.match(r"\$([A-Za-z_]\w*)?\$", text[i:])
            if m and not (i and (text[i - 1].isalnum() or text[i - 1] == "_")):
                tag = m.group(0)
                end = text.find(tag, i + len(tag))
                end = n if end < 0 else end + len(tag)
                clean.append(text[i:end])
                i = end
                continue
        if ch == ";":
            body = "".join(clean)
            if body.strip():
                lead = len(body) - len(body.lstrip())
                line = text.count("\n", 0, start + lead) + 1
                out.append((line, body.strip(), text[start + lead:i].strip()))
            clean = []
            i += 1
            start = i
            continue
        clean.append(ch)
        i += 1
    body = "".join(clean)
    if body.strip():
        lead = len(body) - len(body.lstrip())
        out.append((text.count("\n", 0, start + lead) + 1, body.strip(), text[start + lead:].strip()))
    return out


def ident(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1].replace('""', '"')
    return raw.lower()


def qname(raw: str, default: str = "public") -> tuple[str, str]:
    parts = re.findall(_IDENT, raw)
    if len(parts) >= 2:
        return ident(parts[0]), ident(parts[1])
    return default, ident(parts[0]) if parts else ""


def paren_after(text: str, pos: int) -> tuple[str, int] | None:
    """The balanced ``( ... )`` group starting at or after *pos* (whitespace only before it)."""
    m = re.compile(r"\s*\(").match(text, pos)
    if not m:
        return None
    i = m.end()
    depth = 1
    j = i
    while j < len(text) and depth:
        c = text[j]
        if c in "'\"":
            k = text.find(c, j + 1)
            j = len(text) if k < 0 else k + 1
            continue
        if c == "$":
            dm = re.match(r"\$([A-Za-z_]\w*)?\$", text[j:])
            if dm:
                k = text.find(dm.group(0), j + len(dm.group(0)))
                j = len(text) if k < 0 else k + len(dm.group(0))
                continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        j += 1
    return text[i:j - 1], j


def split_top(text: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for c in text:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Schema model
# ---------------------------------------------------------------------------

@dataclass
class Table:
    schema: str
    name: str
    file: str
    line: int
    excerpt: str
    columns: set[str] = field(default_factory=set)
    rls: bool = False
    force: bool = False
    rls_stmt: Statement | None = None
    disabled: Statement | None = None

    @property
    def tenant_columns(self) -> set[str]:
        return {c for c in self.columns if TENANT_RE.fullmatch(c) or OWNER_RE.fullmatch(c)}


@dataclass
class Policy:
    schema: str
    table: str
    name: str
    command: str
    permissive: bool
    roles: list[str]
    using: str | None
    check: str | None
    stmt: Statement

    def applies_to_api(self) -> bool:
        return not self.roles or any(r in API_ROLES for r in self.roles)

    def commands(self) -> list[str]:
        return ["select", "insert", "update", "delete"] if self.command == "all" else [self.command]


@dataclass
class Function:
    schema: str
    name: str
    stmt: Statement
    definer: bool
    search_path: bool
    body: str
    body_offset: int
    revoked: set[str] = field(default_factory=set)


@dataclass
class View:
    schema: str
    name: str
    stmt: Statement
    invoker: bool
    sources: list[tuple[str, str]]


@dataclass
class Schema:
    tables: dict[tuple[str, str], Table] = field(default_factory=dict)
    policies: dict[tuple[str, str, str], Policy] = field(default_factory=dict)
    functions: dict[tuple[str, str], Function] = field(default_factory=dict)
    views: dict[tuple[str, str], View] = field(default_factory=dict)
    grants: list[tuple[Statement, set[str], list[tuple[str, str]], str | None, set[str]]] = field(default_factory=list)
    roles: list[tuple[Statement, str, str]] = field(default_factory=list)
    supabase: bool = False

    def exposed(self, schema: str) -> bool:
        if schema == "public":
            return True
        return self.supabase and schema not in SYSTEM_SCHEMAS and not schema.startswith("pg_")

    def policies_for(self, key: tuple[str, str]) -> list[Policy]:
        return [p for p in self.policies.values() if (p.schema, p.table) == key]


_CREATE_TABLE = re.compile(rf"^create\s+(?:(?:global\s+|local\s+)?(?:temp|temporary|unlogged)\s+)?table\s+(?:if\s+not\s+exists\s+)?({QNAME})", re.I)
_ALTER_TABLE = re.compile(rf"^alter\s+table\s+(?:if\s+exists\s+)?(?:only\s+)?({QNAME})\s+(.*)$", re.I | re.S)
_CREATE_POLICY = re.compile(rf"^create\s+policy\s+({_IDENT})\s+on\s+({QNAME})(.*)$", re.I | re.S)
_ALTER_POLICY = re.compile(rf"^alter\s+policy\s+({_IDENT})\s+on\s+({QNAME})(.*)$", re.I | re.S)
_DROP_POLICY = re.compile(rf"^drop\s+policy\s+(?:if\s+exists\s+)?({_IDENT})\s+on\s+({QNAME})", re.I)
_DROP_TABLE = re.compile(rf"^drop\s+table\s+(?:if\s+exists\s+)?({QNAME}(?:\s*,\s*{QNAME})*)", re.I)
_CREATE_FUNC = re.compile(rf"^create\s+(?:or\s+replace\s+)?(?:function|procedure)\s+({QNAME})\s*\(", re.I)
_ALTER_FUNC = re.compile(rf"^alter\s+(?:function|procedure)\s+({QNAME})", re.I)
_REVOKE_FUNC = re.compile(rf"^revoke\s+(?:execute|all(?:\s+privileges)?)\s+on\s+(?:function|procedure|routine)\s+({QNAME})(?:\s*\([^)]*\))?\s+from\s+(.+)$", re.I | re.S)
_CREATE_VIEW = re.compile(rf"^create\s+(?:or\s+replace\s+)?(?:temp\s+|temporary\s+)?(?:recursive\s+)?(?:materialized\s+)?view\s+(?:if\s+not\s+exists\s+)?({QNAME})", re.I)
_ALTER_VIEW = re.compile(rf"^alter\s+view\s+(?:if\s+exists\s+)?({QNAME})\s+set\s*\((.*)\)", re.I | re.S)
_GRANT = re.compile(r"^grant\s+(.+?)\s+on\s+(.+?)\s+to\s+(.+?)(?:\s+with\s+grant\s+option)?$", re.I | re.S)
_ROLE = re.compile(rf"^(?:create|alter)\s+(?:role|user)\s+({_IDENT})\b(.*)$", re.I | re.S)


def _bool_opt(options: str, name: str) -> bool | None:
    m = re.search(rf"\b{name}\b\s*(?:=\s*([\w']+))?", options, re.I)
    if not m:
        return None
    val = (m.group(1) or "true").strip("'").lower()
    return val in {"true", "on", "1", "yes"}


def build_schema(files: list[SourceFile]) -> Schema:
    schema = Schema()
    for src in sorted(files, key=lambda s: s.rel):
        if re.search(r"\bauth\s*\.\s*(?:uid|jwt)\s*\(|\bto\s+(?:anon|authenticated)\b|supabase", src.text, re.I) or "supabase/" in src.rel:
            schema.supabase = True
        for line, clean, raw in split_sql(src.text):
            _apply(schema, Statement(src.rel, line, clean, raw))
    return schema


def _apply(schema: Schema, st: Statement) -> None:
    text = st.text
    if m := _CREATE_TABLE.match(text):
        key = qname(m.group(1))
        table = Table(key[0], key[1], st.file, st.line, line_text(st.raw, 1))
        group = paren_after(text, m.end())
        if group:
            for part in split_top(group[0]):
                first = re.match(_IDENT, part)
                if first and not re.match(r"(?:constraint|primary|unique|foreign|check|exclude|like)\b", part, re.I):
                    table.columns.add(ident(first.group(0)))
        schema.tables[key] = table
    elif m := _ALTER_TABLE.match(text):
        key = qname(m.group(1))
        rest = m.group(2)
        table = schema.tables.get(key)
        if table is None:
            table = Table(key[0], key[1], st.file, st.line, line_text(st.raw, 1))
            schema.tables[key] = table
        for part in split_top(rest):
            low = part.lower()
            if re.match(r"enable\s+row\s+level\s+security", low):
                table.rls, table.rls_stmt, table.disabled = True, st, None
            elif re.match(r"disable\s+row\s+level\s+security", low):
                table.rls, table.disabled = False, st
            elif re.match(r"no\s+force\s+row\s+level\s+security", low):
                table.force = False
            elif re.match(r"force\s+row\s+level\s+security", low):
                table.force = True
            elif cm := re.match(rf"add\s+(?:column\s+)?(?:if\s+not\s+exists\s+)?({_IDENT})", part, re.I):
                if cm.group(1).lower() not in {"constraint", "primary", "unique", "foreign", "check"}:
                    table.columns.add(ident(cm.group(1)))
            elif cm := re.match(rf"rename\s+column\s+({_IDENT})\s+to\s+({_IDENT})", part, re.I):
                table.columns.discard(ident(cm.group(1)))
                table.columns.add(ident(cm.group(2)))
    elif m := _DROP_TABLE.match(text):
        for name in re.findall(QNAME, m.group(1)):
            key = qname(name)
            schema.tables.pop(key, None)
            for pk in [k for k in schema.policies if k[:2] == key]:
                del schema.policies[pk]
    elif m := _CREATE_POLICY.match(text):
        tkey = qname(m.group(2))
        rest = m.group(3)
        pol = Policy(tkey[0], tkey[1], ident(m.group(1)), "all", True, [], None, None, st)
        if pm := re.search(r"\bas\s+(permissive|restrictive)\b", rest, re.I):
            pol.permissive = pm.group(1).lower() == "permissive"
        if fm := re.search(r"\bfor\s+(all|select|insert|update|delete)\b", rest, re.I):
            pol.command = fm.group(1).lower()
        _policy_clauses(pol, rest)
        schema.policies[(*tkey, pol.name)] = pol
    elif m := _ALTER_POLICY.match(text):
        tkey = qname(m.group(2))
        pol = schema.policies.get((*tkey, ident(m.group(1))))
        rest = m.group(3)
        if rm := re.search(rf"\brename\s+to\s+({_IDENT})", rest, re.I):
            if pol:
                del schema.policies[(*tkey, pol.name)]
                pol.name = ident(rm.group(1))
                schema.policies[(*tkey, pol.name)] = pol
        elif pol:
            pol.stmt = st
            _policy_clauses(pol, rest, keep=True)
    elif m := _DROP_POLICY.match(text):
        schema.policies.pop((*qname(m.group(2)), ident(m.group(1))), None)
    elif m := _CREATE_FUNC.match(text):
        key = qname(m.group(1))
        body_m = re.search(r"(\$([A-Za-z_]\w*)?\$)(.*?)\1", text, re.S)
        body = body_m.group(3) if body_m else ""
        outside = text.replace(body, " ") if body else text
        low = outside.lower()
        schema.functions[key] = Function(
            key[0], key[1], st, bool(re.search(r"\bsecurity\s+definer\b", low)),
            bool(re.search(r"\bset\s+search_path\b", low)), body, body_m.start(3) if body_m else 0,
        )
    elif m := _ALTER_FUNC.match(text):
        fn = schema.functions.get(qname(m.group(1)))
        if fn:
            low = text.lower()
            if re.search(r"\bset\s+search_path\b", low):
                fn.search_path = True
            if re.search(r"\bsecurity\s+definer\b", low):
                fn.definer = True
            if re.search(r"\bsecurity\s+invoker\b", low):
                fn.definer = False
    elif m := _REVOKE_FUNC.match(text):
        fn = schema.functions.get(qname(m.group(1)))
        if fn:
            fn.revoked |= {ident(r) for r in re.findall(_IDENT, m.group(2))}
    elif m := _CREATE_VIEW.match(text):
        key = qname(m.group(1))
        after = text[m.end():]
        invoker = False
        wm = re.match(r"\s*with\s*", after, re.I)
        if wm:
            group = paren_after(after, wm.end())
            if group:
                invoker = bool(_bool_opt(group[0], "security_invoker"))
        sources = [qname(x) for x in re.findall(rf"\b(?:from|join)\s+(?:only\s+)?({QNAME})", after, re.I)]
        schema.views[key] = View(key[0], key[1], st, invoker, sources)
    elif m := _ALTER_VIEW.match(text):
        view = schema.views.get(qname(m.group(1)))
        val = _bool_opt(m.group(2), "security_invoker")
        if view and val is not None:
            view.invoker = val
    elif m := _GRANT.match(text):
        privs = {p.strip().split()[0].lower() for p in m.group(1).split(",") if p.strip()}
        target = m.group(2).strip()
        roles = {ident(r) for r in re.findall(_IDENT, m.group(3)) if r.lower() != "group"}
        low = target.lower()
        if am := re.match(rf"all\s+tables\s+in\s+schema\s+({_IDENT})", target, re.I):
            schema.grants.append((st, privs, [], ident(am.group(1)), roles))
        elif not re.match(r"(?:function|functions|procedure|routine|sequence|all\s+sequences|all\s+functions|all\s+routines|schema|database|type|domain|language|foreign|large|tablespace|parameter)\b", low):
            tables = [qname(x) for x in re.findall(QNAME, re.sub(r"^table\s+", "", target, flags=re.I))]
            schema.grants.append((st, privs, tables, None, roles))
    elif m := _ROLE.match(text):
        opts = m.group(2).lower()
        for flag in ("bypassrls", "superuser"):
            if re.search(rf"(?<!no){flag}\b", opts):
                schema.roles.append((st, ident(m.group(1)), flag))


def _policy_clauses(pol: Policy, rest: str, keep: bool = False) -> None:
    tm = re.search(r"\bto\s+(.+?)(?=\busing\b|\bwith\s+check\b|$)", rest, re.I | re.S)
    if tm:
        pol.roles = [ident(r) for r in re.findall(_IDENT, tm.group(1))]
    um = re.search(r"\busing\b", rest, re.I)
    if um and (g := paren_after(rest, um.end())):
        pol.using = g[0].strip()
    cm = re.search(r"\bwith\s+check\b", rest, re.I)
    if cm and (g := paren_after(rest, cm.end())):
        pol.check = g[0].strip()


# ---------------------------------------------------------------------------
# Expression helpers
# ---------------------------------------------------------------------------

def is_true(expr: str | None) -> bool:
    if expr is None:
        return False
    e = re.sub(r"[\s()]", "", expr).lower()
    return e in {"true", "1=1", "'t'", "true::boolean"}


def mentions(expr: str | None, columns: set[str]) -> set[str]:
    if not expr:
        return set()
    low = expr.lower()
    return {c for c in columns if re.search(rf'(?<![\w$])"?{re.escape(c.lower())}"?(?![\w$])', low)}


def has_helper_call(expr: str | None) -> bool:
    if not expr:
        return False
    names = re.findall(r"([A-Za-z_][\w$]*(?:\s*\.\s*[A-Za-z_][\w$]*)?)\s*\(", expr)
    return any(n.split(".")[-1].strip().lower() not in _BUILTIN_CALLS for n in names)


def effective(pol: Policy, command: str) -> tuple[str | None, str | None]:
    """(USING, WITH CHECK) as PostgreSQL applies them for *command*."""
    if command == "insert":
        return None, pol.check
    if command in {"select", "delete"}:
        return pol.using, None
    return pol.using, pol.check if pol.check is not None else pol.using


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

def _finding(rule: str, st: Statement, *, title: str, severity: str, cwe: str, family: str = "multitenancy",
             remediation: str, description: str = "", controls: list[str] | None = None,
             confidence: float = 0.7, component: str = "", impact: str = "", line: int | None = None) -> Finding:
    ln = line or st.line
    excerpt = st.raw[:600]
    return Finding(
        id=rule, family=family, title=title, severity=severity, cwe=cwe, file_path=st.file, line=ln,
        component=component, controls=controls or ["C2.09"], confidence=confidence, source=ANALYZER_ID,
        description=description or title, remediation=remediation, impact=impact,
        evidence=[Evidence(kind="static", description=title, tool=ANALYZER_ID, observed=excerpt, location=f"{st.file}:{ln}")],
    )


def _table_stmt(table: Table) -> Statement:
    return Statement(table.file, table.line, table.excerpt, table.excerpt)


def schema_findings(schema: Schema) -> list[Finding]:
    out: list[Finding] = []
    for key, table in schema.tables.items():
        if not schema.exposed(table.schema):
            continue
        fq = f"{key[0]}.{key[1]}"
        if not table.rls:
            st = table.disabled or _table_stmt(table)
            sensitive = bool(table.tenant_columns) or schema.supabase
            why = "row level security is disabled later in the migrations" if table.disabled else "row level security is never enabled"
            out.append(_finding(
                "db-rls-disabled", st, severity="high" if sensitive else "medium", cwe="CWE-284", component=fq,
                title=f"Table {fq} in an exposed schema: {why}",
                description=(f"{fq} is reachable by the API roles and {why}; any role with table privileges reads and writes every row"
                             + (f" (tenant/owner columns: {', '.join(sorted(table.tenant_columns))})." if table.tenant_columns else ".")),
                remediation=f"Run ALTER TABLE {fq} ENABLE ROW LEVEL SECURITY and add policies scoped to the caller (for example auth.uid() = user_id).",
                impact="exfiltration", confidence=0.8,
            ))
            continue
        pols = schema.policies_for(key)
        if not pols:
            out.append(_finding(
                "db-rls-no-policy", table.rls_stmt or _table_stmt(table), severity="low", cwe="CWE-284", component=fq,
                title=f"RLS enabled on {fq} without any policy: deny all for non-owners",
                description=f"No policy exists on {fq}, so every non-owner role (anon, authenticated) sees no row: the table is unreachable through the API. This is safe but often unintended.",
                remediation="If clients must use the table, add explicit policies; otherwise document that only the service role / owner reads it.",
                confidence=0.9,
            ))
            continue
        tenant = table.tenant_columns
        for pol in pols:
            out.extend(_policy_findings(table, pol, tenant))
        for cmd in ("select", "insert", "update", "delete"):
            perm = [p for p in pols if p.permissive and cmd in p.commands() and p.applies_to_api()]
            broad = [p for p in perm if any(is_true(x) for x in effective(p, cmd))]
            if len(perm) >= 2 and broad:
                out.append(_finding(
                    "db-permissive-or", broad[0].stmt, severity="low", cwe="CWE-284", component=fq,
                    title=f"{len(perm)} permissive {cmd.upper()} policies on {fq}, one of them is always true",
                    description=(f"PERMISSIVE policies are OR-ed: policy {broad[0].name!r} evaluates to true, so the narrower ones "
                                 f"({', '.join(repr(p.name) for p in perm if p is not broad[0])}) never restrict {cmd.upper()}."),
                    remediation="Drop the broad policy, or make the narrowing policy AS RESTRICTIVE so it is AND-ed with the others.",
                    confidence=0.8,
                ))
    rls_tables = {k for k, t in schema.tables.items() if t.rls}
    for key, fn in schema.functions.items():
        if not fn.definer:
            continue
        fq = f"{key[0]}.{key[1]}"
        if not fn.search_path:
            out.append(_finding(
                "db-security-definer-search-path", fn.stmt, family="authorization", severity="high", cwe="CWE-426", component=fq,
                title=f"SECURITY DEFINER function {fq} without SET search_path",
                description="The function runs with its owner's rights and resolves unqualified names through the caller's search_path, so a caller who can create objects in a searched schema can hijack it.",
                remediation=f"Add SET search_path = '' (or pg_catalog, public) to {fq} and schema-qualify every object it uses.",
            ))
        if schema.exposed(key[0]) and not fn.revoked & {"public", "anon"}:
            out.append(_finding(
                "db-security-definer-exposed", fn.stmt, family="authorization", severity="medium", cwe="CWE-269", component=fq,
                title=f"SECURITY DEFINER function {fq} is executable by public/anon",
                description="Functions get EXECUTE for PUBLIC by default and an exposed schema publishes them as RPC: anyone calls code that runs with the owner's rights and bypasses RLS.",
                remediation=f"REVOKE EXECUTE ON FUNCTION {fq} FROM public, anon; then GRANT EXECUTE to the roles that need it, or move it to a private schema.",
                confidence=0.6,
            ))
        out.extend(_dynamic_sql(fn))
    for key, view in schema.views.items():
        if not schema.exposed(key[0]) or view.invoker:
            continue
        hit = [s for s in view.sources if s in rls_tables]
        if hit:
            fq = f"{key[0]}.{key[1]}"
            out.append(_finding(
                "db-view-bypasses-rls", view.stmt, severity="high", cwe="CWE-284", component=fq,
                title=f"View {fq} reads RLS table(s) {', '.join(f'{a}.{b}' for a, b in hit)} with the owner's rights",
                description="A view runs with its owner's privileges, and the owner bypasses RLS: every API caller of the view sees all rows of the underlying tables.",
                remediation=f"Recreate it WITH (security_invoker = true) (PostgreSQL 15+), or ALTER VIEW {fq} SET (security_invoker = on); otherwise revoke it from anon/authenticated.",
                impact="exfiltration", confidence=0.8,
            ))
    for st, privs, tables, in_schema, roles in schema.grants:
        writes = privs & _WRITE_PRIVS
        if not writes:
            continue
        targets = [schema.tables.get(t) for t in tables] if in_schema is None else [t for t in schema.tables.values() if t.schema == in_schema]
        what = f"ALL TABLES IN SCHEMA {in_schema}" if in_schema else ", ".join(f"{a}.{b}" for a, b in tables)
        if roles & {"anon", "public"}:
            out.append(_finding(
                "db-grant-broad", st, family="authorization", severity="high", cwe="CWE-732", component=what,
                title=f"GRANT {', '.join(sorted(writes)).upper()} on {what} to {', '.join(sorted(roles & {'anon', 'public'}))}",
                description="Unauthenticated callers get write privileges; only RLS policies (if any) stand between them and the data.",
                remediation="Revoke write privileges from anon/public and grant them to authenticated only where RLS policies scope the rows.",
            ))
        elif "authenticated" in roles and any(t is None or not t.rls for t in targets):
            out.append(_finding(
                "db-grant-broad", st, family="authorization", severity="medium", cwe="CWE-732", component=what,
                title=f"GRANT {', '.join(sorted(writes)).upper()} on {what} to authenticated on a table without RLS",
                description="Every signed-in user can write every row of a table that has no row level security.",
                remediation="Enable row level security on the table and add owner/tenant scoped policies, or narrow the grant.",
                confidence=0.6,
            ))
    for st, role, flag in schema.roles:
        out.append(_finding(
            "db-bypassrls-role", st, family="authorization", severity="medium", cwe="CWE-250", component=role,
            title=f"Role {role} is created with {flag.upper()} in a migration",
            description=f"{flag.upper()} roles skip every row level security policy; a leaked credential of {role} reads all tenants.",
            remediation=f"Remove {flag.upper()} from {role}; give it only the table privileges it needs and let RLS apply.",
        ))
    return out


def _policy_findings(table: Table, pol: Policy, tenant: set[str]) -> list[Finding]:
    out: list[Finding] = []
    fq = f"{table.schema}.{table.name}"
    broad_true = False
    if pol.permissive and pol.applies_to_api():
        for cmd in pol.commands():
            using, check = effective(pol, cmd)
            if is_true(using) or is_true(check):
                broad_true = True
        if broad_true:
            write = pol.command != "select"
            out.append(_finding(
                "db-policy-permissive-true", pol.stmt, severity="high" if write else "medium", cwe="CWE-284", component=fq,
                title=f"Policy {pol.name!r} on {fq} ({pol.command.upper()}) is always true for {', '.join(pol.roles) or 'public'}",
                description="USING (true) / WITH CHECK (true) on a permissive policy opens every row to these roles: RLS is enabled but does nothing for this command.",
                remediation="Replace true with a caller-scoped predicate such as auth.uid() = user_id, or restrict the TO clause to the roles that really need it.",
                impact="exfiltration" if not write else "fraud", confidence=0.85,
            ))
    if pol.command in {"update", "all"}:
        using, check = effective(pol, "update")
        scoped = mentions(using, tenant) if tenant else set()
        if scoped and not mentions(check, scoped):
            out.append(_finding(
                "db-policy-tenant-change", pol.stmt, severity="high", cwe="CWE-639", component=fq,
                title=f"UPDATE policy {pol.name!r} on {fq} checks {', '.join(sorted(scoped))} in USING but not in WITH CHECK",
                description="USING filters the rows a user may target, WITH CHECK validates the new row; when WITH CHECK ignores the owner/tenant column a user can move a row to another tenant or owner.",
                remediation=f"Make WITH CHECK constrain the same column(s) as USING ({', '.join(sorted(scoped))}), or omit WITH CHECK so PostgreSQL reuses USING.",
                impact="fraud", confidence=0.8,
            ))
    if tenant and not broad_true:
        exprs = [pol.using, pol.check]
        if not any(mentions(e, tenant) or (e and SCOPE_RE.search(e)) or has_helper_call(e) for e in exprs):
            out.append(_finding(
                "db-policy-no-tenant-scope", pol.stmt, severity="medium", cwe="CWE-284", component=fq,
                title=f"Policy {pol.name!r} on {fq} does not reference {', '.join(sorted(tenant))}, auth.uid() or a helper (needs review)",
                description="The table carries tenant/owner columns but this policy predicate uses none of them nor the caller identity, so it probably does not isolate tenants.",
                remediation="Scope the predicate to the caller, for example user_id = auth.uid() or tenant_id = (auth.jwt() ->> 'tenant_id')::uuid.",
                confidence=0.3,
            ))
    return out


def _dynamic_sql(fn: Function) -> list[Finding]:
    out: list[Finding] = []
    for m in re.finditer(r"\bexecute\b(?!\s+(?:function|procedure)\b)([^;]*)", fn.body, re.I):
        expr = m.group(1)
        if "||" not in expr or re.search(r"\bformat\s*\(|\bquote_(?:ident|literal|nullable)\s*\(|\busing\b", expr, re.I):
            continue
        line = fn.stmt.line + fn.stmt.text.count("\n", 0, fn.body_offset + m.start())
        out.append(_finding(
            "db-dynamic-sql", fn.stmt, family="injection", severity="medium", cwe="CWE-89", controls=["C1.01"],
            component=f"{fn.schema}.{fn.name}", line=line,
            title=f"Dynamic SQL built by concatenation in {fn.schema}.{fn.name}",
            description=f"EXECUTE runs a string assembled with ||: {expr.strip()[:200]}",
            remediation="Build the statement with format() using %I for identifiers and %L for literals, or pass values with EXECUTE ... USING.",
            confidence=0.6,
        ))
    return out


def _jwt_role(token: str) -> str:
    try:
        payload = json.loads(base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)))
    except (ValueError, UnicodeDecodeError):
        return ""
    return str(payload.get("role", "")) if isinstance(payload, dict) else ""


def client_findings(scripts: list[SourceFile]) -> list[Finding]:
    out: list[Finding] = []
    for src in scripts:
        rel = src.rel.replace("\\", "/")
        client = (any(rel.startswith(d) or f"/{d}" in rel for d in CLIENT_DIRS)
                  and not re.search(r"(?:^|/)(?:api|server|functions|supabase/functions)/|\.server\.", rel))
        for lineno, text in enumerate(src.text.splitlines(), 1):
            hit = ""
            if m := PUBLIC_ENV_RE.search(text):
                hit = f"public env variable {m.group(0)} exposes the service role key to the browser bundle"
            elif client and (m := SERVICE_ROLE_RE.search(text)):
                hit = f"{m.group(0)} referenced in client-side code"
            elif client:
                for jm in JWT_RE.finditer(text):
                    if _jwt_role(jm.group(1)) == "service_role":
                        hit = "a Supabase service_role JWT is embedded in client-side code"
                        break
            if hit:
                st = Statement(src.rel, lineno, text, JWT_RE.sub("eyJ***", text.strip())[:300])
                out.append(_finding(
                    "db-service-role-client", st, family="secrets", severity="critical", cwe="CWE-798",
                    controls=["C1.10", "C2.09"], title=f"Supabase service role key in client code: {hit}",
                    description="The service_role key bypasses every RLS policy; shipped to a browser or app bundle, any user owns the whole database.",
                    remediation="Rotate the service_role key now, keep it in server-only environment variables, and use the anon key plus RLS in the client.",
                    impact="compromise", confidence=0.85,
                ))
                break
    return out


def analyze(sources: SourceSet) -> list[Finding]:
    findings: list[Finding] = []
    if sources.sql:
        findings.extend(schema_findings(build_schema(sources.sql)))
    else:
        sources.notes.append("no SQL migration found: PostgreSQL row level security was not checked")
    findings.extend(client_findings(sources.scripts))
    return findings
