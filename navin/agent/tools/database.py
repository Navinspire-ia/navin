"""Database connector tool: query SQLite, PostgreSQL, MySQL/MariaDB, and Supabase."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from pydantic import Field

from navin.agent.tools.base import Tool, ToolResult, tool_parameters
from navin.agent.tools.schema import (
    IntegerSchema,
    StringSchema,
    tool_parameters_schema,
)
from navin.config_base import Base
from navin.security.workspace_access import current_tool_workspace
from navin.security.workspace_policy import WorkspaceBoundaryError, resolve_allowed_path

_SUPPORTED_ENGINES = ("sqlite", "postgres", "supabase", "mysql", "mariadb")

# Statements considered read-only. Everything else requires allow_writes.
_READ_ONLY_PREFIXES = (
    "select", "with", "show", "describe", "desc", "explain", "pragma", "values",
)

_MAX_CELL_CHARS = 200
_DEFAULT_MAX_ROWS = 100
_HARD_MAX_ROWS = 1000
_DEFAULT_TIMEOUT_S = 30.0


class DatabaseConnectionConfig(Base):
    """One named database connection."""

    engine: str = "sqlite"  # sqlite | postgres | supabase | mysql | mariadb
    # DSN/URL for server engines (postgres://..., mysql://...); Supabase uses
    # its Postgres connection string. For sqlite, use `path` instead.
    url: str = ""
    path: str = ""  # sqlite file path (absolute or workspace-relative)
    allow_writes: bool = False


class DatabaseToolConfig(Base):
    """Database tool configuration."""

    enabled: bool = True
    connections: dict[str, DatabaseConnectionConfig] = Field(default_factory=dict)
    max_rows: int = Field(default=_DEFAULT_MAX_ROWS, ge=1, le=_HARD_MAX_ROWS)
    timeout_seconds: float = Field(default=_DEFAULT_TIMEOUT_S, ge=1, le=300)


class DatabaseToolError(Exception):
    pass


def _is_read_only_sql(query: str) -> bool:
    stripped = re.sub(r"^\s*(--[^\n]*\n|/\*.*?\*/\s*)*", "", query, flags=re.DOTALL).strip()
    first = stripped.split(None, 1)[0].lower() if stripped else ""
    return first in _READ_ONLY_PREFIXES


def _truncate_cell(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    text = str(value)
    if len(text) > _MAX_CELL_CHARS:
        return text[: _MAX_CELL_CHARS - 1] + "…"
    return text


def _format_rows(columns: list[str], rows: list[tuple], *, truncated: bool) -> str:
    if not columns:
        return "Statement executed."
    lines = [" | ".join(columns), " | ".join("---" for _ in columns)]
    for row in rows:
        lines.append(" | ".join(_truncate_cell(cell) for cell in row))
    out = "\n".join(lines)
    summary = f"\n\n{len(rows)} row(s)"
    if truncated:
        summary += " (truncated — refine the query or raise max_rows)"
    return out + summary


def _parse_params(raw: str | None) -> list[Any]:
    if not raw or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DatabaseToolError(f"params must be a JSON array: {exc}") from exc
    if not isinstance(parsed, list):
        raise DatabaseToolError("params must be a JSON array")
    return parsed


def _run_sqlite(
    path: str,
    query: str,
    params: list[Any],
    *,
    max_rows: int,
    allow_writes: bool,
    timeout: float,
) -> tuple[list[str], list[tuple], bool, int]:
    import sqlite3

    uri = f"file:{path}?mode={'rw' if allow_writes else 'ro'}"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout)
    try:
        cursor = conn.execute(query, params)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(max_rows + 1) if columns else []
        if allow_writes:
            conn.commit()
        rowcount = cursor.rowcount if cursor.rowcount is not None else -1
        return columns, [tuple(r) for r in rows[:max_rows]], len(rows) > max_rows, rowcount
    finally:
        conn.close()


def _run_postgres(
    url: str,
    query: str,
    params: list[Any],
    *,
    max_rows: int,
    allow_writes: bool,
    timeout: float,
) -> tuple[list[str], list[tuple], bool, int]:
    try:
        import psycopg
    except ImportError as exc:
        raise DatabaseToolError(
            "PostgreSQL/Supabase support requires the 'psycopg[binary]' package. "
            "Install it with: pip install 'psycopg[binary]'"
        ) from exc

    with psycopg.connect(url, connect_timeout=int(timeout), autocommit=False) as conn:
        if not allow_writes:
            conn.read_only = True
        with conn.cursor() as cursor:
            cursor.execute(query, params or None)
            columns = [d.name for d in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(max_rows + 1) if columns else []
            if allow_writes:
                conn.commit()
            rowcount = cursor.rowcount if cursor.rowcount is not None else -1
            return columns, [tuple(r) for r in rows[:max_rows]], len(rows) > max_rows, rowcount


def _run_mysql(
    url: str,
    query: str,
    params: list[Any],
    *,
    max_rows: int,
    allow_writes: bool,
    timeout: float,
) -> tuple[list[str], list[tuple], bool, int]:
    try:
        import pymysql
    except ImportError as exc:
        raise DatabaseToolError(
            "MySQL/MariaDB support requires the 'PyMySQL' package. "
            "Install it with: pip install PyMySQL"
        ) from exc
    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    conn = pymysql.connect(
        host=parsed.hostname or "127.0.0.1",
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=parsed.path.lstrip("/") or None,
        connect_timeout=int(timeout),
        read_timeout=int(timeout),
        write_timeout=int(timeout),
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(query, params or None)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchmany(max_rows + 1) if columns else []
            if allow_writes:
                conn.commit()
            rowcount = cursor.rowcount if cursor.rowcount is not None else -1
            return columns, [tuple(r) for r in rows[:max_rows]], len(rows) > max_rows, rowcount
    finally:
        conn.close()


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema(
            "SQL statement to execute. Read-only statements (SELECT, SHOW, EXPLAIN, "
            "PRAGMA, DESCRIBE, WITH) run on any connection; writes require a "
            "connection configured with allowWrites.",
            min_length=1,
        ),
        connection=StringSchema(
            "Name of a configured connection from tools.database.connections. "
            "Omit to query a local SQLite file via sqlite_path.",
        ),
        sqlite_path=StringSchema(
            "Path to a local SQLite database file (workspace-relative or absolute) "
            "when no named connection is used.",
        ),
        params=StringSchema(
            'Optional JSON array of query parameters, e.g. ["value", 42]. '
            "Use placeholders (? for SQLite/MySQL, %s for PostgreSQL).",
        ),
        max_rows=IntegerSchema(
            description="Maximum rows to return (default from config, hard cap 1000).",
            minimum=1,
            maximum=_HARD_MAX_ROWS,
        ),
        required=["query"],
    )
)
class DatabaseTool(Tool):
    """Query configured databases (SQLite, PostgreSQL, MySQL/MariaDB, Supabase)."""

    config_key = "database"

    @classmethod
    def config_cls(cls):
        return DatabaseToolConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.database.enabled

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(workspace=ctx.workspace, config=ctx.config.database)

    def __init__(self, *, workspace: str | Path, config: DatabaseToolConfig) -> None:
        self.workspace = Path(workspace).expanduser()
        self.config = config

    @property
    def name(self) -> str:
        return "db_query"

    @property
    def description(self) -> str:
        names = ", ".join(sorted(self.config.connections)) or "none configured"
        return (
            "Run SQL against a database. Supports SQLite files in the workspace and "
            "named connections (PostgreSQL, Supabase, MySQL, MariaDB) from "
            f"tools.database.connections (available: {names}). Read-only by default; "
            "writes only on connections with allowWrites=true."
        )

    def _resolve_sqlite_path(self, raw: str) -> str:
        access = current_tool_workspace(self.workspace, restrict_to_workspace=True)
        workspace = access.project_path or self.workspace
        try:
            resolved = resolve_allowed_path(
                raw,
                workspace=workspace,
                allowed_root=access.allowed_root,
                strict=True,
            )
        except WorkspaceBoundaryError as exc:
            raise DatabaseToolError("sqlite_path must stay inside the workspace") from exc
        except OSError as exc:
            raise DatabaseToolError(f"sqlite database not found: {raw}") from exc
        if not resolved.is_file():
            raise DatabaseToolError(f"sqlite database not found: {raw}")
        return str(resolved)

    async def execute(
        self,
        query: str,
        connection: str | None = None,
        sqlite_path: str | None = None,
        params: str | None = None,
        max_rows: int | None = None,
        **kwargs: Any,
    ) -> str:
        limit = min(max_rows or self.config.max_rows, _HARD_MAX_ROWS)
        timeout = self.config.timeout_seconds
        try:
            bound_params = _parse_params(params)

            if connection:
                conn_cfg = self.config.connections.get(connection)
                if conn_cfg is None:
                    known = ", ".join(sorted(self.config.connections)) or "none"
                    return ToolResult.error(
                        f"Error: unknown connection '{connection}' (configured: {known})"
                    )
                engine = conn_cfg.engine.strip().lower()
                if engine not in _SUPPORTED_ENGINES:
                    return ToolResult.error(f"Error: unsupported engine '{conn_cfg.engine}'")
                allow_writes = conn_cfg.allow_writes
                if not allow_writes and not _is_read_only_sql(query):
                    return ToolResult.error(
                        "Error: this connection is read-only. Set allowWrites=true on "
                        f"tools.database.connections.{connection} to allow writes."
                    )
                if engine == "sqlite":
                    target = conn_cfg.path or conn_cfg.url
                    if not target:
                        return ToolResult.error("Error: sqlite connection needs a path")
                    resolved = self._resolve_sqlite_path(target)
                    runner = lambda: _run_sqlite(  # noqa: E731
                        resolved, query, bound_params,
                        max_rows=limit, allow_writes=allow_writes, timeout=timeout,
                    )
                elif engine in ("postgres", "supabase"):
                    if not conn_cfg.url:
                        return ToolResult.error("Error: connection needs a url (DSN)")
                    runner = lambda: _run_postgres(  # noqa: E731
                        conn_cfg.url, query, bound_params,
                        max_rows=limit, allow_writes=allow_writes, timeout=timeout,
                    )
                else:  # mysql / mariadb
                    if not conn_cfg.url:
                        return ToolResult.error("Error: connection needs a url (DSN)")
                    runner = lambda: _run_mysql(  # noqa: E731
                        conn_cfg.url, query, bound_params,
                        max_rows=limit, allow_writes=allow_writes, timeout=timeout,
                    )
            elif sqlite_path:
                if not _is_read_only_sql(query):
                    return ToolResult.error(
                        "Error: ad-hoc sqlite_path queries are read-only. Configure a "
                        "named connection with allowWrites=true for writes."
                    )
                resolved = self._resolve_sqlite_path(sqlite_path)
                runner = lambda: _run_sqlite(  # noqa: E731
                    resolved, query, bound_params,
                    max_rows=limit, allow_writes=False, timeout=timeout,
                )
            else:
                known = ", ".join(sorted(self.config.connections)) or "none"
                return ToolResult.error(
                    "Error: provide either a named connection or sqlite_path "
                    f"(configured connections: {known})"
                )

            columns, rows, truncated, rowcount = await asyncio.wait_for(
                asyncio.to_thread(runner), timeout=timeout + 5,
            )
            if not columns and rowcount >= 0:
                return f"Statement executed. {rowcount} row(s) affected."
            return _format_rows(columns, rows, truncated=truncated)
        except DatabaseToolError as exc:
            return ToolResult.error(f"Error: {exc}")
        except asyncio.TimeoutError:
            return ToolResult.error(f"Error: query timed out after {timeout:.0f}s")
        except Exception as exc:  # driver-specific errors (OperationalError, etc.)
            return ToolResult.error(f"Error: {type(exc).__name__}: {exc}")
