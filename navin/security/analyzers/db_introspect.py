# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Catalog introspection of PostgreSQL row level security.

``introspect_ephemeral`` replays the project's migrations into a throwaway
local cluster (unix socket in a private temp dir, no TCP) with Supabase
stubs, then reads the catalogs; ``introspect_url`` reads the catalogs of a
database the user provides, read-only. Both turn what the catalog says into
findings carrying the static rule ids and dynamic evidence, so the correlator
confirms the static findings. Every function blocks: run them off the event
loop. Every subprocess has a timeout.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.security.analyzers import database as static_db
from navin.security.analyzers.common import TENANT_RE, SourceFile
from navin.security.model import Evidence, Finding

EPHEMERAL_ENGINE = "navin_db_ephemeral"
LIVE_ENGINE = "navin_db_live"
_SUPERUSER = "navin"

SUPABASE_STUBS = """
DO $navin$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
END $navin$;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS storage;
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE TABLE IF NOT EXISTS auth.users (id uuid PRIMARY KEY, email text, raw_user_meta_data jsonb, created_at timestamptz DEFAULT now());
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS
  $$ SELECT coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(auth.jwt() ->> 'sub', '')::uuid $$;
CREATE OR REPLACE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS
  $$ SELECT coalesce(auth.jwt() ->> 'role', 'anon') $$;
GRANT USAGE ON SCHEMA public, auth TO anon, authenticated, service_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA auth TO anon, authenticated, service_role;
"""
MIGRATOR = "navin_migrator"
MIGRATOR_SETUP = f"""
CREATE ROLE {MIGRATOR} LOGIN CREATEROLE NOSUPERUSER NOBYPASSRLS NOREPLICATION;
ALTER SCHEMA public OWNER TO {MIGRATOR};
GRANT ALL ON SCHEMA auth, storage, extensions TO {MIGRATOR};
GRANT CREATE ON DATABASE postgres TO {MIGRATOR};
ALTER TABLE auth.users OWNER TO {MIGRATOR};
"""

_SYSTEM_FILTER = "n.nspname NOT IN ('pg_catalog','information_schema','auth','storage','extensions','pg_toast') AND n.nspname NOT LIKE 'pg\\_%'"
QUERIES: dict[str, str] = {
    "version": "SELECT json_build_object('v', current_setting('server_version'))",
    "tables": f"""SELECT coalesce(json_agg(t), '[]') FROM (
        SELECT n.nspname AS schema, c.relname AS name, c.relrowsecurity AS rls, c.relforcerowsecurity AS force,
               (SELECT coalesce(json_agg(a.attname), '[]') FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped) AS columns,
               (SELECT coalesce(json_agg(json_build_object('role', r, 'privilege', p)), '[]')
                  FROM unnest(ARRAY['anon','authenticated']) r, unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE','TRUNCATE']) p
                 WHERE EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) AND has_table_privilege(r, c.oid, p)) AS grants
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind IN ('r','p') AND {_SYSTEM_FILTER} ORDER BY 1, 2) t""",
    "policies": """SELECT coalesce(json_agg(p), '[]') FROM (
        SELECT schemaname AS schema, tablename AS table, policyname AS name, permissive, roles::text[] AS roles, cmd, qual, with_check
          FROM pg_policies WHERE schemaname NOT IN ('auth','storage') ORDER BY 1, 2, 3) p""",
    "functions": f"""SELECT coalesce(json_agg(f), '[]') FROM (
        SELECT n.nspname AS schema, p.proname AS name, p.proconfig AS config,
               has_function_privilege('public', p.oid, 'EXECUTE') AS public_exec,
               (EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') AND has_function_privilege('anon', p.oid, 'EXECUTE')) AS anon_exec
          FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
         WHERE p.prosecdef AND {_SYSTEM_FILTER} ORDER BY 1, 2) f""",
    "views": f"""SELECT coalesce(json_agg(v), '[]') FROM (
        SELECT n.nspname AS schema, c.relname AS name, c.reloptions AS options,
               (SELECT coalesce(json_agg(DISTINCT jsonb_build_object('schema', sn.nspname, 'name', s.relname)), '[]')
                  FROM pg_depend d JOIN pg_rewrite rw ON rw.oid = d.objid
                  JOIN pg_class s ON s.oid = d.refobjid JOIN pg_namespace sn ON sn.oid = s.relnamespace
                 WHERE rw.ev_class = c.oid AND s.oid <> c.oid AND s.relrowsecurity) AS rls_sources
          FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE c.relkind = 'v' AND {_SYSTEM_FILTER} ORDER BY 1, 2) v""",
    "roles": """SELECT coalesce(json_agg(r), '[]') FROM (
        SELECT rolname AS name, rolbypassrls AS bypassrls, rolsuper AS superuser FROM pg_roles
         WHERE (rolbypassrls OR rolsuper) AND rolname NOT LIKE 'pg\\_%' AND rolname <> current_user
           AND rolname NOT IN ('service_role','postgres','supabase_admin','rds_superuser','cloudsqlsuperuser') ORDER BY 1) r""",
}


