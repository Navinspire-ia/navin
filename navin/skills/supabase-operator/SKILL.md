---
name: supabase-operator
description: Administer Supabase tables, migrations, storage, functions, and RLS policies. Prefer MCP Supabase or CLI when configured.
metadata: {"navin":{"emoji":"⚡","category":"data"}}
---

# Supabase Operator

## Overview

Treat RLS as part of the product. Schema changes go through migrations.

## Tools

For SQL, use the built-in `db_query` tool with a connection configured as `{"engine": "supabase", "url": "<Supabase Postgres connection string>"}` under `tools.database.connections` (read-only unless `allowWrites`). Use the Supabase CLI or MCP server for migrations, storage, and edge functions.

## Workflow

1. Confirm project ref / linked CLI or MCP.
2. Inspect tables, policies, and functions relevant to the task.
3. For schema changes: write a migration; never hot-edit prod casually.
4. Verify RLS: who can select/insert/update/delete.
5. Storage buckets: check public vs private; signed URLs for private objects.

## Rules

- Service-role keys are secrets - never expose to clients or chat.
- Policy tightening/loosening needs review (`human-approval` on prod).
