# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Datastore security beyond PostgreSQL: SQL dialects, server configs, security rules, connection strings.

PostgreSQL row level security has its own analyzer (``database``); this one reads what the other engines
leave in a repository:

- MySQL / MariaDB, SQL Server and Oracle scripts: global grants, administrator roles, accounts open to
  any host or with an empty password, definer routines, ``xp_cmdshell``, ``TRUSTWORTHY``;
- server configs: ``redis.conf``, ``mongod.conf``, ``my.cnf``, ``pg_hba.conf`` / ``postgresql.conf``,
  ``elasticsearch.yml`` / ``opensearch.yml``, ``cassandra.yaml``;
- Firebase security rules (Firestore, Storage, Realtime Database);
- Compose files and env files: default or empty database passwords, database ports published on every
  interface;
- connection strings in code and config: TLS turned off, server certificate not verified, ``sa`` logins.

Every rule reads text only (no connection, no subprocess); one bounded walk collects the config files.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from navin.security.analyzers.common import SKIP_DIRS, SourceFile, SourceSet
from navin.security.model import Evidence, Finding

ANALYZER_ID = "navin_datastores"
# Findings set the status of their controls; a run without findings proves no catalog control on its own.
CHECKED_CONTROLS: frozenset[str] = frozenset()

_MAX_FILES_SEEN = 8000
_MAX_CONFIG_FILES = 1500
_MAX_BYTES = 400_000

_CONFIG_KINDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(^|/)redis[^/]*\.conf$", re.I), "redis"),
    (re.compile(r"(^|/)(mongod|mongodb)\.(conf|ya?ml)$|(^|/)mongod[-_.][^/]*\.conf$", re.I), "mongodb"),
    (re.compile(r"(^|/)(my|mysqld|mariadb|server)[^/]*\.cnf$", re.I), "mysql"),
    (re.compile(r"(^|/)pg_hba[^/]*\.conf$", re.I), "pg_hba"),
    (re.compile(r"(^|/)postgresql[^/]*\.conf$", re.I), "postgresql"),
    (re.compile(r"(^|/)(elasticsearch|opensearch)\.ya?ml$", re.I), "search"),
    (re.compile(r"(^|/)cassandra\.ya?ml$", re.I), "cassandra"),
    (re.compile(r"(^|/)(firestore|storage)[^/]*\.rules$", re.I), "firebase_rules"),
    (re.compile(r"(^|/)database\.rules\.json$", re.I), "firebase_rtdb"),
    (re.compile(r"(^|/)(docker-)?compose[^/]*\.ya?ml$", re.I), "compose"),
    (re.compile(r"(^|/)(\.env(\.[^/]*)?|[^/]+\.env)$", re.I), "env"),
    (re.compile(r"\.(properties|ini|cfg|toml)$|(^|/)appsettings[^/]*\.json$|(^|/)application[^/]*\.ya?ml$", re.I), "config"),
    # Code the Python / JS analyzers do not read: only its connection strings are checked.
    (re.compile(r"\.(java|kt|go|cs|php|rb|scala)$", re.I), "config"),
)