@dataclass
class PostgresBinaries:
    initdb: str
    pg_ctl: str
    psql: str
    postgres: str
    version: int


@dataclass
class IntrospectionReport:
    engine: str
    status: str
    postgres_version: str = ""
    applied: list[str] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    policies: list[dict[str, Any]] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    duration_s: float = 0.0
    detail: str = ""
    role_tests: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [f.to_dict() for f in self.findings]
        return data


def _version_of(binary: str) -> int:
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("postgres version probe failed for {}: {}", binary, exc)
        return 0
    m = re.search(r"(\d+)(?:\.\d+)?", out)
    return int(m.group(1)) if m else 0


def find_postgres_binaries() -> PostgresBinaries | None:
    """initdb / pg_ctl / psql / postgres from ``NAVIN_PG_BIN``, PATH, else the highest /usr/lib/postgresql/*/bin."""
    names = ("initdb", "pg_ctl", "psql", "postgres")
    pinned = os.environ.get("NAVIN_PG_BIN", "").strip()
    if pinned and all((Path(pinned) / n).is_file() for n in names):
        return PostgresBinaries(*(str(Path(pinned) / n) for n in names), version=_version_of(str(Path(pinned) / "postgres")))
    found = {n: shutil.which(n) for n in names}
    if all(found.values()):
        return PostgresBinaries(*(str(found[n]) for n in names), version=_version_of(str(found["postgres"])))
    candidates = []
    for bindir in Path("/usr/lib/postgresql").glob("*/bin"):
        if bindir.parent.name.isdigit() and all((bindir / n).is_file() for n in names):
            candidates.append((int(bindir.parent.name), bindir))
    if not candidates:
        return None
    version, bindir = max(candidates)
    return PostgresBinaries(*(str(bindir / n) for n in names), version=version)


def _run(cmd: list[str], *, timeout: float, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=max(1.0, timeout), env=env, stdin=subprocess.DEVNULL)


def _first_error(stderr: str) -> str:
    for line in stderr.splitlines():
        if "ERROR" in line or "FATAL" in line or "error" in line.lower():
            return line.strip()[:400]
    return stderr.strip().splitlines()[0][:400] if stderr.strip() else "unknown error"


_FOREIGN_DIALECTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MySQL/MariaDB", re.compile(r"(?i)\bENGINE\s*=\s*\w+|\bAUTO_INCREMENT\b|`\w+`|\bIDENTIFIED\s+BY\b|@\s*'%'")),
    ("SQL Server", re.compile(r"(?im)^\s*GO\s*$|\bNVARCHAR\s*\(|\bIDENTITY\s*\(\s*\d|\[dbo\]\.|\bsp_configure\b")),
    ("Oracle", re.compile(r"(?i)\bVARCHAR2\s*\(|\bFROM\s+DUAL\b|\bAUTHID\s+DEFINER\b")),
)


def _foreign_dialect(sql: str) -> str:
    """The other SQL dialect a script is written in, or "" when it can be PostgreSQL."""
    return next((name for name, pattern in _FOREIGN_DIALECTS if pattern.search(sql)), "")


def _tolerant_extensions(sql: str) -> str:
    """CREATE EXTENSION becomes a block that logs and continues when the extension is missing."""
    def repl(m: re.Match[str]) -> str:
        stmt = m.group(0).rstrip(";").replace("'", "''")
        return (f"DO $navin_ext$ BEGIN EXECUTE '{stmt}'; EXCEPTION WHEN others THEN "
                f"RAISE NOTICE 'navin: extension skipped: %', SQLERRM; END $navin_ext$;")
    return re.sub(r"(?im)^\s*create\s+extension\b[^;]*;", repl, sql)


def redact_url(url: str) -> str:
    out = re.sub(r"(://[^:/@\s]*:)[^@\s]*@", r"\1***@", url)
    return re.sub(r"(?i)(password=)[^&\s]*", r"\1***", out)


