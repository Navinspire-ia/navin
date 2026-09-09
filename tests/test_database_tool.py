# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the database tool's read-only enforcement (navin.agent.tools.database).

The SQL prefix check upstream classifies WITH as read-only, but MySQL 8 accepts
WITH ... DELETE/UPDATE. SQLite opens with mode=ro and Postgres sets
conn.read_only, so MySQL was the one engine where allow_writes=false relied on
string inspection alone; the server must enforce it.
"""

from __future__ import annotations

import sys
import unittest
from types import ModuleType
from unittest import mock

from navin.agent.tools.database import _run_mysql

_READ_ONLY_STMT = "SET SESSION TRANSACTION READ ONLY"


def _fake_pymysql(recorder: list[str]) -> tuple[ModuleType, mock.MagicMock]:
    """A pymysql stand-in whose cursors record every statement executed."""
    module = ModuleType("pymysql")
    cursor = mock.MagicMock()
    cursor.description = [("id",)]
    cursor.fetchmany.return_value = [(1,)]
    cursor.rowcount = -1
    cursor.execute.side_effect = lambda q, *a, **k: recorder.append(q)
    cursor_cm = mock.MagicMock()
    cursor_cm.__enter__.return_value = cursor
    cursor_cm.__exit__.return_value = False
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor_cm
    module.connect = mock.MagicMock(return_value=conn)  # type: ignore[attr-defined]
    return module, conn


class MySqlReadOnlyGuardTest(unittest.TestCase):
    def _run(self, *, allow_writes: bool) -> tuple[list[str], mock.MagicMock]:
        statements: list[str] = []
        module, conn = _fake_pymysql(statements)
        with mock.patch.dict(sys.modules, {"pymysql": module}):
            _run_mysql(
                "mysql://user:pw@localhost/db",
                "WITH doomed AS (SELECT 1) SELECT * FROM doomed",
                [],
                max_rows=10,
                allow_writes=allow_writes,
                timeout=5.0,
            )
        return statements, conn

    def test_a_read_only_session_is_declared_before_the_query(self) -> None:
        statements, conn = self._run(allow_writes=False)
        self.assertEqual(statements[0], _READ_ONLY_STMT)
        # The attribute only applies to transactions, so one must be open.
        conn.begin.assert_called_once()
        conn.commit.assert_not_called()

    def test_a_writable_connection_is_left_alone(self) -> None:
        statements, conn = self._run(allow_writes=True)
        self.assertNotIn(_READ_ONLY_STMT, statements)
        conn.begin.assert_not_called()
        conn.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