_DB_PORTS = frozenset({"5432", "3306", "27017", "27018", "6379", "9200", "9300", "1433", "1521", "9042", "5984", "8123", "9000", "7687", "26257"})
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
_WEAK_PASSWORDS = frozenset({
    "postgres", "root", "password", "passw0rd", "admin", "changeme", "change_me", "secret", "example", "123456",
    "12345678", "mysql", "mongo", "redis", "test", "sa", "toor", "pass", "elastic", "default",
})
_PASSWORD_ENV = re.compile(
    r"^\s*-?\s*[\"']?(POSTGRES_PASSWORD|PGPASSWORD|MYSQL_ROOT_PASSWORD|MYSQL_PASSWORD|MARIADB_ROOT_PASSWORD|MARIADB_PASSWORD|"
    r"MONGO_INITDB_ROOT_PASSWORD|SA_PASSWORD|MSSQL_SA_PASSWORD|ELASTIC_PASSWORD|REDIS_PASSWORD|ORACLE_PASSWORD|"
    r"ORACLE_PWD|CASSANDRA_PASSWORD|NEO4J_AUTH|CLICKHOUSE_PASSWORD|COUCHDB_PASSWORD)[\"']?\s*[:=]\s*[\"']?([^\"'\s#]*)[\"']?\s*(#.*)?$",
)
_NO_PASSWORD_ENV = re.compile(
    r"(?i)^\s*-?\s*[\"']?(MYSQL_ALLOW_EMPTY_PASSWORD|MARIADB_ALLOW_EMPTY_ROOT_PASSWORD|ALLOW_EMPTY_PASSWORD)[\"']?\s*[:=]\s*[\"']?(yes|true|1)\b"
    r"|^\s*-?\s*[\"']?POSTGRES_HOST_AUTH_METHOD[\"']?\s*[:=]\s*[\"']?trust\b"
    r"|^\s*-?\s*[\"']?NEO4J_AUTH[\"']?\s*[:=]\s*[\"']?none\b"
)
_PORT_LINE = re.compile(r"^\s*-\s*[\"']?(?:(?P<host>\d{1,3}(?:\.\d{1,3}){3}|\[[0-9a-fA-F:]+\]|localhost):)?(?P<pub>\d{2,5}):(?P<target>\d{2,5})(?:/tcp)?[\"']?\s*(#.*)?$")
_DB_URL = re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://|mysql(?:\+\w+)?://|mariadb://|mongodb(?:\+srv)?://|rediss?://|jdbc:\w+:|sqlserver://|"
                     r"\b(?:Server|Data Source|Host)\s*=)")
_TLS_OFF = re.compile(r"(?i)(\bsslmode=disable\b|\buseSSL=false\b|\brequireSSL=false\b|[?&;]ssl=false\b|[?&;]tls=false\b|\bssl-mode=DISABLED\b|"
                      r"\bEncrypt\s*=\s*(False|No|Optional)\b)")
_CERT_OFF = re.compile(r"(?i)(\bTrustServerCertificate\s*=\s*(True|Yes)\b|\btlsAllowInvalidCertificates=true\b|\btlsInsecure=true\b|"
                       r"\bssl_cert_reqs=(none|CERT_NONE)\b|\bsslAllowInvalidCertificates=true\b)")
_REJECT_UNAUTHORIZED = re.compile(r"\bssl\s*:\s*\{[^}]*rejectUnauthorized\s*:\s*false")
_SA_LOGIN = re.compile(r"(?i)\b(User\s*Id|uid|user)\s*=\s*sa\s*;")
_COMMENT = re.compile(r"^\s*(#|//|--|;|\*)")
_MASK = re.compile(r"(?i)(password|passwd|pwd|identified\s+by|requirepass|masterauth|_PASSWORD|NEO4J_AUTH)(\s*[:=]?\s*[\"'`]?)([^\"'`\s;,]+)")
_URL_CREDENTIALS = re.compile(r"(://[^:/@\s]*:)[^@\s]+@")

# SQL dialect rules: (rule id, pattern, dialects it needs or None for any)
_SQL_MSSQL = re.compile(r"(?im)^\s*GO\s*$|\bNVARCHAR\b|\bIDENTITY\s*\(|\[dbo\]\.|\bsp_\w+|\bsys\.\w+")
_SQL_ORACLE = re.compile(r"(?i)\bVARCHAR2\b|\bNUMBER\s*\(|\bCREATE\s+OR\s+REPLACE\s+PACKAGE\b|\bFROM\s+DUAL\b|\bTABLESPACE\b")


def _mask(line: str) -> str:
    """The evidence line with every password and URL credential replaced: never a single character of it."""
    line = _URL_CREDENTIALS.sub(r"\1***@", line.strip())
    return _MASK.sub(lambda m: f"{m[1]}{m[2]}***", line)[:300]


