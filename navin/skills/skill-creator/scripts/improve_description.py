#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Score a SKILL.md description for triggering quality and suggest fixes.

Usage:
    improve_description.py --skill <folder> [--triggers <trigger-set.json>] [--json]
    improve_description.py --skill <folder> --apply "<new description>" [--write]

Heuristic checks (no model call): length, third person, quoted trigger
phrases, a "Use when" clause, vocabulary overlap with prompts that should
trigger the skill and with prompts that should not. --apply scores a
candidate text next to the current one; nothing on disk changes unless
--write is also passed, and --write without --apply is refused.

Trigger set format: a JSON list of {"query": str, "should_trigger": bool}.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
STOPWORDS = set(
    "a an the and or of to for in on with when use used using this that these those is are be it its "
    "my me i we our you your as at by from into about do does can please help need want make".split()
)
THIRD_PERSON_BAD = re.compile(r"\b(you|your|yours|yourself|i|we|our)\b", re.IGNORECASE)
SITUATION_CUE = re.compile(r"\b(use when|use for|use this|when the user|trigger|or mentions|or pastes|or attaches)\b", re.IGNORECASE)


def read_description(skill_dir: Path) -> tuple[str, str]:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise SystemExit(f"SKILL.md not found in {skill_dir}")
    text = skill_md.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise SystemExit("SKILL.md has no YAML frontmatter")
    desc = _frontmatter_value(match.group(1), "description")
    if desc is None:
        raise SystemExit("frontmatter has no description")
    return desc, text


def _frontmatter_value(block: str, key: str) -> str | None:
    lines = block.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith(f"{key}:"):
            continue
        value = line[len(key) + 1:].strip()
        if value in ("|", ">", "|-", ">-"):
            folded = []
            for extra in lines[index + 1:]:
                if extra and not extra[0].isspace():
                    break
                folded.append(extra.strip())
            return " ".join(part for part in folded if part)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        return value
    return None


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9'+-]*", text.lower()) if w not in STOPWORDS and len(w) > 2}


def overlap(desc: str, query: str) -> float:
    q = words(query)
    if not q:
        return 0.0
    return len(q & words(desc)) / len(q)


def score(desc: str, triggers: list[dict] | None) -> dict:
    checks: list[dict] = []
    suggestions: list[str] = []
    desc = desc.strip()
    length = len(desc)

    def add(name: str, points: int, max_points: int, detail: str, fix: str | None = None) -> None:
        checks.append({"check": name, "points": points, "max": max_points, "detail": detail})
        if fix and points < max_points:
            suggestions.append(fix)

    if 120 <= length <= 700:
        add("length", 15, 15, f"{length} characters")
    elif 80 <= length <= 1024:
        add("length", 8, 15, f"{length} characters (sweet spot 120-700)",
            "Aim for 120-700 characters: one sentence on what, one on when.")
    else:
        add("length", 0, 15, f"{length} characters (must stay between 80 and 1024)",
            "Rewrite to 80-1024 characters; shorter than 80 says too little, over 1024 is cut.")

    pronouns = THIRD_PERSON_BAD.findall(desc)
    if pronouns:
        add("third_person", 0, 15, f"first/second person found: {sorted(set(p.lower() for p in pronouns))}",
            "Write in third person: describe the skill and the user's request, never 'you' or 'we'.")
    else:
        add("third_person", 15, 15, "no first or second person")

    phrases = re.findall(r'"([^"]{3,80})"', desc)
    if len(phrases) >= 3:
        add("trigger_phrases", 20, 20, f"{len(phrases)} quoted phrases")
    else:
        add("trigger_phrases", 7 * len(phrases), 20, f"{len(phrases)} quoted phrases (aim for 3 or more)",
            "Quote at least three phrases users actually type, e.g. \"fill this PDF form\".")

    if SITUATION_CUE.search(desc):
        add("when_clause", 15, 15, "has a 'Use when ...' or situation clause")
    else:
        add("when_clause", 0, 15, "no 'Use when' clause",
            "Add 'Use when the user asks to ...' followed by phrases and situations.")

    verb_start = re.match(r"^[A-Z][a-z]+\b", desc) and not re.match(r"^(This|The|A|An|It)\b", desc)
    add("capability_first", 10 if verb_start else 4, 10,
        "starts with a capability verb" if verb_start else "does not start with a verb (Create, Fill, Review ...)",
        "Open with the capability: 'Fill, flatten and extract PDF forms ...'.")

    if triggers:
        positives = [t for t in triggers if t.get("should_trigger")]
        negatives = [t for t in triggers if not t.get("should_trigger")]
        pos_scores = [overlap(desc, t["query"]) for t in positives]
        neg_scores = [overlap(desc, t["query"]) for t in negatives]
        pos_mean = sum(pos_scores) / len(pos_scores) if pos_scores else 0.0
        neg_mean = sum(neg_scores) / len(neg_scores) if neg_scores else 0.0
        weak = [t["query"] for t, s in zip(positives, pos_scores) if s < 0.2]
        risky = [t["query"] for t, s in zip(negatives, neg_scores) if s >= 0.4]
        pos_points = round(min(pos_mean / 0.5, 1.0) * 15)
        add("should_trigger_overlap", pos_points, 15,
            f"mean vocabulary overlap with should-trigger prompts: {pos_mean:.2f}; weak: {len(weak)}",
            "Borrow words from the prompts that failed to trigger: " + "; ".join(q[:60] for q in weak[:3]) if weak else None)
        neg_points = 10 if neg_mean < 0.25 and not risky else 5 if neg_mean < 0.4 else 0
        add("should_not_trigger_separation", neg_points, 10,
            f"mean overlap with should-not-trigger prompts: {neg_mean:.2f}; risky: {len(risky)}",
            "Name what the skill does not cover, or drop generic words shared with: " + "; ".join(q[:60] for q in risky[:3]) if risky else None)
        trigger_detail = {
            "positives": len(positives), "negatives": len(negatives),
            "weak_positives": weak, "risky_negatives": risky,
        }
    else:
        add("should_trigger_overlap", 0, 0, "no trigger set given (--triggers)")
        add("should_not_trigger_separation", 0, 0, "no trigger set given (--triggers)")
        trigger_detail = None

    total = sum(c["points"] for c in checks)
    maximum = sum(c["max"] for c in checks) or 1
    return {
        "description": desc,
        "score": round(100 * total / maximum),
        "checks": checks,
        "suggestions": suggestions,
        "triggers": trigger_detail,
    }