class _Reader:
    def __init__(self, psql: str, args: list[str], env: dict[str, str], deadline: float, wrap_readonly: bool):
        self.psql, self.args, self.env, self.deadline, self.wrap = psql, args, env, deadline, wrap_readonly

    def query(self, sql: str) -> Any:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError("introspection timed out")
        cmd = [self.psql, *self.args, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"]
        if self.wrap:
            cmd += ["-c", "BEGIN READ ONLY", "-c", sql, "-c", "ROLLBACK"]
        else:
            cmd += ["-c", sql]
        proc = _run(cmd, timeout=left, env=self.env)
        if proc.returncode != 0:
            raise RuntimeError(_first_error(proc.stderr))
        # json_agg separates its elements with newlines: the payload is every line but psql's own tags.
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip() not in {"", "BEGIN", "ROLLBACK"}]
        return json.loads("\n".join(lines)) if lines else None


def _read_catalog(reader: _Reader, report: IntrospectionReport, locate: dict[str, tuple[str, int]]) -> None:
    report.postgres_version = str((reader.query(QUERIES["version"]) or {}).get("v", ""))
    report.tables = reader.query(QUERIES["tables"]) or []
    report.policies = reader.query(QUERIES["policies"]) or []
    functions = reader.query(QUERIES["functions"]) or []
    views = reader.query(QUERIES["views"]) or []
    roles = reader.query(QUERIES["roles"]) or []
    report.findings = catalog_findings(report.engine, report.tables, report.policies, functions, views, roles, locate)


def _loc(locate: dict[str, tuple[str, int]], key: str) -> tuple[str, int | None]:
    file, line = locate.get(key, ("", 0))
    return file, (line or None)


def _component(key: str) -> str:
    """The object a catalog finding is about, named like the static analyzer names it (a policy is its table)."""
    kind, _, name = key.partition(":")
    if kind == "policy":
        return name.rsplit(".", 1)[0]
    return name


def _f(engine: str, rule: str, locate: dict[str, tuple[str, int]], key: str, *, family: str, severity: str, cwe: str,
       title: str, observed: str, remediation: str, controls: list[str] | None = None) -> Finding:
    file, line = _loc(locate, key)
    return Finding(
        id=rule, family=family, severity=severity, cwe=cwe, title=title, file_path=file, line=line, component=_component(key),
        controls=controls or ["C2.09"], source=engine, confidence=0.9, remediation=remediation, description=title,
        evidence=[Evidence(kind="dynamic", description=f"catalog: {title}", tool=engine, observed=observed[:600], location=f"{file}:{line}" if file else "")],
    )