@dataclass(frozen=True)
class _Rule:
    id: str
    family: str
    severity: str
    cwe: str
    controls: tuple[str, ...]
    title: str
    remediation: str
    confidence: float = 0.8


_RULES: dict[str, _Rule] = {r.id: r for r in (
    _Rule("ds-sql-grant-all-global", "authorization", "high", "CWE-269", ("C2.08",),
          "Global grant of every privilege on every database (ON *.*)",
          "Grant the application account only the privileges it needs, on its own schema; keep administration to a separate account."),
    _Rule("ds-sql-high-privilege", "authorization", "high", "CWE-250", ("C2.08",),
          "Administrator-level database privilege granted (SUPER, FILE, DBA, ANY, sysadmin or db_owner)",
          "Remove administrator privileges from application and service accounts; use a dedicated, audited admin account."),
    _Rule("ds-sql-grant-public", "authorization", "medium", "CWE-732", ("C2.08",),
          "Privilege granted to PUBLIC: every database user receives it",
          "Grant to a named role instead of PUBLIC and revoke what PUBLIC holds by default when it is not needed."),
    _Rule("ds-mysql-any-host", "authorization", "medium", "CWE-284", ("C4.09",),
          "Database account accepted from any host ('user'@'%')",
          "Restrict the account to the application hosts or network ('app'@'10.0.%') and keep the server off public networks."),
    _Rule("ds-mysql-empty-password", "authentication", "high", "CWE-258", ("C2.10",),
          "Database account created with an empty password",
          "Give every account a strong password from a secret manager, or use certificate or IAM authentication."),
    _Rule("ds-definer-routine", "authorization", "medium", "CWE-269", ("C2.08",),
          "Routine or view runs with its definer's rights (SQL SECURITY DEFINER, EXECUTE AS OWNER, AUTHID DEFINER)",
          "Prefer invoker rights; when definer rights are needed, define the routine with a low-privilege owner and validate its inputs.",
          0.7),
    _Rule("ds-mssql-xp-cmdshell", "code_execution", "critical", "CWE-78", ("C1.04",),
          "xp_cmdshell enabled: SQL can run operating system commands",
          "Keep xp_cmdshell disabled (sp_configure 'xp_cmdshell', 0) and run OS tasks outside the database."),
    _Rule("ds-mssql-clr-unsafe", "code_execution", "high", "CWE-250", ("C1.04",),
          "Unsafe CLR assembly or CLR strict security turned off",
          "Keep CLR strict security on and load only signed SAFE assemblies."),
    _Rule("ds-mssql-trustworthy", "authorization", "high", "CWE-269", ("C2.08",),
          "Database marked TRUSTWORTHY: its owner's server rights leak into the database",
          "SET TRUSTWORTHY OFF and sign the modules that need extra rights instead."),
    _Rule("ds-rls-policy-off", "multitenancy", "high", "CWE-284", ("C2.09",),
          "Row-level security policy created or switched off (SQL Server STATE = OFF, Oracle VPD disabled)",
          "Keep the security policy enabled (STATE = ON, DBMS_RLS enable => TRUE) and test it with two tenants."),
    _Rule("ds-mssql-sa-login", "authentication", "medium", "CWE-250", ("C2.10",),
          "Connection or script uses the sa administrator login",
          "Disable or rename sa and connect the application with a least-privilege login."),
    _Rule("ds-redis-no-auth", "authentication", "high", "CWE-306", ("C2.10", "C4.09"),
          "Redis without authentication (no requirepass, no ACL users)",
          "Set requirepass or ACL users (aclfile), keep protected-mode on and bind Redis to private interfaces."),
    _Rule("ds-redis-protected-mode-off", "infrastructure", "high", "CWE-306", ("C4.09",),
          "Redis protected-mode turned off",
          "Keep protected-mode yes unless Redis is bound to a private interface and requires authentication."),
    _Rule("ds-mongo-no-auth", "authentication", "high", "CWE-306", ("C2.10", "C4.09"),
          "MongoDB without access control (authorization not enabled)",
          "Set security.authorization: enabled, create least-privilege users and bind MongoDB to private interfaces."),
    _Rule("ds-mysql-skip-grant-tables", "authentication", "critical", "CWE-306", ("C2.10",),
          "MySQL started with skip-grant-tables: every connection is accepted with full rights",
          "Remove skip-grant-tables; it is only for one-off password recovery on an isolated server."),
    _Rule("ds-mysql-local-infile", "files", "medium", "CWE-73", ("C1.06",),
          "local_infile enabled: a malicious server or injected query can read client files",
          "Set local_infile = 0 on the server and in the client drivers."),
    _Rule("ds-pg-hba-open", "authentication", "high", "CWE-306", ("C2.10", "C4.09"),
          "pg_hba.conf lets remote hosts connect without a password check (trust) or with a cleartext password",
          "Use scram-sha-256 (or certificates) for every host line and restrict the address ranges."),
    _Rule("ds-search-security-off", "authentication", "high", "CWE-306", ("C2.10", "C4.09"),
          "Elasticsearch / OpenSearch security plugin turned off: the cluster accepts unauthenticated requests",
          "Keep xpack.security.enabled (or the OpenSearch security plugin) on, with TLS and role-based users."),
    _Rule("ds-cassandra-no-auth", "authentication", "high", "CWE-306", ("C2.10",),
          "Cassandra accepts every client (AllowAllAuthenticator / AllowAllAuthorizer)",
          "Use PasswordAuthenticator and CassandraAuthorizer with least-privilege roles."),
    _Rule("ds-firebase-open-rules", "authorization", "high", "CWE-284", ("C2.09",),
          "Firebase security rules open the data to everyone (allow ... if true, or no condition)",
          "Scope every rule to request.auth (request.auth.uid == resource.data.owner) and deny by default."),
    _Rule("ds-db-default-password", "authentication", "high", "CWE-1392", ("C2.10",),
          "Database container configured with a default or trivial password",
          "Inject the password from a secret store (Docker secrets, Kubernetes Secret, vault), never a default value."),
    _Rule("ds-db-no-password", "authentication", "high", "CWE-306", ("C2.10",),
          "Database container started without a password (empty password allowed or trust authentication)",
          "Remove the empty-password or trust setting and provide a password from a secret store."),
    _Rule("ds-db-port-exposed", "infrastructure", "medium", "CWE-668", ("C4.09",),
          "Database port published on every interface",
          "Publish the port on 127.0.0.1 only (\"127.0.0.1:5432:5432\") or not at all; reach the database over a private network.",
          0.7),
    _Rule("ds-db-bind-all", "infrastructure", "medium", "CWE-668", ("C4.09",),
          "Database server listens on every network interface",
          "Bind the server to the private interface it needs and firewall the port.", 0.7),
    _Rule("ds-conn-tls-disabled", "cryptography", "medium", "CWE-319", ("C4.09",),
          "Database connection with TLS turned off",
          "Require TLS (sslmode=verify-full, useSSL=true, Encrypt=True) on every connection that leaves the host."),
    _Rule("ds-conn-cert-unverified", "cryptography", "medium", "CWE-295", ("C4.09",),
          "Database TLS without server certificate verification",
          "Verify the server certificate against a trusted CA (TrustServerCertificate=False, rejectUnauthorized true)."),
)}