def replace_description(text: str, new_desc: str) -> str:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise SystemExit("SKILL.md has no YAML frontmatter")
    block = match.group(1)
    lines = block.splitlines()
    out, skipping = [], False
    replaced = False
    for line in lines:
        if line.startswith("description:"):
            out.append("description: " + new_desc.replace("\n", " ").strip())
            replaced = True
            skipping = line[len("description:"):].strip() in ("|", ">", "|-", ">-")
            continue
        if skipping and line and line[0].isspace():
            continue
        skipping = False
        out.append(line)
    if not replaced:
        raise SystemExit("frontmatter has no description line to replace")
    return text[: match.start(1)] + "\n".join(out) + text[match.end(1):]


def print_report(report: dict, label: str) -> None:
    print(f"{label}: score {report['score']}/100")
    print(f"  {report['description']}")
    for check in report["checks"]:
        if check["max"]:
            print(f"  [{check['points']:>2}/{check['max']:<2}] {check['check']}: {check['detail']}")
        else:
            print(f"  [ -- ] {check['check']}: {check['detail']}")
    if report["suggestions"]:
        print("  suggestions:")
        for item in report["suggestions"]:
            print(f"   - {item}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Heuristic scoring of a skill description; no model call.")
    parser.add_argument("--skill", required=True, type=Path, help="skill folder containing SKILL.md")
    parser.add_argument("--triggers", type=Path, help="JSON list of {query, should_trigger}")
    parser.add_argument("--apply", metavar="TEXT", help="candidate description to score (and write with --write)")
    parser.add_argument("--write", action="store_true", help="write the --apply text into SKILL.md")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if args.write and not args.apply:
        print("--write requires --apply with the new description text", file=sys.stderr)
        return 2
    triggers = None
    if args.triggers:
        triggers = json.loads(args.triggers.read_text(encoding="utf-8"))
        if not isinstance(triggers, list):
            print("--triggers must be a JSON list", file=sys.stderr)
            return 2

    current, text = read_description(args.skill)
    result = {"current": score(current, triggers)}
    if args.apply:
        result["candidate"] = score(args.apply, triggers)
        if args.write:
            (args.skill / "SKILL.md").write_text(replace_description(text, args.apply), encoding="utf-8")
            result["written"] = True

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print_report(result["current"], "current")
        if "candidate" in result:
            print()
            print_report(result["candidate"], "candidate")
            print("\nSKILL.md updated" if result.get("written") else "\n(dry run: add --write to apply the candidate)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
