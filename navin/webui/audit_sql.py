# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""SQL catalog + security/performance findings for Vision 360.

Scans every code and ``.sql`` file in the project (not a single blob), splits
statements, and attaches concrete findings users can act on.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

_MAX_QUERIES = 400
_MAX_FINDINGS = 120

_SQL_KIND = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|WITH)\b",
    re.IGNORECASE,
)
_SQL_HAS_KEYWORD = re.compile(
    r"\b(SELECT\s+.+?\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|"
    r"CREATE\s+(TABLE|INDEX|VIEW|OR\s+REPLACE)|ALTER\s+TABLE|DROP\s+TABLE|"
    r"TRUNCATE\s+TABLE)\b",
    re.IGNORECASE | re.DOTALL,
)
_DROP_TABLE = re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE)
_TRUNCATE_TABLE = re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE)

# Start of a Python/JS/TS string that embeds SQL.
_EMBEDDED_SQL_START = re.compile(
    r"""(?P<prefix>(?:f|rf|fr|r|b)?(?P<q>["']{1,3}))\s*"""
    r"""(?P<body>(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|TRUNCATE|WITH)\b)""",
    re.IGNORECASE,
)

_TABLE_FROM = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+([`\"'\[]?\w+[`\"'\]]?(?:\.[`\"'\[]?\w+[`\"'\]]?)?)",
    re.IGNORECASE,
)
_WHERE = re.compile(r"\bWHERE\b", re.IGNORECASE)
_LIMIT = re.compile(r"\bLIMIT\b", re.IGNORECASE)
_SELECT_STAR = re.compile(r"\bSELECT\s+\*\s+FROM\b", re.IGNORECASE)
_LIKE_LEADING = re.compile(
    r"""\b(?:I?LIKE|SIMILAR\s+TO)\s+['\"]%""",
    re.IGNORECASE,
)
_ORDER_BY = re.compile(r"\bORDER\s+BY\b", re.IGNORECASE)
_CREATE_INDEX = re.compile(
    r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\s+\w+\s+ON\s+([`\"']?\w+[`\"']?)",
    re.IGNORECASE,
)

# Dynamic construction that is NOT a bound placeholder style.
_DYNAMIC_FSTRING = re.compile(r"\bf[\"']")
_DYNAMIC_FORMAT = re.compile(r"\.format\s*\(|%\s*\(|\$\{")
_DYNAMIC_CONCAT = re.compile(r"""["']\s*\+\s*\w|\w\s*\+\s*["']""")
# psycopg/sqlite bound params - not injection by themselves
_BOUND_PLACEHOLDER = re.compile(r"%\([a-zA-Z_][\w]*\)s|:\w+|\?")

_CODE_EXTS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".sql",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".cs",
}


def _make_finding(
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
        "snippet": (snippet or "")[:200],
        "confidence": confidence,
        "evidence": evidence,
    }


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--`` and ``/* */`` comments so kind/heuristics ignore prose."""
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    lines: list[str] = []
    for line in without_block.splitlines():
        if "--" in line:
            in_single = False
            out: list[str] = []
            index = 0
            while index < len(line):
                char = line[index]
                if char == "'" and not in_single:
                    in_single = True
                    out.append(char)
                elif char == "'" and in_single:
                    in_single = False
                    out.append(char)
                elif char == "-" and not in_single and index + 1 < len(line) and line[index + 1] == "-":
                    break
                else:
                    out.append(char)
                index += 1
            lines.append("".join(out))
        else:
            lines.append(line)
    return "\n".join(lines)


def _kind_of(sql: str) -> str:
    cleaned = _strip_sql_comments(sql)
    match = _SQL_KIND.search(cleaned)
    return match.group(1).upper() if match else "SQL"


def _tables_of(sql: str) -> list[str]:
    cleaned = _strip_sql_comments(sql)
    found: list[str] = []
    seen: set[str] = set()
    for match in _TABLE_FROM.finditer(cleaned):
        name = match.group(1).strip("`\"'[]")
        key = name.lower()
        if key in seen or key in {"select", "as", "on", "set", "values", "if", "exists"}:
            continue
        seen.add(key)
        found.append(name)
        if len(found) >= 8:
            break
    return found


def _looks_like_real_sql(sql: str) -> bool:
    cleaned = _strip_sql_comments(sql).strip()
    if len(cleaned) < 14:
        return False
    if not _SQL_HAS_KEYWORD.search(cleaned):
        return False
    # Reject analyzer token lists / tiny fragments without a table target.
    if len(cleaned) < 40 and not _TABLE_FROM.search(cleaned):
        return False
    return True


def _split_sql_statements(text: str) -> list[tuple[int, str]]:
    """Return (1-based start line, statement) for each non-empty SQL statement."""
    statements: list[tuple[int, str]] = []
    buf: list[str] = []
    start_line = 1
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if not buf and not line.strip():
            continue
        if not buf:
            start_line = idx + 1
        buf.append(line)
        # Split on semicolons outside of simple quotes (good enough for audits).
        joined = "\n".join(buf)
        if ";" not in line:
            continue
        parts = []
        current = []
        in_single = in_double = False
        for char in joined:
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
            if char == ";" and not in_single and not in_double:
                parts.append("".join(current).strip())
                current = []
            else:
                current.append(char)
        trailing = "".join(current).strip()
        for part in parts:
            if part and _looks_like_real_sql(part):
                statements.append((start_line, part))
        buf = [trailing] if trailing else []
        if buf:
            start_line = idx + 1
    leftover = "\n".join(buf).strip()
    if leftover and _looks_like_real_sql(leftover):
        statements.append((start_line, leftover))
    return statements


def _is_dynamic_construction(source_line: str, sql: str) -> bool:
    if _DYNAMIC_FSTRING.search(source_line):
        return True
    if _DYNAMIC_FORMAT.search(source_line) and not _BOUND_PLACEHOLDER.search(sql):
        return True
    if _DYNAMIC_CONCAT.search(source_line):
        return True
    # f-string braces inside the SQL text itself
    if re.search(r"\{[a-zA-Z_][\w.]*\}", sql) and not _BOUND_PLACEHOLDER.search(sql):
        return True
    return False


def _is_migration_path(rel: str) -> bool:
    lowered = rel.replace("\\", "/").lower()
    name = Path(lowered).name
    if name in {"schema.sql", "seed.sql", "init.sql"}:
        return True
    return any(
        part in lowered
        for part in (
            "/migrations/",
            "/alembic/",
            "/flyway/",
            "/liquibase/",
            "/db/migrate",
            "/supabase/",
            "/prisma/migrations/",
            "migration",
        )
    )


def _is_test_path(rel: str) -> bool:
    lowered = rel.replace("\\", "/").lower()
    return (
        "/tests/" in f"/{lowered}"
        or "/test/" in f"/{lowered}"
        or "__tests__" in lowered
        or lowered.startswith("tests/")
        or Path(lowered).name.startswith("test_")
        or lowered.endswith(
            (".test.ts", ".test.tsx", ".test.js", ".spec.ts", ".spec.js", "_test.go", "_test.py")
        )
    )


def _is_self_scanner_path(rel: str) -> bool:
    lowered = rel.replace("\\", "/").lower()
    name = Path(lowered).name
    return name in {"audit_sql.py", "project_audit.py", "audit_ast.py"}


def _analyze_statement(
    *,
    sql: str,
    file: str,
    line: int,
    source_line: str,
    indexed_tables: set[str],
    in_migration: bool,
    in_test: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cleaned = _strip_sql_comments(sql)
    kind = _kind_of(sql)
    tables = _tables_of(sql)
    findings: list[dict[str, Any]] = []
    issues: list[str] = []
    snippet = " ".join(cleaned.split())[:200]

    # Tests / fixtures: keep the catalog, skip security/perf noise.
    emit_findings = not in_test

    dynamic = _is_dynamic_construction(source_line, cleaned)
    if emit_findings and dynamic:
        issues.append("injection")
        findings.append(
            _make_finding(
                finding_id="sql-injection-dynamic",
                severity="critical",
                category="Injection SQL",
                message="Requête SQL construite dynamiquement (injection possible)",
                recommendation="Utiliser des requêtes paramétrées / bind variables (jamais f-string ni concat).",
                file=file,
                line=line,
                snippet=snippet,
                confidence="high",
                evidence="Construction dynamique (f-string, format, concat ou `{var}`) détectée.",
            )
        )

    if emit_findings and kind in {"DELETE", "UPDATE"} and not _WHERE.search(cleaned):
        issues.append("no-where")
        findings.append(
            _make_finding(
                finding_id="sql-no-where",
                severity="high",
                category="Sécurité des données",
                message=f"{kind} sans clause WHERE (impact potentiellement global)",
                recommendation="Ajouter un WHERE explicite ou documenter l'intention (purge totale).",
                file=file,
                line=line,
                snippet=snippet,
                confidence="high",
                evidence=f"Instruction `{kind}` sans `WHERE` après retrait des commentaires.",
            )
        )

    if emit_findings and not in_migration and (_DROP_TABLE.search(cleaned) or _TRUNCATE_TABLE.search(cleaned)):
        issues.append("destructive")
        verb = "DROP TABLE" if _DROP_TABLE.search(cleaned) else "TRUNCATE TABLE"
        findings.append(
            _make_finding(
                finding_id="sql-destructive-app",
                severity="high",
                category="Sécurité des données",
                message=f"{verb} hors migration (destructif dans le code applicatif)",
                recommendation="Réserver DROP/TRUNCATE TABLE aux migrations versionnées, jamais au runtime.",
                file=file,
                line=line,
                snippet=snippet,
                confidence="medium",
                evidence="Instruction destructive hors chemin migration/supabase/alembic.",
            )
        )

    if emit_findings and _SELECT_STAR.search(cleaned):
        issues.append("select-star")
        findings.append(
            _make_finding(
                finding_id="sql-select-star",
                severity="low",
                category="Performance SQL",
                message="SELECT * (colonnes non bornées, I/O et payload excessifs)",
                recommendation="Lister explicitement les colonnes nécessaires.",
                file=file,
                line=line,
                snippet=snippet,
                confidence="high",
                evidence="Motif `SELECT * FROM` dans la requête.",
            )
        )

    if emit_findings and _LIKE_LEADING.search(cleaned):
        issues.append("like-leading")
        findings.append(
            _make_finding(
                finding_id="sql-like-leading",
                severity="medium",
                category="Performance SQL",
                message="LIKE/ILIKE avec joker en tête (`%…`) - index B-tree inutilisable",
                recommendation="Éviter le `%` initial, utiliser un trigram/FTS, ou borner le préfixe.",
                file=file,
                line=line,
                snippet=snippet,
                confidence="high",
                evidence="Literal `LIKE '%…'` / `ILIKE '%…'` détecté.",
            )
        )

    if emit_findings and kind == "SELECT" and not _LIMIT.search(cleaned) and _ORDER_BY.search(cleaned):
        issues.append("unbounded-sort")
        findings.append(
            _make_finding(
                finding_id="sql-unbounded-order",
                severity="low",
                category="Performance SQL",
                message="SELECT … ORDER BY sans LIMIT (tri potentiellement coûteux)",
                recommendation="Ajouter LIMIT/OFFSET ou une pagination clé.",
                file=file,
                line=line,
                snippet=snippet,
                confidence="medium",
                evidence="ORDER BY présent, LIMIT absent.",
            )
        )

    if emit_findings and kind == "SELECT" and tables and indexed_tables:
        missing = [t for t in tables if t.lower() not in indexed_tables]
        if missing and indexed_tables and len(tables) == 1:
            if _WHERE.search(cleaned) or _ORDER_BY.search(cleaned):
                findings.append(
                    _make_finding(
                        finding_id="sql-missing-index-hint",
                        severity="low",
                        category="Performance SQL",
                        message=f"Table `{tables[0]}` filtrée/triée sans CREATE INDEX détecté dans le dépôt",
                        recommendation="Vérifier le plan d'exécution et ajouter un index si la requête est chaude.",
                        file=file,
                        line=line,
                        snippet=snippet,
                        confidence="low",
                        evidence=(
                            "Heuristique dépôt : aucun CREATE INDEX sur cette table "
                            "alors que WHERE/ORDER BY est présent."
                        ),
                    )
                )

    risk_reasons = {
        "injection": "Construction dynamique - risque d'injection SQL",
        "no-where": f"{kind} sans WHERE",
        "destructive": "DROP/TRUNCATE TABLE hors migration",
        "select-star": "SELECT *",
        "like-leading": "LIKE avec % en tête",
        "unbounded-sort": "ORDER BY sans LIMIT",
        "index-hint": "Index manquant possible",
    }
    query = {
        "file": file,
        "line": line,
        "kind": kind,
        "sql": sql.strip()[:1200],
        "tables": tables,
        "parameterized": bool(_BOUND_PLACEHOLDER.search(cleaned)) and not dynamic,
        "source": "sql-file" if file.lower().endswith(".sql") else "embedded",
        "risky": bool(issues),
        "risk_reason": " · ".join(risk_reasons[i] for i in issues if i in risk_reasons),
        "issue_ids": [f["id"] for f in findings],
    }
    return query, findings


def extract_sql_audit(
    files: list[Any],
    *,
    load_text: Callable[[Any], str],
) -> dict[str, Any]:
    """Build SQL catalog + security/performance findings across the project."""
    queries: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    by_kind: dict[str, int] = {}
    indexed_tables: set[str] = set()
    files_with_sql = 0
    by_source: dict[str, int] = {"sql-file": 0, "embedded": 0}

    # First pass: collect CREATE INDEX targets for soft index hints.
    for file in files:
        ext = getattr(file, "ext", "")
        if ext not in _CODE_EXTS:
            continue
        text = load_text(file)
        if not text or "CREATE" not in text.upper():
            continue
        for match in _CREATE_INDEX.finditer(text):
            indexed_tables.add(match.group(1).strip("`\"'[]").lower())

    for file in files:
        ext = getattr(file, "ext", "")
        rel = str(getattr(file, "rel", ""))
        if ext not in _CODE_EXTS:
            continue
        if _is_self_scanner_path(rel):
            continue
        if _is_test_path(rel):
            continue
        text = load_text(file)
        if not text:
            continue
        upper = text.upper()
        if not any(
            token in upper
            for token in (
                "SELECT ",
                "INSERT INTO",
                "UPDATE ",
                "DELETE FROM",
                "CREATE TABLE",
                "CREATE INDEX",
                "DROP TABLE",
                "TRUNCATE ",
            )
        ):
            continue

        in_migration = _is_migration_path(rel)
        lines = text.splitlines()
        local_statements: list[tuple[int, str, str]] = []

        if ext == ".sql":
            for start_line, stmt in _split_sql_statements(text):
                local_statements.append(
                    (start_line, stmt, lines[start_line - 1] if start_line <= len(lines) else "")
                )
        else:
            for idx, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith("//"):
                    continue
                if "re.compile(" in line or "_SQL_" in line:
                    continue
                match = _EMBEDDED_SQL_START.search(line)
                if not match:
                    continue
                quote = match.group("q")
                block_lines = [line[match.start("body") :]]
                closed = line.count(quote) >= (2 if len(quote) == 1 else 2)
                if not closed:
                    for extra in lines[idx + 1 : idx + 12]:
                        block_lines.append(extra)
                        if quote in extra:
                            break
                raw = "\n".join(block_lines)
                raw = re.split(r"""["']{1,3}\s*[,)]?$""", raw, maxsplit=1)[0]
                raw = raw.strip().rstrip("\"'")
                if not _looks_like_real_sql(raw):
                    continue
                local_statements.append((idx + 1, raw, line))

        if not local_statements:
            continue

        files_with_sql += 1
        for start_line, stmt, source_line in local_statements:
            query, stmt_findings = _analyze_statement(
                sql=stmt,
                file=rel,
                line=start_line,
                source_line=source_line,
                indexed_tables=indexed_tables,
                in_migration=in_migration,
                in_test=False,
            )
            by_kind[query["kind"]] = by_kind.get(query["kind"], 0) + 1
            by_source[query["source"]] = by_source.get(query["source"], 0) + 1
            queries.append(query)
            for finding in stmt_findings:
                if len(findings) < _MAX_FINDINGS:
                    findings.append(finding)
            if len(queries) >= _MAX_QUERIES:
                break
        if len(queries) >= _MAX_QUERIES:
            break

    summary = {level: 0 for level in ("critical", "high", "medium", "low")}
    scored = 0
    for finding in findings:
        if finding.get("confidence") == "low":
            continue
        summary[finding["severity"]] = summary.get(finding["severity"], 0) + 1
        scored += 1

    penalty = 0.0
    for level, weight in (("critical", 18), ("high", 8), ("medium", 3), ("low", 1)):
        count = summary.get(level, 0)
        if count:
            penalty += weight * (count ** 0.7)
    score = max(5, round(100 - penalty)) if findings else 100

    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    findings.sort(key=lambda f: (order.get(f["severity"], 9), f["file"], f["line"]))

    return {
        "total": len(queries),
        "by_kind": by_kind,
        "by_source": by_source,
        "queries": queries,
        "files_scanned": files_with_sql,
        "indexed_tables": sorted(indexed_tables)[:40],
        "findings": findings,
        "summary": summary,
        "score": score,
        "scored_findings": scored,
        "engine": "sql-audit",
    }