def _finding(rule_id: str, rel: str, line: int, text: str, *, component: str, detail: str = "", severity: str = "",
             confidence: float | None = None) -> Finding:
    rule = _RULES[rule_id]
    title = f"{rule.title}: {detail}" if detail else rule.title
    return Finding(
        id=rule.id, family=rule.family, severity=severity or rule.severity, cwe=rule.cwe, title=title,
        file_path=rel, line=line, component=component, controls=list(rule.controls), source=ANALYZER_ID,
        confidence=rule.confidence if confidence is None else confidence, remediation=rule.remediation, description=title,
        evidence=[Evidence(kind="static", description=rule.title, tool=ANALYZER_ID, observed=_mask(text), location=f"{rel}:{line}")],
    )


def _lines(text: str) -> list[tuple[int, str]]:
    return list(enumerate(text.splitlines(), start=1))


# ---------------------------------------------------------------------------
# SQL dialects
# ---------------------------------------------------------------------------

_SQL_LINE_RULES: tuple[tuple[str, re.Pattern[str], str | None], ...] = (
    ("ds-sql-grant-all-global", re.compile(r"(?i)\bGRANT\s+ALL(\s+PRIVILEGES)?\s+ON\s+\*\.\*\s+TO\b"), None),
    ("ds-sql-high-privilege", re.compile(
        r"(?i)\bGRANT\s+[^;]*\b(SUPER|FILE|SHUTDOWN|CREATE\s+USER|REPLICATION\s+SLAVE)\b[^;]*\bON\s+\*\.\*"
        r"|\bGRANT\s+(DBA|SYSDBA|SYSOPER)\b|\bGRANT\s+ALL\s+PRIVILEGES\s+TO\b|\bGRANT\s+[^;]*\bANY\s+(TABLE|DICTIONARY|PRIVILEGE)\b"
        r"|\bALTER\s+SERVER\s+ROLE\s+\[?sysadmin\]?\s+ADD\s+MEMBER\b|\bsp_addsrvrolemember\b[^;]*sysadmin"
        r"|\bALTER\s+ROLE\s+\[?db_owner\]?\s+ADD\s+MEMBER\b|\bsp_addrolemember\b[^;]*db_owner"), None),
    ("ds-sql-grant-public", re.compile(r"(?i)\bGRANT\s+[^;]+\bTO\s+PUBLIC\b"), "mssql_oracle"),
    ("ds-mysql-any-host", re.compile(r"(?i)\b(CREATE|ALTER|RENAME)\s+USER\b[^;]*@\s*['\"`]%['\"`]|\bTO\s+['\"`]?\w+['\"`]?\s*@\s*['\"`]%['\"`]"), None),
    ("ds-mysql-empty-password", re.compile(r"(?i)\bIDENTIFIED\s+(WITH\s+\w+\s+)?BY\s+''(?!')"), None),
    ("ds-definer-routine", re.compile(r"(?i)\bSQL\s+SECURITY\s+DEFINER\b|\bWITH\s+EXECUTE\s+AS\s+(OWNER|'dbo')|\bAUTHID\s+DEFINER\b"), None),
    ("ds-mssql-xp-cmdshell", re.compile(r"(?i)\bsp_configure\s+['\"]?xp_cmdshell['\"]?\s*,\s*1\b"), None),
    ("ds-mssql-clr-unsafe", re.compile(r"(?i)\bPERMISSION_SET\s*=\s*UNSAFE\b|\bsp_configure\s+['\"]clr strict security['\"]\s*,\s*0\b"), None),
    ("ds-mssql-trustworthy", re.compile(r"(?i)\bSET\s+TRUSTWORTHY\s+ON\b"), None),
    ("ds-rls-policy-off", re.compile(r"(?i)\bSECURITY\s+POLICY\b[^;]*\bSTATE\s*=\s*OFF\b|\bDBMS_RLS\.(DISABLE_POLICY|ENABLE_POLICY\s*\([^)]*FALSE)"), None),
    ("ds-mssql-sa-login", re.compile(r"(?i)\bALTER\s+LOGIN\s+\[?sa\]?\s+(ENABLE|WITH\s+PASSWORD)\b"), None),
)


