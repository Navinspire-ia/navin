"""Load review rules (OCR-compatible .opencodereview/rule.json + Navin path)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_review_rules(root: Path) -> dict[str, Any]:
    """Resolve project rules. Prefer Navin path, then OCR-compatible path."""
    root = root.expanduser().resolve(strict=False)
    candidates = [
        root / ".navin" / "review-rules.json",
        root / ".opencodereview" / "rule.json",
    ]
    for path in candidates:
        data = _read_json(path)
        if data is not None:
            include = data.get("include") if isinstance(data.get("include"), list) else []
            exclude = data.get("exclude") if isinstance(data.get("exclude"), list) else []
            rules = data.get("rules") if isinstance(data.get("rules"), list) else []
            return {
                "ok": True,
                "source": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
                "include": [str(x) for x in include if isinstance(x, str)],
                "exclude": [str(x) for x in exclude if isinstance(x, str)],
                "rules": [
                    {
                        "path": str(item.get("path") or ""),
                        "rule": str(item.get("rule") or ""),
                        "merge_system_rule": bool(item.get("merge_system_rule")),
                    }
                    for item in rules
                    if isinstance(item, dict) and item.get("path") and item.get("rule")
                ],
            }
    return {
        "ok": True,
        "source": None,
        "include": [],
        "exclude": [],
        "rules": [],
        "note": (
            "No project rules file. Optional: .navin/review-rules.json or "
            ".opencodereview/rule.json (OCR-compatible)."
        ),
    }


def rules_for_path(path: str, rules_payload: dict[str, Any]) -> list[str]:
    """Return matching rule texts for a relative path (first match wins unless merge)."""
    from navin.review.gates import path_matches

    rel = path.replace("\\", "/").lstrip("./")
    matched: list[str] = []
    for item in rules_payload.get("rules") or []:
        pattern = str(item.get("path") or "")
        if not pattern or not path_matches(rel, pattern):
            continue
        text = str(item.get("rule") or "").strip()
        if not text:
            continue
        matched.append(text)
        if not item.get("merge_system_rule"):
            break
    return matched
