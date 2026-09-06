---
name: sql-analyst
description: Translate business questions into SQL, validate results, and explain assumptions. Use for analytics, reconciliations, and ad-hoc reporting.
metadata: {"navin":{"emoji":"📊","category":"data"}}
---

# SQL Analyst

## Overview

Question → SQL → verified answer. Show the query and caveats.

## Tools

Run SQL through the built-in `db_query` tool (SQLite files via `sqlite_path`, or named PostgreSQL/Supabase/MySQL/MariaDB connections from `tools.database.connections`). Results come back as a table with a row cap - refine queries instead of dumping tables.

## Workflow

1. Clarify metrics definitions (what counts as “active”, timezone, etc.).
2. Inspect schema via `database-explorer` habits.
3. Write readable SQL (CTEs over nested mess when helpful).
4. Run with limits first; then aggregate.
5. Sanity-check totals against a second query or known benchmark.
6. Present: answer, SQL, assumptions, data freshness.

## Rules

- No `UPDATE`/`DELETE` unless explicitly requested and approved.
- Watch for fan-out joins that inflate metrics.