def _sql(src: SourceFile) -> list[Finding]:
    text = src.text
    dialects = set()
    if _SQL_MSSQL.search(text):
        dialects.add("mssql_oracle")
    if _SQL_ORACLE.search(text):
        dialects.add("mssql_oracle")
    out: list[Finding] = []
    for line_no, raw in _lines(text):
        line = raw.split("--", 1)[0]
        if not line.strip():
            continue
        for rule_id, pattern, needs in _SQL_LINE_RULES:
            if needs and needs not in dialects:
                continue
            if pattern.search(line):
                out.append(_finding(rule_id, src.rel, line_no, raw, component="sql"))
    return out


# ---------------------------------------------------------------------------
# Server configs and security rules
# ---------------------------------------------------------------------------


def _redis(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    has_auth = bool(re.search(r"(?im)^\s*(requirepass\s+\S|aclfile\s+\S|user\s+\S+\s+on\b)", text))
    for line_no, line in _lines(text):
        if _COMMENT.match(line):
            continue
        if re.match(r"(?i)^\s*protected-mode\s+no\b", line):
            out.append(_finding("ds-redis-protected-mode-off", rel, line_no, line, component="redis"))
        if re.match(r"(?i)^\s*bind\s+.*(\b0\.0\.0\.0\b|\*|::\s*$|-::\*)", line):
            out.append(_finding("ds-db-bind-all", rel, line_no, line, component="redis"))
    if not has_auth:
        out.append(_finding("ds-redis-no-auth", rel, 1, text.splitlines()[0] if text.strip() else "", component="redis",
                            detail="no requirepass and no ACL user in this file (a password passed at start-up is not visible here)",
                            severity="medium", confidence=0.6))
    return out


def _mongo(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    explicit_off = None
    for line_no, line in _lines(text):
        if _COMMENT.match(line):
            continue
        if re.search(r"(?i)^\s*authorization\s*:\s*[\"']?disabled|^\s*noauth\s*=\s*true|^\s*auth\s*=\s*false", line):
            explicit_off = (line_no, line)
        if re.search(r"(?i)^\s*bindIp\s*:\s*[\"']?[^#]*\b0\.0\.0\.0\b|^\s*bindIpAll\s*:\s*true|^\s*bind_ip\s*=\s*0\.0\.0\.0", line):
            out.append(_finding("ds-db-bind-all", rel, line_no, line, component="mongodb"))
    if explicit_off:
        out.append(_finding("ds-mongo-no-auth", rel, explicit_off[0], explicit_off[1], component="mongodb", confidence=0.9))
    elif not re.search(r"(?im)^\s*(authorization\s*:\s*[\"']?enabled|auth\s*=\s*true|keyFile\s*[:=])", text):
        out.append(_finding("ds-mongo-no-auth", rel, 1, text.splitlines()[0] if text.strip() else "", component="mongodb",
                            detail="security.authorization is never enabled (MongoDB's default is no access control)",
                            severity="medium", confidence=0.6))
    return out


def _mysql_cnf(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _lines(text):
        if _COMMENT.match(line):
            continue
        if re.match(r"(?i)^\s*bind[-_]address\s*=\s*(0\.0\.0\.0|\*|::)\s*$", line):
            out.append(_finding("ds-db-bind-all", rel, line_no, line, component="mysql"))
        if re.match(r"(?i)^\s*local[-_]infile\s*=\s*(1|on|true)\s*$", line):
            out.append(_finding("ds-mysql-local-infile", rel, line_no, line, component="mysql"))
        if re.match(r"(?i)^\s*skip[-_]grant[-_]tables\b", line):
            out.append(_finding("ds-mysql-skip-grant-tables", rel, line_no, line, component="mysql"))
        if re.match(r"(?i)^\s*require[-_]secure[-_]transport\s*=\s*(off|0|false)\s*$", line):
            out.append(_finding("ds-conn-tls-disabled", rel, line_no, line, component="mysql"))
    return out


_HBA = re.compile(r"^\s*host(ssl|nossl)?\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(\d{1,3}(?:\.\d{1,3}){3}))?\s+(trust|password)\b", re.I)
_HBA_LOCAL = {"127.0.0.1/32", "::1/128", "localhost", "samehost"}


def _pg(rel: str, text: str, kind: str) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _lines(text):
        if _COMMENT.match(line):
            continue
        if kind == "pg_hba":
            m = _HBA.match(line)
            if m and m.group(4).lower() not in _HBA_LOCAL:
                everyone = m.group(4) in {"0.0.0.0/0", "::/0", "all"}
                method = m.group(6).lower()
                severity = "critical" if method == "trust" and everyone else ("medium" if method == "password" else "high")
                out.append(_finding("ds-pg-hba-open", rel, line_no, line, component="postgresql", severity=severity,
                                    detail=f"{method} from {m.group(4)}"))
        else:
            if re.match(r"(?i)^\s*ssl\s*=\s*['\"]?off['\"]?\s*(#.*)?$", line):
                out.append(_finding("ds-conn-tls-disabled", rel, line_no, line, component="postgresql", severity="low"))
            if re.match(r"(?i)^\s*listen_addresses\s*=\s*['\"]\*['\"]", line):
                out.append(_finding("ds-db-bind-all", rel, line_no, line, component="postgresql", severity="low", confidence=0.5))
    return out


def _search(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _lines(text):
        if _COMMENT.match(line):
            continue
        if re.search(r"(?i)^\s*(xpack\.security\.enabled\s*:\s*[\"']?false|plugins\.security\.disabled\s*:\s*[\"']?true)", line):
            out.append(_finding("ds-search-security-off", rel, line_no, line, component="search"))
        if re.search(r"(?i)^\s*network\.host\s*:\s*[\"']?(0\.0\.0\.0|_site_|_global_)", line):
            out.append(_finding("ds-db-bind-all", rel, line_no, line, component="search"))
    return out


def _cassandra(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _lines(text):
        if re.search(r"^\s*authenticator\s*:\s*(org\.apache\.cassandra\.auth\.)?AllowAllAuthenticator\b", line):
            out.append(_finding("ds-cassandra-no-auth", rel, line_no, line, component="cassandra"))
        elif re.search(r"^\s*authorizer\s*:\s*(org\.apache\.cassandra\.auth\.)?AllowAllAuthorizer\b", line):
            out.append(_finding("ds-cassandra-no-auth", rel, line_no, line, component="cassandra", severity="medium",
                                detail="AllowAllAuthorizer gives every role every permission"))
    return out


_RULES_ALLOW = re.compile(r"\ballow\s+([a-z,\s]+?)\s*(?::\s*if\s+(.+?))?\s*;", re.I)
_RULES_TEST_MODE = re.compile(r"request\.time\s*<\s*timestamp\.date\(")


def _firebase_rules(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _lines(text):
        if _COMMENT.match(line):
            continue
        for m in _RULES_ALLOW.finditer(line):
            ops = {op.strip().lower() for op in m.group(1).split(",") if op.strip()}
            cond = (m.group(2) or "").strip()
            writes = bool(ops & {"write", "create", "update", "delete"})
            if not cond or cond.lower() == "true":
                out.append(_finding("ds-firebase-open-rules", rel, line_no, line, component="firebase",
                                    severity="high" if writes else "medium", detail=f"allow {', '.join(sorted(ops))} for everyone"))
            elif _RULES_TEST_MODE.search(cond) and "request.auth" not in cond:
                out.append(_finding("ds-firebase-open-rules", rel, line_no, line, component="firebase", severity="medium",
                                    detail="test-mode rule: open to everyone until a date", confidence=0.7))
    return out


def _firebase_rtdb(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _lines(text):
        m = re.search(r"[\"']\.(read|write)[\"']\s*:\s*(true|[\"']true[\"'])", line)
        if m:
            out.append(_finding("ds-firebase-open-rules", rel, line_no, line, component="firebase",
                                severity="high" if m.group(1) == "write" else "medium", detail=f".{m.group(1)} is true for everyone"))
    return out


def _compose_and_env(rel: str, text: str, kind: str) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _lines(text):
        if _COMMENT.match(line):
            continue
        m = _PASSWORD_ENV.match(line)
        if m:
            key, value = m.group(1), m.group(2).strip()
            low = value.lower()
            if key == "NEO4J_AUTH" and "/" in low:
                low = low.split("/", 1)[1]
            if value and not value.startswith("$") and low in _WEAK_PASSWORDS:
                out.append(_finding("ds-db-default-password", rel, line_no, line, component="container", detail=key))
            elif value == "" and kind == "compose" and re.search(r"[:=]\s*[\"']{2}\s*$", line):
                out.append(_finding("ds-db-no-password", rel, line_no, line, component="container", detail=f"{key} is empty"))
        if _NO_PASSWORD_ENV.search(line):
            out.append(_finding("ds-db-no-password", rel, line_no, line, component="container"))
        if kind == "compose":
            p = _PORT_LINE.match(line)
            if p and p.group("target") in _DB_PORTS and (p.group("host") or "") not in _LOCAL_HOSTS:
                out.append(_finding("ds-db-port-exposed", rel, line_no, line, component="container",
                                    detail=f"container port {p.group('target')} published on {p.group('host') or 'every interface'}"))
    return out


def _connection_strings(rel: str, text: str) -> list[Finding]:
    out: list[Finding] = []
    for line_no, line in _lines(text):
        if _COMMENT.match(line) or len(line) > 2000:
            continue
        db_line = bool(_DB_URL.search(line))
        if db_line and _TLS_OFF.search(line):
            out.append(_finding("ds-conn-tls-disabled", rel, line_no, line, component="connection"))
        if (db_line and _CERT_OFF.search(line)) or _REJECT_UNAUTHORIZED.search(line):
            out.append(_finding("ds-conn-cert-unverified", rel, line_no, line, component="connection"))
        if db_line and _SA_LOGIN.search(line):
            out.append(_finding("ds-mssql-sa-login", rel, line_no, line, component="connection"))
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _config_files(root: Path) -> list[tuple[str, str, str]]:
    """(rel, kind, text) of the datastore config files, one bounded walk, never through a symlink."""
    out: list[tuple[str, str, str]] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            seen += 1
            if seen > _MAX_FILES_SEEN or len(out) >= _MAX_CONFIG_FILES:
                return out
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            kind = next((k for pattern, k in _CONFIG_KINDS if pattern.search(rel)), None)
            if kind is None or full.is_symlink():
                continue
            try:
                if full.stat().st_size > _MAX_BYTES:
                    continue
                out.append((rel, kind, full.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return out


def analyze(sources: SourceSet) -> list[Finding]:
    findings: list[Finding] = []
    for src in sources.sql:
        findings.extend(_sql(src))
    for src in (*sources.python, *sources.scripts):
        findings.extend(_connection_strings(src.rel, src.text))
    for rel, kind, text in _config_files(Path(sources.root)):
        if kind == "redis":
            findings.extend(_redis(rel, text))
        elif kind == "mongodb":
            findings.extend(_mongo(rel, text))
        elif kind == "mysql":
            findings.extend(_mysql_cnf(rel, text))
        elif kind in {"pg_hba", "postgresql"}:
            findings.extend(_pg(rel, text, kind))
        elif kind == "search":
            findings.extend(_search(rel, text))
        elif kind == "cassandra":
            findings.extend(_cassandra(rel, text))
        elif kind == "firebase_rules":
            findings.extend(_firebase_rules(rel, text))
        elif kind == "firebase_rtdb":
            findings.extend(_firebase_rtdb(rel, text))
        elif kind in {"compose", "env"}:
            findings.extend(_compose_and_env(rel, text, kind))
            if kind == "env":
                findings.extend(_connection_strings(rel, text))
        else:
            findings.extend(_connection_strings(rel, text))
    return findings


RULE_IDS: tuple[str, ...] = tuple(_RULES)