def catalog_findings(engine: str, tables: list[dict], policies: list[dict], functions: list[dict], views: list[dict],
                     roles: list[dict], locate: dict[str, tuple[str, int]] | None = None) -> list[Finding]:
    locate = locate or {}
    out: list[Finding] = []
    supabase = any(p.get("qual") and "auth." in str(p.get("qual")) or "auth." in str(p.get("with_check") or "") for p in policies)
    for t in tables:
        fq = f"{t['schema']}.{t['name']}"
        cols = set(t.get("columns") or [])
        tenant = {c for c in cols if TENANT_RE.fullmatch(c) or static_db.OWNER_RE.fullmatch(c)}
        if not t.get("rls") and (t["schema"] == "public" or supabase):
            out.append(_f(engine, "db-rls-disabled", locate, f"table:{fq}", family="multitenancy",
                          severity="high" if tenant or supabase else "medium", cwe="CWE-284",
                          title=f"Table {fq} in an exposed schema: row level security is never enabled",
                          observed=f"pg_class {fq}: relrowsecurity=false relforcerowsecurity={t.get('force')}",
                          remediation=f"ALTER TABLE {fq} ENABLE ROW LEVEL SECURITY and add caller-scoped policies."))
        writes = sorted({g["privilege"] for g in t.get("grants") or [] if g["role"] == "anon" and g["privilege"] != "SELECT"})
        auth_writes = sorted({g["privilege"] for g in t.get("grants") or [] if g["role"] == "authenticated" and g["privilege"] != "SELECT"})
        if writes:
            out.append(_f(engine, "db-grant-broad", locate, f"grant:{fq}", family="authorization", severity="high", cwe="CWE-732",
                          title=f"anon holds {', '.join(writes)} on {fq}",
                          observed=f"has_table_privilege('anon', '{fq}', ...) true for {', '.join(writes)}",
                          remediation="Revoke write privileges from anon/public."))
        elif auth_writes and not t.get("rls"):
            out.append(_f(engine, "db-grant-broad", locate, f"grant:{fq}", family="authorization", severity="medium", cwe="CWE-732",
                          title=f"authenticated holds {', '.join(auth_writes)} on {fq} without RLS",
                          observed=f"has_table_privilege('authenticated', '{fq}', ...) true for {', '.join(auth_writes)}; relrowsecurity=false",
                          remediation="Enable row level security or narrow the grant."))
    for p in policies:
        roles_p = [r for r in (p.get("roles") or [])]
        api = not roles_p or any(r in static_db.API_ROLES for r in roles_p)
        cmd = str(p.get("cmd") or "ALL").lower()
        using, check = p.get("qual"), p.get("with_check")
        if cmd == "update" and check is None:
            check = using
        if p.get("permissive") == "PERMISSIVE" and api and (static_db.is_true(using) or static_db.is_true(check)):
            fq = f"{p['schema']}.{p['table']}"
            out.append(_f(engine, "db-policy-permissive-true", locate, f"policy:{fq}.{p['name']}", family="multitenancy",
                          severity="medium" if cmd == "select" else "high", cwe="CWE-284",
                          title=f"Policy {p['name']!r} on {fq} ({cmd.upper()}) is always true for {', '.join(roles_p)}",
                          observed=f"pg_policies: cmd={p.get('cmd')} roles={roles_p} qual={p.get('qual')} with_check={p.get('with_check')}",
                          remediation="Replace true with a caller-scoped predicate."))
    for fn in functions:
        fq = f"{fn['schema']}.{fn['name']}"
        config = fn.get("config") or []
        if not any(str(c).startswith("search_path=") for c in config):
            out.append(_f(engine, "db-security-definer-search-path", locate, f"function:{fq}", family="authorization", severity="high",
                          cwe="CWE-426", title=f"SECURITY DEFINER function {fq} without SET search_path",
                          observed=f"pg_proc {fq}: prosecdef=true proconfig={config}", remediation="ALTER FUNCTION ... SET search_path = ''."))
        if (fn.get("public_exec") or fn.get("anon_exec")) and (fn["schema"] == "public" or supabase):
            out.append(_f(engine, "db-security-definer-exposed", locate, f"function:{fq}", family="authorization", severity="medium",
                          cwe="CWE-269", title=f"SECURITY DEFINER function {fq} is executable by public/anon",
                          observed=f"has_function_privilege: public={fn.get('public_exec')} anon={fn.get('anon_exec')}",
                          remediation=f"REVOKE EXECUTE ON FUNCTION {fq} FROM public, anon."))
    for v in views:
        options = [str(o).lower() for o in v.get("options") or []]
        invoker = any(o in {"security_invoker=true", "security_invoker=on", "security_invoker=1"} for o in options)
        sources = v.get("rls_sources") or []
        if sources and not invoker and (v["schema"] == "public" or supabase):
            fq = f"{v['schema']}.{v['name']}"
            out.append(_f(engine, "db-view-bypasses-rls", locate, f"view:{fq}", family="multitenancy", severity="high", cwe="CWE-284",
                          title=f"View {fq} reads RLS table(s) {', '.join(s['schema'] + '.' + s['name'] for s in sources)} with the owner's rights",
                          observed=f"pg_class {fq}: reloptions={v.get('options')}", remediation="ALTER VIEW ... SET (security_invoker = on)."))
    for r in roles:
        flag = "SUPERUSER" if r.get("superuser") else "BYPASSRLS"
        out.append(_f(engine, "db-bypassrls-role", locate, f"role:{r['name']}", family="authorization", severity="medium", cwe="CWE-250",
                      title=f"Role {r['name']} has {flag}", observed=f"pg_roles {r['name']}: rolbypassrls={r.get('bypassrls')} rolsuper={r.get('superuser')}",
                      remediation=f"Remove {flag} from {r['name']}."))
    return out


# ---------------------------------------------------------------------------
# Row-level access tests with synthetic users (ephemeral cluster only)
# ---------------------------------------------------------------------------

USER_A = "00000000-0000-4000-8000-00000000000a"
USER_B = "00000000-0000-4000-8000-00000000000b"
_OWNER_IN_POLICY = (
    re.compile(r"auth\.uid\(\)(?:\s+AS\s+\w+)?\s*\)*\s*=\s*\(*\s*\"?([A-Za-z_][A-Za-z0-9_]*)\"?"),
    re.compile(r"\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s*\)*\s*=\s*\(*\s*(?:SELECT\s+)?auth\.uid\(\)", re.I),
)
_COLUMNS = """SELECT coalesce(json_agg(c ORDER BY c.attnum), '[]') FROM (
    SELECT a.attnum, a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type, t.typname, t.typcategory AS category,
           a.attnotnull AS notnull, (a.atthasdef OR a.attidentity <> '' OR a.attgenerated <> '') AS has_default,
           (SELECT e.enumlabel FROM pg_enum e WHERE e.enumtypid = a.atttypid ORDER BY e.enumsortorder LIMIT 1) AS first_label
      FROM pg_attribute a JOIN pg_type t ON t.oid = a.atttypid
     WHERE a.attrelid = {rel}::regclass AND a.attnum > 0 AND NOT a.attisdropped) c"""
