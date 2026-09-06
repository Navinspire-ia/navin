---
name: test-generator
description: Generate and run unit, API, and E2E tests (including Playwright) that match project conventions. Use when adding coverage or reproducing a bug with a failing test first.
metadata: {"navin":{"emoji":"✅","category":"devops"}}
---

# Test Generator

## Overview

Write tests the project already understands (pytest, vitest, jest, playwright). Prefer failing test → fix → green.

## Workflow

1. Detect the test runner and layout (`package.json`, `pyproject.toml`, existing tests).
2. Choose level:
   - unit for pure logic
   - API/integration for handlers
   - E2E for critical user paths
3. Add a focused test next to siblings; mirror naming/fixtures.
4. Run the smallest command that exercises the new test.
5. Iterate until green; avoid flaky sleeps - use explicit waits.

## Rules

- Do not snapshot huge blobs without need.
- Keep tests deterministic; inject clocks/network.
- For UI E2E, reuse `playwright-browser` guidance.
