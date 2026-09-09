# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Vision 360 SQL auditor: multi-file catalog + security/perf findings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navin.webui.audit_sql import extract_sql_audit


class _F:
    def __init__(self, rel: str, text: str) -> None:
        self.rel = rel
        self.ext = Path(rel).suffix.lower() or (".sql" if rel.endswith(".sql") else "")
        self._text = text

    def __repr__(self) -> str:
        return self.rel


def _load(file: Any) -> str:
    return file._text


def test_sql_catalog_spans_multiple_files(tmp_path: Path) -> None:
    files = [
        _F(
            "app/queries.py",
            'SQL = "SELECT id, name FROM users WHERE id = ?"\n',
        ),
        _F(
            "migrations/001_init.sql",
            "CREATE TABLE users (id INT PRIMARY KEY, name TEXT);\n"
            "CREATE INDEX idx_users_name ON users (name);\n",
        ),
        _F(
            "supabase/seed.sql",
            "INSERT INTO users (id, name) VALUES (1, 'a');\n",
        ),
    ]
    result = extract_sql_audit(files, load_text=_load)
    assert result["files_scanned"] >= 2
    assert result["total"] >= 3
    assert len({q["file"] for q in result["queries"]}) >= 2
    assert result["engine"] == "sql-audit"


def test_sql_flags_injection_and_no_where() -> None:
    files = [
        _F(
            "app/bad.py",
            'q = f"SELECT * FROM users WHERE name = \'{name}\'"\n'
            'wipe = "DELETE FROM users"\n',
        ),
    ]
    result = extract_sql_audit(files, load_text=_load)
    ids = {f["id"] for f in result["findings"]}
    assert "sql-injection-dynamic" in ids
    assert "sql-no-where" in ids
    assert "sql-select-star" in ids
    assert result["score"] < 100


def test_sql_skips_migrations_destructive_and_tests() -> None:
    files = [
        _F(
            "site/supabase/2026-01-01-reset.sql",
            "DROP TABLE IF EXISTS public.old;\n"
            "CREATE TABLE public.old (id INT);\n",
        ),
        _F(
            "tests/test_db.py",
            'SQL = f"SELECT * FROM t WHERE x = \'{v}\'"\n',
        ),
    ]
    result = extract_sql_audit(files, load_text=_load)
    ids = {f["id"] for f in result["findings"]}
    assert "sql-destructive-app" not in ids
    assert "sql-injection-dynamic" not in ids


def test_sql_ignores_comment_keywords_and_drop_constraint() -> None:
    files = [
        _F(
            "site/supabase/fix.sql",
            "-- webhook UPDATE fails otherwise\n"
            "ALTER TABLE public.subscriptions\n"
            "  DROP CONSTRAINT IF EXISTS subscriptions_plan_check;\n",
        ),
    ]
    result = extract_sql_audit(files, load_text=_load)
    assert all(q["kind"] == "ALTER" for q in result["queries"])
    assert not any(f["id"] == "sql-no-where" for f in result["findings"])
    assert not any(f["id"] == "sql-destructive-app" for f in result["findings"])


def test_sql_perf_like_and_order_without_limit() -> None:
    files = [
        _F(
            "app/search.py",
            'Q = "SELECT id FROM items WHERE name LIKE \'%foo%\' ORDER BY created_at"\n',
        ),
    ]
    result = extract_sql_audit(files, load_text=_load)
    ids = {f["id"] for f in result["findings"]}
    assert "sql-like-leading" in ids
    assert "sql-unbounded-order" in ids
