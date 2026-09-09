# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""OCR-style five-gate file filter for Review mode.

Gate data (supported extensions + default test excludes) is loaded from the
OCR allowlist JSON files embedded under ``navin/review/data/``.
"""

from __future__ import annotations

import fnmatch
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent / "data"

_BINARY_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".tgz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".mp3",
        ".mp4",
        ".webm",
        ".wasm",
        ".lock",
        ".bin",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".class",
        ".o",
        ".a",
        ".jar",
        ".pyc",
        ".pyo",
    }
)

_SKIP_PARTS = (
    "/node_modules/",
    "/.venv/",
    "/venv/",
    "/dist/",
    "/build/",
    "/.git/",
    "/vendor/",
    "/coverage/",
    "/__pycache__/",
    "/.next/",
    "/.turbo/",
    "/target/",
    "/site-packages/",
    "/.gradle/",
    "/.idea/",
    "/.tox/",
)

_BRACE_RE = re.compile(r"\{([^{}]+)\}")


@lru_cache(maxsize=1)
def _load_supported_exts() -> frozenset[str]:
    path = _DATA_DIR / "supported_file_types.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError(f"invalid allowlist: {path}")
    return frozenset(str(x).lower() for x in raw if isinstance(x, str))


@lru_cache(maxsize=1)
def _load_default_path_patterns() -> tuple[str, ...]:
    path = _DATA_DIR / "default_exclude_patterns.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RuntimeError(f"invalid exclude patterns: {path}")
    return tuple(str(x) for x in raw if isinstance(x, str))


def expand_glob(pattern: str) -> list[str]:
    """Expand simple brace globs: ``*.{ts,tsx}`` → ``['*.ts', '*.tsx']``."""
    match = _BRACE_RE.search(pattern)
    if not match:
        return [pattern]
    options = match.group(1).split(",")
    prefix = pattern[: match.start()]
    suffix = pattern[match.end() :]
    out: list[str] = []
    for opt in options:
        out.extend(expand_glob(f"{prefix}{opt.strip()}{suffix}"))
    return out or [pattern]


def path_matches(rel: str, pattern: str) -> bool:
    """Case-insensitive path match with ``**`` and brace expansion.

    ``fnmatch`` alone does not treat ``**`` as cross-directory; we expand it.
    """
    norm = rel.replace("\\", "/").lstrip("./").lower()
    for expanded in expand_glob(pattern):
        pat = expanded.replace("\\", "/").lower()
        if _glob_match(norm, pat):
            return True
        if "/" not in pat.rstrip("*") and fnmatch.fnmatch(Path(norm).name, pat):
            return True
    return False


def _glob_match(path: str, pattern: str) -> bool:
    """Match ``path`` against a glob that may contain ``**``."""
    if "**" not in pattern:
        return fnmatch.fnmatch(path, pattern)
    if pattern == "**":
        return True
    regex = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            regex += "(?:.*/)?"
            i += 3
            continue
        if pattern.startswith("**", i):
            regex += ".*"
            i += 2
            continue
        ch = pattern[i]
        if ch == "*":
            regex += "[^/]*"
        elif ch == "?":
            regex += "[^/]"
        elif ch in ".^$+{}[]|()\\":
            regex += "\\" + ch
        else:
            regex += ch
        i += 1
    return re.fullmatch(regex, path) is not None


def _ext(rel: str) -> str:
    name = Path(rel).name.lower()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return ".dockerfile"
    return Path(rel).suffix.lower()


def gate_file(
    rel: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    skip_default_test_paths: bool = True,
) -> tuple[bool, str | None]:
    """Apply OCR five-gates. Return (keep, drop_reason)."""
    norm = "/" + rel.replace("\\", "/").lstrip("/")
    lower = rel.replace("\\", "/").lower()

    if any(part in norm for part in _SKIP_PARTS):
        return False, "vendor_path"

    # Gate 1: binary
    if _ext(rel) in _BINARY_EXTS:
        return False, "binary"

    # Gate 2: user_exclude
    for pattern in exclude or []:
        if path_matches(lower, pattern):
            return False, "user_exclude"

    # Gate 3: user_include → keep immediately (bypass 4+5)
    for pattern in include or []:
        if path_matches(lower, pattern):
            return True, None

    # Gate 4: unsupported_ext (OCR allowlist)
    ext = _ext(rel)
    supported = _load_supported_exts()
    if ext and ext not in supported:
        if "." in Path(rel).name:
            return False, "unsupported_ext"

    # Gate 5: default_path (OCR test excludes)
    if skip_default_test_paths:
        for pattern in _load_default_path_patterns():
            if path_matches(lower, pattern):
                return False, "default_path"

    return True, None


def apply_file_gates(
    files: list[str],
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    skip_default_test_paths: bool = True,
) -> dict[str, Any]:
    kept: list[str] = []
    dropped: list[dict[str, str]] = []
    for rel in files:
        ok, reason = gate_file(
            rel,
            include=include,
            exclude=exclude,
            skip_default_test_paths=skip_default_test_paths,
        )
        if ok:
            kept.append(rel)
        else:
            dropped.append({"path": rel, "reason": reason or "excluded"})
    return {
        "files": kept,
        "dropped": dropped,
        "kept_count": len(kept),
        "dropped_count": len(dropped),
        "allowlist_source": "navin/review/data/supported_file_types.json",
        "exclude_source": "navin/review/data/default_exclude_patterns.json",
    }


def filter_findings_precision(
    findings: list[dict[str, Any]],
    *,
    min_confidence: float = 0.75,
    require_evidence: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Second-pass finding filter: precision over recall (no invented claims)."""
    from navin.review.evidence import (
        DEFAULT_MIN_CONFIDENCE,
        finding_line,
        finding_path,
        has_grounded_evidence,
        is_speculative_claim,
    )

    floor = float(min_confidence if min_confidence is not None else DEFAULT_MIN_CONFIDENCE)
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for raw in findings:
        item = dict(raw)
        conf = item.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else 0.7
        except (TypeError, ValueError):
            conf_f = 0.7
        path = finding_path(item)
        line = finding_line(item)
        sev = str(item.get("severity") or "").lower()
        reason = None
        if conf_f < floor:
            reason = "low_confidence"
        elif not path:
            reason = "missing_path"
        elif is_speculative_claim(item) and conf_f < 0.92:
            reason = "speculative"
        elif line is None and sev in {"critical", "high", "medium"}:
            reason = "missing_line"
        elif (
            require_evidence
            and sev in {"critical", "high", "medium"}
            and not has_grounded_evidence(item)
        ):
            reason = "missing_evidence"
        if reason:
            item["drop_reason"] = reason
            dropped.append(item)
        else:
            kept.append(item)
    return kept, dropped
