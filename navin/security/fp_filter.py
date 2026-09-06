"""High-confidence FP filter for security findings (Anthropic-style hard rules)."""

from __future__ import annotations

import re
from typing import Any

from navin.review.evidence import (
    DEFAULT_MIN_CONFIDENCE,
    has_grounded_evidence,
    is_speculative_claim,
)

# Hard exclusions inspired by claude-code-security-review HardExclusionRules (MIT).
_RATE_LIMITING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(missing|lack of|no)\s+rate\s+limit"),
    re.compile(r"(?i)\brate\s+limiting\s+(missing|required|not implemented)"),
    re.compile(r"(?i)\b(implement|add(?:ing)?)\s+rate\s+limit"),
    re.compile(r"(?i)\bunlimited\s+(requests|calls|api)\b"),
    re.compile(r"(?i)\brate.?limit"),
]

_DOS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(denial of service|dos attack|resource exhaustion)\b"),
    re.compile(r"(?i)\b(exhaust|overwhelm|overload).*?(resource|memory|cpu)\b"),
    re.compile(r"(?i)\b(infinite|unbounded).*?(loop|recursion)\b"),
    re.compile(r"(?i)\b(ddos|dos)\b"),
]

_RESOURCE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(resource|memory|file)\s+leak\s+potential"),
    re.compile(r"(?i)\bunclosed\s+(resource|file|connection)"),
    re.compile(r"(?i)\bpotential\s+memory\s+leak"),
    re.compile(r"(?i)\b(database|thread|socket|connection)\s+leak"),
]

_OPEN_REDIRECT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(open redirect|unvalidated redirect)\b"),
    re.compile(r"(?i)\b(redirect.(attack|exploit|vulnerability))\b"),
]

_MEMORY_SAFETY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(buffer overflow|stack overflow|heap overflow)\b"),
    re.compile(r"(?i)\b(oob)\s+(read|write|access)\b"),
    re.compile(r"(?i)\b(out.?of.?bounds?)\b"),
    re.compile(r"(?i)\b(memory safety|memory corruption)\b"),
    re.compile(r"(?i)\b(use.?after.?free|double.?free|null.?pointer.?dereference)\b"),
    re.compile(r"(?i)\b(integer overflow|integer underflow)\b"),
]

_REGEX_INJECTION: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(regex|regular expression)\s+injection\b"),
    re.compile(r"(?i)\b(regex|regular expression)\s+denial of service\b"),
]

_SSRF_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(ssrf|server\s*-?\s*side\s+request\s+forgery)\b"),
]

_GENERIC_NOISE: list[re.Pattern[str]] = [
    re.compile(r"(?i)\b(missing\s+https?\s+only|hsts\s+recommended)\b"),
    re.compile(r"(?i)\b(outdated\s+dependenc|bump\s+all\s+packages)\b"),
    re.compile(r"(?i)\b(add\s+more\s+logging|consider\s+adding\s+comments)\b"),
    re.compile(r"(?i)\b(theoretical|potential(?:ly)?\s+vulnerable|could\s+be\s+abused)\b"),
    re.compile(r"(?i)\b(peut[- ][eê]tre|pourrait|théorique|éventuellement)\b"),
]

_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(_test|\.test|\.spec)\.[a-z]+$",
    re.I,
)

_C_CPP_EXTS = frozenset({".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hxx"})


def hard_exclusion_reason(finding: dict[str, Any]) -> str | None:
    """Return a drop reason for known low-signal / inapplicable findings."""
    path = str(finding.get("file_path") or finding.get("file") or "")
    lower_path = path.lower()
    if lower_path.endswith(".md"):
        return "markdown_doc"

    text = " ".join(
        str(finding.get(k) or "")
        for k in (
            "summary",
            "explanation",
            "recommendation",
            "category",
            "id",
            "vulnerability_type",
            "title",
            "description",
        )
    )

    for pattern in _DOS_PATTERNS:
        if pattern.search(text):
            return "dos_noise"
    for pattern in _RATE_LIMITING_PATTERNS:
        if pattern.search(text):
            return "rate_limit_noise"
    for pattern in _RESOURCE_PATTERNS:
        if pattern.search(text):
            return "resource_mgmt_noise"
    for pattern in _OPEN_REDIRECT_PATTERNS:
        if pattern.search(text):
            return "open_redirect_low_signal"
    for pattern in _REGEX_INJECTION:
        if pattern.search(text):
            return "regex_injection_noise"
    for pattern in _GENERIC_NOISE:
        if pattern.search(text):
            return "hard_exclusion"

    ext = ""
    if "." in lower_path:
        ext = f".{lower_path.rsplit('.', 1)[-1]}"
    # Memory safety only applies to C/C++.
    if ext not in _C_CPP_EXTS:
        for pattern in _MEMORY_SAFETY_PATTERNS:
            if pattern.search(text):
                return "memory_safety_non_cpp"
    # SSRF in HTML is usually client-side noise.
    if ext == ".html":
        for pattern in _SSRF_PATTERNS:
            if pattern.search(text):
                return "ssrf_in_html"

    return None


def apply_fp_filter(
    findings: list[dict[str, Any]],
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    drop_test_paths: bool = True,
    require_evidence_for_high: bool = True,
    require_evidence_for_medium: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (kept, dropped) after hard rules + confidence + evidence gate."""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for raw in findings:
        item = dict(raw)
        path = str(item.get("file_path") or item.get("file") or "")
        conf = item.get("confidence")
        try:
            conf_f = float(conf) if conf is not None else 0.75
        except (TypeError, ValueError):
            conf_f = 0.75
        item["confidence"] = round(conf_f, 2)

        reason = None
        if drop_test_paths and _TEST_PATH.search(path.replace("\\", "/")):
            reason = "test_path"
        else:
            reason = hard_exclusion_reason(item)

        if reason is None and conf_f < min_confidence:
            reason = "low_confidence"

        if reason is None and is_speculative_claim(item) and conf_f < 0.92:
            reason = "speculative"

        # Medium+ need a PoC / exploit / source→sink / code excerpt.
        if reason is None and (
            require_evidence_for_high or require_evidence_for_medium
        ):
            sev = str(item.get("severity") or "").lower()
            need_high = require_evidence_for_high and sev in {"critical", "high"}
            need_med = require_evidence_for_medium and sev == "medium"
            if (need_high or need_med) and not has_grounded_evidence(item, min_chars=6):
                reason = "missing_evidence"

        if reason:
            item["drop_reason"] = reason
            dropped.append(item)
        else:
            kept.append(item)
    return kept, dropped