_ROLE_TEST_FINDINGS = {
    "anon_read": ("db-rls-anon-read", "medium", "CWE-284", "the anonymous role reads {n} private row(s) of {fq}",
                  "Scope the SELECT policies to authenticated callers (TO authenticated) and to the caller's own rows."),
    "other_read": ("db-rls-cross-tenant-read", "high", "CWE-639", "user A reads {n} row(s) of {fq} owned by user B",
                   "Restrict the SELECT policy with USING (auth.uid() = {owner})."),
    "other_insert": ("db-rls-cross-tenant-insert", "high", "CWE-639", "user A creates a row of {fq} owned by user B",
                     "Add WITH CHECK (auth.uid() = {owner}) to the INSERT policy."),
    "other_update": ("db-rls-cross-tenant-update", "high", "CWE-639", "user A modifies {n} row(s) of {fq} owned by user B",
                     "Restrict the UPDATE policy with USING (auth.uid() = {owner})."),
    "tenant_change": ("db-policy-tenant-change", "high", "CWE-639", "user A moves its own row of {fq} to user B",
                      "Add WITH CHECK (auth.uid() = {owner}) to the UPDATE policy so a row cannot change owner."),
    "other_delete": ("db-rls-cross-tenant-delete", "high", "CWE-639", "user A deletes {n} row(s) of {fq} owned by user B",
                     "Restrict the DELETE policy with USING (auth.uid() = {owner})."),
}
ROLE_TEST_IDS: tuple[str, ...] = tuple(sorted({v[0] for v in _ROLE_TEST_FINDINGS.values()}))


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _owner_column(policies: list[dict[str, Any]], columns: set[str]) -> str:
    for p in policies:
        for expr in (p.get("qual"), p.get("with_check")):
            for pattern in _OWNER_IN_POLICY:
                for m in pattern.finditer(str(expr or "")):
                    if m.group(1) in columns:
                        return m.group(1)
    return ""


def _value(col: dict[str, Any], n: int) -> str | None:
    """A synthetic SQL value for a required column, or None when Navin cannot invent one."""
    name, category = str(col.get("typname") or ""), str(col.get("category") or "")
    if name == "uuid":
        return "gen_random_uuid()"
    if name in {"json", "jsonb"}:
        return "'{}'"
    if category == "S":
        return f"'navin-test-{n}'"
    if category == "N":
        return str(n)
    if category == "B":
        return "false"
    if category == "D":
        return "now()"
    if category == "A":
        return "'{}'"
    if category == "E" and col.get("first_label") is not None:
        return _literal(str(col["first_label"]))
    return None


def _role_test_sql(fq: str, owner: str, owner_type: str, required: list[tuple[str, str]]) -> str:
    cols = [_ident(owner), *(_ident(c) for c, _ in required)]

    def row(user: str, n: int) -> str:
        values = [f"{_literal(user)}::{owner_type}"] + [v.replace("navin-test-", f"navin-test-{n}-") for _, v in required]
        return "(" + ", ".join(values) + ")"

    o, a, b = _ident(owner), f"{_literal(USER_A)}::{owner_type}", f"{_literal(USER_B)}::{owner_type}"
    claims_a = _literal(json.dumps({"sub": USER_A, "role": "authenticated"}))
    insert = f"INSERT INTO {fq} ({', '.join(cols)}) VALUES"
    return "\n".join([
        "BEGIN;",
        "SET LOCAL statement_timeout = '5s';",
        f"INSERT INTO auth.users (id) VALUES ({_literal(USER_A)}), ({_literal(USER_B)}) ON CONFLICT DO NOTHING;",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {fq} TO anon, authenticated;",
        f"{insert} {row(USER_A, 1)}, {row(USER_B, 2)};",
        f"SELECT 'NAVIN seeded ' || count(*) FROM {fq} WHERE {o} IN ({a}, {b});",
        "SET LOCAL ROLE anon;",
        "SELECT set_config('request.jwt.claims', '{\"role\":\"anon\"}', true) IS NULL;",
        f"SELECT 'NAVIN anon_read ' || count(*) FROM {fq} WHERE {o} IN ({a}, {b});",
        "RESET ROLE;",
        "SET LOCAL ROLE authenticated;",
        f"SELECT set_config('request.jwt.claims', {claims_a}, true) IS NULL;",
        f"SELECT 'NAVIN own_read ' || count(*) FROM {fq} WHERE {o} = {a};",
        f"SELECT 'NAVIN other_read ' || count(*) FROM {fq} WHERE {o} = {b};",
        f"{insert} {row(USER_B, 3)} RETURNING 'NAVIN other_insert 1';",
        f"WITH u AS (UPDATE {fq} SET {o} = {o} WHERE {o} = {b} RETURNING 1) SELECT 'NAVIN other_update ' || count(*) FROM u;",
        f"WITH d AS (DELETE FROM {fq} WHERE {o} = {b} RETURNING 1) SELECT 'NAVIN other_delete ' || count(*) FROM d;",
        f"WITH u AS (UPDATE {fq} SET {o} = {b} WHERE {o} = {a} RETURNING 1) SELECT 'NAVIN tenant_change ' || count(*) FROM u;",
        "ROLLBACK;",
    ]) + "\n"


