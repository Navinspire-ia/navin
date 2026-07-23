---
name: database-explorer
description: Explore PostgreSQL, MySQL, MongoDB, Redis, and SQLite schemas and sample data safely (read-first). Use when understanding data models or diagnosing query issues.
metadata: {"navin":{"emoji":"🗄️","category":"data"}}
---

# Database Explorer

## Overview

Map schemas before writing queries. Prefer read-only roles and limits.

## Tools

Prefer the built-in `db_query` tool: it queries SQLite files in the workspace directly (`sqlite_path`) and named connections (PostgreSQL, Supabase, MySQL, MariaDB) configured under `tools.database.connections`. Connections are read-only unless `allowWrites` is set. Fall back to CLI clients (psql, mysql, sqlite3) or MCP servers only when a connection is not configured.

## Workflow

1. Identify engine + connection method (`db_query` connection, CLI, URL in env, MCP Supabase, etc.).
2. List databases/schemas/tables — never dump entire prod tables.
3. Sample with `LIMIT` / projections; avoid `SELECT *` on huge tables.
4. Summarize: entities, keys, relations, row-count estimates.
5. Flag PII columns; do not paste sensitive rows into chat.

## Rules

- Writes/DDL on shared DBs need `human-approval`.
- Credentials from env/secret stores only (`secrets-manager`).
