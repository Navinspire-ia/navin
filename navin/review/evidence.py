"""Ground-truth helpers for Review / Security / Debug findings.

Findings without a real code/runtime excerpt are treated as invented and
must be dropped before reports, PR comments, or chat summaries.
"""

from __future__ import annotations

import re
from typing import Any

# Excerpt / PoC fields accepted as proof (any non-empty one is enough).
EVIDENCE_FIELDS: tuple[str, ...] = (
    "existing_code",
    "evidence",
    "proof",
    "excerpt",
    "failing_output",
    "stack",
    "poc_sketch",
    "malicious_input_example",
    "exploit_scenario",
    "trigger_flow",
    "before",
    "after",
)

# Soft speculative wording (EN + FR). Kept findings may still use hedging
# only when confidence is very high AND evidence is present.
SPECULATIVE_RE = re.compile(
    r"(?i)\b("
    r"could\s+be|might\s+be|may\s+be|possibly|potentially|perhaps|"
    r"seems?\s+to|appears?\s+to|likely|probably|theoretical|"
    r"consider\s+adding|optional\s+improvement|nit:|"
    r"style\s+only|could\s+be\s+abused|potential(?:ly)?\s+vulnerable|"
    r"peut[- ][eê]tre|pourrait|éventuellement|théorique|"
    r"il\s+se\s+peut|semble|probablement|possiblement"
    r")\b"
)

# Absences / absolutes claimed without proof are a common hallucination pattern.
UNVERIFIED_ABSENCE_RE = re.compile(
    r"(?i)\b("
    r"does\s+not\s+exist|n['’]existe\s+pas|there\s+is\s+no|"
    r"never\s+(?:runs?|executes?|calls?)|always\s+sequential|"
    r"no\s+concurrency|pas\s+de\s+concurrence|exécution\s+séquentielle|"
    r"tools?\s+are\s+sequential|sequential\s+(?:only|execution)"
    r")\b"
)

DEFAULT_MIN_CONFIDENCE = 0.75
DEFAULT_MIN_EVIDENCE_CHARS = 12


def finding_text_blob(item: dict[str, Any]) -> str:
    """Join human-readable claim fields for pattern checks."""
    return " ".join(
        str(item.get(k) or "")
        for k in (
            "summary",
            "explanation",
            "recommendation",
            "category",
            "vulnerability_type",
            "title",
            "description",
            "id",
        )
    )


def finding_evidence_text(item: dict[str, Any]) -> str:
    """Return concatenated proof fields (code excerpt, PoC, stack, …)."""
    parts: list[str] = []
    for key in EVIDENCE_FIELDS:
        value = item.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def has_grounded_evidence(
    item: dict[str, Any],
    *,
    min_chars: int = DEFAULT_MIN_EVIDENCE_CHARS,
) -> bool:
    """True when the finding carries a real excerpt / PoC / stack proof."""
    return len(finding_evidence_text(item)) >= max(1, int(min_chars))


def is_speculative_claim(item: dict[str, Any]) -> bool:
    """True when the claim uses hedging or unverified-absence language."""
    text = finding_text_blob(item)
    if SPECULATIVE_RE.search(text):
        return True
    if UNVERIFIED_ABSENCE_RE.search(text) and not has_grounded_evidence(item):
        return True
    return False


def finding_path(item: dict[str, Any]) -> str:
    return str(
        item.get("file_path")
        or item.get("relevant_file")
        or item.get("path")
        or item.get("file")
        or ""
    ).strip()


def finding_line(item: dict[str, Any]) -> int | None:
    raw = item.get("start_line", item.get("line"))
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