def _role_tests(psql: str, reader: _Reader, mig_conn: list[str], env: dict[str, str], deadline: float,
                report: IntrospectionReport, locate: dict[str, tuple[str, int]]) -> None:
    """Run SELECT / INSERT / UPDATE / DELETE as anon and as two synthetic users on each RLS table.

    Everything runs in a transaction that is rolled back, as the non-superuser migration role (which owns the
    tables and switches to anon / authenticated), with Supabase's default table privileges granted for the
    test only. A table is tested when one of its policies compares a column with auth.uid().
    """
    by_table: dict[str, list[dict[str, Any]]] = {}
    for p in report.policies:
        by_table.setdefault(f"{p['schema']}.{p['table']}", []).append(p)
    for t in report.tables:
        if not t.get("rls"):
            continue
        name = f"{t['schema']}.{t['name']}"
        entry: dict[str, Any] = {"table": name, "tested": False}
        report.role_tests.append(entry)
        if not by_table.get(name):
            entry["skipped"] = "no policy: every non-owner is refused"
            continue
        fq = f"{_ident(t['schema'])}.{_ident(t['name'])}"
        try:
            columns = reader.query(_COLUMNS.format(rel=_literal(fq))) or []
        except (RuntimeError, TimeoutError, ValueError) as exc:
            entry["skipped"] = f"columns not read: {exc}"[:200]
            continue
        owner = _owner_column(by_table.get(name, []), {c["name"] for c in columns})
        if not owner:
            entry["skipped"] = "no policy compares a column with auth.uid(): tenant membership is not simulated"
            continue
        owner_col = next(c for c in columns if c["name"] == owner)
        required: list[tuple[str, str]] = []
        unknown = ""
        for n, col in enumerate(columns):
            if col["name"] == owner or not col.get("notnull") or col.get("has_default"):
                continue
            value = _value(col, n)
            if value is None:
                unknown = f"{col['name']} ({col['type']})"
                break
            required.append((col["name"], value))
        if unknown:
            entry["skipped"] = f"cannot invent a value for the required column {unknown}"
            continue
        left = deadline - time.monotonic()
        if left < 3:
            entry["skipped"] = "out of time"
            continue
        script = _role_test_sql(fq, owner, str(owner_col["type"]), required)
        # Read from stdin so psql splits the statements and ON_ERROR_ROLLBACK keeps a refused one from aborting the rest.
        proc = subprocess.run([psql, *mig_conn, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_ROLLBACK=on"], input=script,
                              capture_output=True, text=True, timeout=max(1.0, min(30.0, left)), env=env)
        results: dict[str, int] = {}
        for line in proc.stdout.splitlines():
            parts = line.strip().split(" ")
            if len(parts) == 3 and parts[0] == "NAVIN" and parts[2].isdigit():
                results[parts[1]] = int(parts[2])
        entry.update({"owner_column": owner, "results": results})
        if results.get("seeded", 0) < 2:
            entry["skipped"] = f"synthetic rows not created: {_first_error(proc.stderr)}"
            continue
        entry["tested"] = True
        policy_key = next((f"policy:{name}.{p['name']}" for p in by_table.get(name, []) if str(p.get("cmd")).upper() in {"UPDATE", "ALL"}),
                          f"table:{name}")
        for check, (rule, severity, cwe, what, fix) in _ROLE_TEST_FINDINGS.items():
            n = results.get(check, 0)
            if n <= 0:
                continue
            key = policy_key if check == "tenant_change" else f"table:{name}"
            file, line = _loc(locate, key)
            title = what.format(n=n, fq=name)
            report.findings.append(Finding(
                id=rule, family="multitenancy", severity=severity, cwe=cwe, title=f"Row-level security bypass: {title}",
                file_path=file, line=line, component=name, controls=["C2.09"], source=report.engine, confidence=0.95,
                impact="escalation" if check in {"other_insert", "other_update", "tenant_change", "other_delete"} else "exfiltration",
                remediation=fix.format(owner=owner), description=title,
                evidence=[Evidence(kind="reproduction", tool=report.engine, location=f"{file}:{line}" if file else "",
                                   description=f"synthetic users {USER_A[-1]} and {USER_B[-1]} in a throwaway PostgreSQL, rolled back",
                                   request=f"SET ROLE {'anon' if check == 'anon_read' else 'authenticated'}; {check} on {name}",
                                   observed=json.dumps(results, sort_keys=True))],
            ))


def locate_objects(sql_files: list[Path], root: Path | None = None) -> dict[str, tuple[str, int]]:
    """Map catalog objects to the file:line the static analyzer reports, so findings merge."""
    sources = []
    for path in sql_files:
        rel = path.as_posix()
        if root is not None:
            try:
                rel = path.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                rel = path.as_posix()
        try:
            sources.append(SourceFile(rel, path.read_text(encoding="utf-8", errors="replace")))
        except OSError as exc:
            logger.warning("db introspection: cannot read {}: {}", path, exc)
    schema = static_db.build_schema(sources)
    out: dict[str, tuple[str, int]] = {}
    for (s, n), t in schema.tables.items():
        st = t.disabled
        out[f"table:{s}.{n}"] = (st.file, st.line) if st else (t.file, t.line)
    for (s, n, p), pol in schema.policies.items():
        out[f"policy:{s}.{n}.{p}"] = (pol.stmt.file, pol.stmt.line)
    for (s, n), fn in schema.functions.items():
        out[f"function:{s}.{n}"] = (fn.stmt.file, fn.stmt.line)
    for (s, n), v in schema.views.items():
        out[f"view:{s}.{n}"] = (v.stmt.file, v.stmt.line)
    for st, privs, tables, in_schema, _roles in schema.grants:
        if privs & {"insert", "update", "delete", "truncate", "all"}:
            targets = tables if in_schema is None else [k for k in schema.tables if k[0] == in_schema]
            for s, n in targets:
                out.setdefault(f"grant:{s}.{n}", (st.file, st.line))
    for st, role, _flag in schema.roles:
        out[f"role:{role}"] = (st.file, st.line)
    return out


def introspect_ephemeral(sql_files: list[Path], *, timeout_s: float = 120, root: Path | None = None) -> IntrospectionReport:
    """Apply the migrations to a throwaway local cluster and read its catalogs."""
    started = time.monotonic()
    report = IntrospectionReport(engine=EPHEMERAL_ENGINE, status="ok")
    files = sorted((Path(p) for p in sql_files), key=lambda p: p.as_posix())
    if not files:
        report.status, report.detail = "not_applicable", "no SQL migration file"
        return report
    bins = find_postgres_binaries()
    if bins is None:
        report.status, report.detail = "not_installed", "PostgreSQL binaries (initdb, pg_ctl, psql, postgres) not found"
        return report
    deadline = started + timeout_s
    tmp = Path(tempfile.mkdtemp(prefix="navin-pg-"))
    data, sock = tmp / "data", tmp / "s"
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env.update({"LC_ALL": "C", "PGTZ": "UTC"})
    running = False
    try:
        os.chmod(tmp, 0o700)
        sock.mkdir()
        proc = _run([bins.initdb, "-D", str(data), "-U", _SUPERUSER, "-A", "trust", "-E", "UTF8", "--no-sync"],
                    timeout=deadline - time.monotonic(), env=env)
        if proc.returncode != 0:
            report.status, report.detail = "failed", f"initdb failed: {_first_error(proc.stderr)}"
            return report
        with open(data / "postgresql.conf", "a", encoding="utf-8") as conf:
            conf.write(f"\nlisten_addresses = ''\nunix_socket_directories = '{sock}'\nfsync = off\n"
                       "synchronous_commit = off\nfull_page_writes = off\nshared_buffers = 16MB\n")
        (data / "pg_hba.conf").write_text("local all all trust\n", encoding="utf-8")
        wait = int(max(5, min(60, deadline - time.monotonic())))
        running = True
        proc = _run([bins.pg_ctl, "-D", str(data), "-l", str(tmp / "server.log"), "-w", "-t", str(wait), "start"],
                    timeout=wait + 10, env=env)
        if proc.returncode != 0:
            log = (tmp / "server.log").read_text(encoding="utf-8", errors="replace") if (tmp / "server.log").exists() else proc.stderr
            report.status, report.detail = "failed", f"server start failed: {_first_error(log)}"
            return report
        conn = ["-h", str(sock), "-U", _SUPERUSER, "-d", "postgres"]
        stub = _run([bins.psql, *conn, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", SUPABASE_STUBS], timeout=deadline - time.monotonic(), env=env)
        if stub.returncode != 0:
            logger.warning("db introspection: supabase stubs failed: {}", _first_error(stub.stderr))
        # The migrations come from the audited repository: never run them as a superuser, which could reach
        # the host (COPY ... PROGRAM, untrusted languages, C functions, server files). A plain owner role
        # with CREATEROLE can still create the tables, policies, functions, views, grants and roles RLS uses.
        setup = _run([bins.psql, *conn, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", MIGRATOR_SETUP],
                     timeout=deadline - time.monotonic(), env=env)
        if setup.returncode != 0:
            report.status, report.detail = "failed", f"migration role setup failed: {_first_error(setup.stderr)}"
            return report
        mig_conn = ["-h", str(sock), "-U", MIGRATOR, "-d", "postgres"]
        work = tmp / "migrations"
        work.mkdir()
        for index, path in enumerate(files):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                report.failed.append({"file": path.as_posix(), "error": f"not read: {exc.strerror or exc}"})
                continue
            copy = work / f"{index:04d}.sql"
            dialect = _foreign_dialect(text)
            if dialect:
                report.skipped.append({"file": path.as_posix(), "reason": f"{dialect} script, not PostgreSQL"})
                continue
            copy.write_text(_tolerant_extensions(text), encoding="utf-8")
            res = _run([bins.psql, *mig_conn, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-f", str(copy)], timeout=deadline - time.monotonic(), env=env)
            if res.returncode == 0:
                report.applied.append(path.as_posix())
            else:
                report.failed.append({"file": path.as_posix(), "error": _first_error(res.stderr)})
        _read_catalog(_Reader(bins.psql, conn, env, deadline, wrap_readonly=False), report, locate_objects(files, root))
        grant = _run([bins.psql, *conn, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-c", f"GRANT anon, authenticated TO {MIGRATOR};"],
                     timeout=deadline - time.monotonic(), env=env)
        if grant.returncode == 0:
            _role_tests(bins.psql, _Reader(bins.psql, conn, env, deadline, wrap_readonly=False), mig_conn, env, deadline, report,
                        locate_objects(files, root))
        else:
            logger.warning("db introspection: role test setup failed: {}", _first_error(grant.stderr))
        tested = sum(1 for t in report.role_tests if t.get("tested"))
        report.detail = (f"{len(report.applied)} migration(s) applied, {len(report.failed)} failed, "
                         f"{len(report.skipped)} other-dialect script(s) skipped, PostgreSQL {report.postgres_version}, "
                         f"row-level access tested on {tested} table(s) as anon and as two users")
    except subprocess.TimeoutExpired:
        report.status, report.detail = "failed", f"timed out after {timeout_s:.0f}s"
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        logger.warning("db introspection (ephemeral) failed: {}", exc)
        report.status, report.detail = "failed", f"{type(exc).__name__}: {exc}"[:500]
    finally:
        if running:
            try:
                _run([bins.pg_ctl, "-D", str(data), "-m", "immediate", "-w", "-t", "15", "stop"], timeout=25, env=env)
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.warning("db introspection: pg_ctl stop failed: {}", exc)
        shutil.rmtree(tmp, ignore_errors=True)
        report.duration_s = round(time.monotonic() - started, 3)
    return report


def introspect_url(database_url: str, *, timeout_s: float = 60) -> IntrospectionReport:
    """Read-only catalog introspection of a user-provided database; the password never leaves this function."""
    started = time.monotonic()
    report = IntrospectionReport(engine=LIVE_ENGINE, status="ok")
    safe = redact_url(database_url)
    password = ""
    if m := re.search(r"://[^:/@\s]*:([^@\s]*)@", database_url):
        password = m.group(1)

    def scrub(text: str) -> str:
        text = redact_url(text)
        return text.replace(password, "***") if password else text

    bins = find_postgres_binaries()
    psql = shutil.which("psql") or (bins.psql if bins else None)
    if not psql:
        report.status, report.detail = "not_installed", "psql not found"
        return report
    env = dict(os.environ)
    env["PGOPTIONS"] = "-c default_transaction_read_only=on"
    env["PGCONNECT_TIMEOUT"] = str(int(max(2, min(15, timeout_s / 4))))
    try:
        _read_catalog(_Reader(psql, ["-d", database_url], env, started + timeout_s, wrap_readonly=True), report, {})
        report.detail = f"read-only introspection of {safe}: {len(report.tables)} table(s), {len(report.policies)} policy(ies)"
    except subprocess.TimeoutExpired:
        report.status, report.detail = "failed", f"timed out after {timeout_s:.0f}s on {safe}"
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        logger.warning("db introspection (live) failed on {}: {}", safe, scrub(str(exc)))
        report.status, report.detail = "failed", scrub(f"{type(exc).__name__}: {exc} ({safe})")[:500]
    finally:
        report.duration_s = round(time.monotonic() - started, 3)
    return report
