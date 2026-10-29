#!/usr/bin/env python3
"""Validate an MCP evaluation set and prepare a report skeleton.

The evaluation file holds realistic questions with expected answers (see
references/evaluation.md). This script never calls an LLM: it checks the file,
optionally connects to the server to list its tools, and writes a report the
agent fills in by running each question itself.

Examples:
    python3 evaluate.py --validate-only evaluation.example.json
    python3 evaluate.py evaluation.json --transport stdio --command python3 --arg server.py \
        --output eval-report.md
    python3 evaluate.py evaluation.xml --transport http --url http://127.0.0.1:8000/mcp \
        --format json --output eval-report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# connections.py sits next to this script; the script folder is sys.path[0] when run
# as a program, so no path manipulation is needed.
from connections import (
    MissingDependencyError,
    add_connection_arguments,
    connection_from_args,
)

try:  # local files only, but prefer the hardened parser when present
    import defusedxml.ElementTree as ElementTree
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as ElementTree

MIN_QUESTIONS = 10


class EvaluationError(ValueError):
    """The evaluation file is malformed."""


def load_evaluation(path: Path) -> list[dict[str, Any]]:
    """Return the list of {question, answer, ...} entries from XML or JSON."""
    if not path.is_file():
        raise EvaluationError(f"{path} does not exist")
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".xml":
        return _load_xml(text, path)
    if path.suffix.lower() == ".json":
        return _load_json(text, path)
    raise EvaluationError(f"{path.name}: use a .json or .xml evaluation file")


def _load_xml(text: str, path: Path) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(text)
    except (ElementTree.ParseError, ValueError) as exc:
        # ParseError for malformed XML; defusedxml raises ValueError subclasses for
        # forbidden constructs (entities, DTD).
        raise EvaluationError(f"{path.name}: invalid XML ({exc})") from exc
    if root.tag != "evaluation":
        raise EvaluationError(f"{path.name}: root element must be <evaluation>, got <{root.tag}>")
    entries = []
    for index, pair in enumerate(root.findall("qa_pair"), start=1):
        question = pair.findtext("question")
        answer = pair.findtext("answer")
        if question is None or answer is None:
            raise EvaluationError(f"{path.name}: qa_pair #{index} needs <question> and <answer>")
        entry: dict[str, Any] = {"question": question.strip(), "answer": answer.strip()}
        tools = pair.findtext("tools")
        if tools:
            entry["tools"] = [t.strip() for t in tools.split(",") if t.strip()]
        entries.append(entry)
    return entries


def _load_json(text: str, path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EvaluationError(f"{path.name}: invalid JSON ({exc})") from exc
    if isinstance(data, dict):
        items = data.get("questions")
        if not isinstance(items, list):
            raise EvaluationError(f"{path.name}: expected a top-level 'questions' array")
    elif isinstance(data, list):
        items = data
    else:
        raise EvaluationError(f"{path.name}: top level must be an object or an array")
    entries = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise EvaluationError(f"{path.name}: question #{index} must be an object")
        entries.append(dict(item))
    return entries


def validate_entries(entries: list[dict[str, Any]], *, strict: bool = False) -> list[str]:
    """Return a list of problems. Empty list means the file is usable."""
    problems: list[str] = []
    if not entries:
        problems.append("no questions found")
        return problems
    seen: set[str] = set()
    for index, entry in enumerate(entries, start=1):
        question = entry.get("question")
        answer = entry.get("answer")
        if not isinstance(question, str) or not question.strip():
            problems.append(f"question #{index}: 'question' must be a non-empty string")
        elif len(question.split()) < 6:
            problems.append(f"question #{index}: too short to be realistic (under 6 words)")
        if answer is None or (isinstance(answer, str) and not answer.strip()):
            problems.append(f"question #{index}: 'answer' must be present and non-empty")
        elif isinstance(answer, (list, dict)):
            problems.append(f"question #{index}: 'answer' must be a single value, not a list or object")
        if isinstance(question, str):
            key = question.strip().lower()
            if key in seen:
                problems.append(f"question #{index}: duplicate question")
            seen.add(key)
        tools = entry.get("tools")
        if tools is not None and (
            not isinstance(tools, list) or not all(isinstance(t, str) for t in tools)
        ):
            problems.append(f"question #{index}: 'tools' must be a list of tool names")
    if strict and len(entries) < MIN_QUESTIONS:
        problems.append(f"only {len(entries)} questions; aim for at least {MIN_QUESTIONS}")
    return problems


async def fetch_tools(ns: argparse.Namespace) -> list[dict[str, Any]]:
    async with connection_from_args(ns) as conn:
        return await conn.list_tools()


def build_report(
    source: Path, entries: list[dict[str, Any]], tools: list[dict[str, Any]] | None
) -> dict[str, Any]:
    expected_tools = {t for e in entries for t in e.get("tools", [])}
    available = {t["name"] for t in tools} if tools is not None else None
    missing = sorted(expected_tools - available) if available is not None else []
    return {
        "source": str(source),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "server_tools": tools,
        "tools_referenced_but_missing": missing,
        "questions": [
            {
                "id": index,
                "question": e["question"],
                "expected": e["answer"],
                "expected_tools": e.get("tools", []),
                "actual": None,
                "tool_calls": [],
                "verdict": "pending",
                "notes": "",
            }
            for index, e in enumerate(entries, start=1)
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# MCP evaluation report",
        "",
        f"Source: `{report['source']}`  ",
        f"Generated: {report['generated_at']}",
        "",
    ]
    tools = report["server_tools"]
    if tools is None:
        lines += ["## Server tools", "", "Not connected (no --transport given).", ""]
    else:
        lines += ["## Server tools", ""]
        for tool in tools:
            first = (tool["description"] or "").strip().splitlines()
            lines.append(f"- `{tool['name']}`: {first[0] if first else '(no description)'}")
        lines.append("")
    if report["tools_referenced_but_missing"]:
        lines += [
            "Tools referenced by questions but absent from the server: "
            + ", ".join(f"`{t}`" for t in report["tools_referenced_but_missing"]),
            "",
        ]
    lines += [
        "## Questions",
        "",
        "Fill `actual`, the tool calls made and the verdict (pass, fail, partial) "
        "after running each question with the server as the only source.",
        "",
        "| # | Question | Expected | Actual | Verdict |",
        "|---|---|---|---|---|",
    ]
    for q in report["questions"]:
        question = q["question"].replace("|", "\\|")
        lines.append(f"| {q['id']} | {question} | {q['expected']} | | pending |")
    lines += ["", "## Observations on the tools", "", "- ", ""]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate an MCP evaluation set and write a report skeleton.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples:", 1)[-1],
    )
    parser.add_argument("evaluation", type=Path, help="evaluation file (.json or .xml)")
    parser.add_argument(
        "--validate-only", action="store_true", help="check the file structure and exit"
    )
    parser.add_argument(
        "--strict", action="store_true", help=f"also require at least {MIN_QUESTIONS} questions"
    )
    parser.add_argument("--output", type=Path, help="report path (default: stdout)")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    add_connection_arguments(parser, required=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    ns = build_parser().parse_args(argv)
    try:
        entries = load_evaluation(ns.evaluation)
    except EvaluationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    problems = validate_entries(entries, strict=ns.strict)
    if problems:
        print(f"INVALID: {ns.evaluation} has {len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"OK: {len(entries)} question(s) in {ns.evaluation}")
    if ns.validate_only:
        return 0

    tools: list[dict[str, Any]] | None = None
    if ns.transport:
        try:
            tools = asyncio.run(fetch_tools(ns))
        except MissingDependencyError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"Connected: {len(tools)} tool(s) listed")
    report = build_report(ns.evaluation, entries, tools)
    text = (
        json.dumps(report, indent=2, ensure_ascii=False)
        if ns.format == "json"
        else render_markdown(report)
    )
    if ns.output:
        ns.output.write_text(text + "\n", encoding="utf-8")
        print(f"Report skeleton written to {ns.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
