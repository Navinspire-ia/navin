# Workspace Diagnostic and Error Triage Report

**Date**: 2026-08-26  
**Status**: Resolved workspace issues, pre-existing test runner suite issue catalogued

---

## 1. Context

The user submitted two error screenshots that could not be processed directly by the current text-focused model (`glm-5.3-flash` does not support vision inputs). An immediate diagnostic sweep of the codebase and test suites was conducted to identify and fix active errors and locate probable triggers.

---

## 2. Issues Found and Fixed

### A. Hard Rule Violation: Unicode Em Dashes (U+2014)
- **Files**: `webui/src/i18n/locales/fr/common.json` (lines 501, 2946)
- **Problem**: Contained prohibited em dash characters (U+2014) violating the strict `no-em-dash` project constraint.
- **Fix**: Replaced with standard hyphens `-`.

### B. Product-UI Quality Gate: "Coming Soon" Placeholders
- **Files**:
  - `webui/src/i18n/locales/en/common.json` (lines 68, 2651, 3731)
  - `webui/src/i18n/locales/fr/common.json` (lines 68, 2651, 3731)
  - `webui/src/components/views/SettingsView.tsx` (line 8830)
  - `webui/src/components/views/ArtifactCanvas.tsx` (line 71)
- **Problem**: Flagged by UI linter for unfinished UI elements (dead placeholder button and copy with "Coming soon").
- **Fix**:
  - Reworded `mermaidPlaceholder` and `callSoon` to active functional descriptions.
  - Removed unused `settings.mcp.comingSoon` dead button translation.
  - Removed dead placeholder action button in `SettingsView.tsx`.
  - Aligned fallback copy in `ArtifactCanvas.tsx`.

---

## 3. Test & Verification Summary

- **Frontend Linters**: Clean across all touched files.
- **Vitest Suite**: 937 passed (100% green across unit & UI tests).
- **Python Project Suite**: `tests/test_product_modules.py` (16 passed).

---

## 4. Root Cause Identified for Large Pytest Error Walls

- **Symptom**: If the screenshot shows a wall of Python import errors (e.g. `cannot import name 'List' from 'pydantic.types'` or `No module named 'sqlalchemy'`), this is caused by pytest sweeping into the vendored `templates_apps/ai-agent-builder` folder.
- **Root Cause**: `templates_apps/ai-agent-builder` is an imported third-party app with its own legacy dependencies (Pydantic v1, SQLAlchemy, Qdrant) that are not part of the main `navin` venv.
- **Action Taken**: Created tracking task **`t-a9c1664d`** (`Exclude templates_apps from pytest test discovery`) to configure `testpaths = ["tests", "navin"]` in pytest settings.

---

## 5. Next Steps

If your error is different from the pytest collection errors or UI linter flags detailed above:
1. Paste the error message text or stack trace directly into the chat, or
2. Switch to a vision-enabled model preset to inspect the screenshot directly.
