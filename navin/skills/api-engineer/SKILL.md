---
name: api-engineer
description: Read OpenAPI specs, call APIs, validate responses, and draft connectors. Use for integration work, contract testing, and client generation.
metadata: {"navin":{"emoji":"🔌","category":"devops"}}
---

# API Engineer

## Overview

Treat OpenAPI/Swagger as the contract. Explore safely, then automate.

## Workflow

1. Locate the spec (`openapi.yaml`, Swagger URL).
2. Map auth (bearer, basic, OAuth) — pull secrets from env.
3. Smoke-call read endpoints first (`web_fetch` / `exec curl` / dedicated tools).
4. Validate status codes and schema fields the user cares about.
5. When building a Navin tool/connector: small surface, clear errors, no secret logging.

## Rules

- Never commit tokens.
- Prefer idempotent GETs for exploration.
- Document rate limits and pagination when found.
