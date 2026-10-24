# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Hookify: warn about or block agent actions with Markdown rule files.

A rule is a ``hookify.<name>.local.md`` file in ``<workspace>/.navin/``
(project) or ``~/.navin/`` (personal; a project rule wins a name clash)::

    ---
    name: block-dangerous-rm
    enabled: true
    event: bash              # bash | file | stop | prompt | all
    pattern: rm\\s+-rf        # short form: one regex on the event's main field
    action: block            # warn (default) | block
    ---
    Message shown when the rule fires.

The long form lists ``conditions`` (all must match), each with ``field``,
``operator`` and ``pattern``. Operators: ``regex_match`` (case-insensitive),
``not_regex_match``, ``contains``, ``not_contains``, ``equals``,
``starts_with``, ``ends_with``. ``tool_matcher`` narrows a rule to some tools
(``exec|write_file``, ``Bash``, ``Edit|Write``...).

Rules only match text; they never run code, so project rules need no trust.
They are evaluated natively by :mod:`navin.agent.user_hooks` on PreToolUse,
PostToolUse, Stop, SubagentStop and UserPromptSubmit. ``NAVIN_HOOKIFY=0``
turns them off.

``python -m navin.agent.hookify list <workspace>`` prints the active rules;
``python -m navin.agent.hookify check <workspace>`` validates every file.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from navin.agent.frontmatter import split_frontmatter

RULE_GLOB = "hookify.*.local.md"
RULE_EVENTS = ("bash", "file", "stop", "prompt", "all")
ACTIONS = ("warn", "block")
OPERATORS = (
    "regex_match", "not_regex_match", "contains", "not_contains",
    "equals", "starts_with", "ends_with",
)
# Field each event checks when a rule uses the short ``pattern`` form.
DEFAULT_FIELD = {"bash": "command", "file": "new_text"}
_MAX_TEXT = 200_000
_MAX_PATTERN = 1000
# Nested quantifiers such as (a+)+ or (.*)* can hang the regex engine.
_REDOS_RE = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")

BASH_TOOLS = frozenset({"exec", "Bash"})
FILE_TOOLS = frozenset({"write_file", "edit_file", "apply_patch", "Write", "Edit", "MultiEdit"})


@dataclass
class Condition:
    field: str
    operator: str = "regex_match"
    pattern: str = ""


@dataclass
class Rule:
    name: str
    event: str = "all"
    action: str = "warn"
    enabled: bool = True
    conditions: list[Condition] = field(default_factory=list)
    tool_matcher: str = ""
    message: str = ""
    source: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return f"**[{self.name}]**\n{self.message}".strip()


@dataclass
class Verdict:
    blocking: list[Rule] = field(default_factory=list)
    warning: list[Rule] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.blocking)

    @property
    def message(self) -> str:
        rules = self.blocking or self.warning
        return "\n\n".join(r.text for r in rules)

    @property
    def warning_message(self) -> str:
        return "\n\n".join(r.text for r in self.warning)


def enabled() -> bool:
    return os.environ.get("NAVIN_HOOKIFY", "1").strip().lower() not in ("0", "false", "off", "no")


def _bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "off", "0", "")
    return default if value is None else bool(value)


def pattern_problem(pattern: str) -> str | None:
    """Why a regex is refused, or None when it is safe to use."""
    if len(pattern) > _MAX_PATTERN:
        return f"pattern longer than {_MAX_PATTERN} characters"
    if _REDOS_RE.search(pattern):
        return "nested quantifier (for example (a+)+) can hang the regex engine"
    try:
        re.compile(pattern)
    except re.error as exc:
        return f"invalid regex: {exc}"
    return None


def parse_rule(text: str, source: str = "") -> Rule | None:
    """Rule from one Markdown file, or None when it has no frontmatter."""
    meta, body, error = split_frontmatter(text)
    if meta is None:
        if error:
            logger.warning("Hookify rule {}: {}", source, error)
        return None
    stem = Path(source).name
    default_name = stem[len("hookify."):-len(".local.md")] if stem.startswith("hookify.") else "unnamed"
    rule = Rule(
        name=str(meta.get("name") or default_name).strip(),
        event=str(meta.get("event") or "all").strip().lower(),
        action=str(meta.get("action") or "warn").strip().lower(),
        enabled=_bool(meta.get("enabled"), True),
        tool_matcher=str(meta.get("tool_matcher") or "").strip(),
        message=(body or "").strip(),
        source=source,
    )
    if rule.event not in RULE_EVENTS:
        rule.problems.append(f"unknown event {rule.event!r} (use {', '.join(RULE_EVENTS)})")
    if rule.action not in ACTIONS:
        rule.problems.append(f"unknown action {rule.action!r} (use warn or block)")
    raw_conditions = meta.get("conditions")
    if isinstance(raw_conditions, list) and raw_conditions:
        for item in raw_conditions:
            if not isinstance(item, dict):
                rule.problems.append(f"condition {item!r} is not a mapping")
                continue
            rule.conditions.append(Condition(
                field=str(item.get("field") or "").strip(),
                operator=str(item.get("operator") or "regex_match").strip(),
                pattern=str(item.get("pattern") if item.get("pattern") is not None else ""),
            ))
    elif meta.get("pattern") not in (None, ""):
        rule.conditions.append(Condition(
            field=DEFAULT_FIELD.get(rule.event, "content"),
            pattern=str(meta["pattern"]),
        ))
    if not rule.conditions:
        rule.problems.append("no pattern and no conditions: the rule can never match")
    for cond in rule.conditions:
        if not cond.field:
            rule.problems.append("a condition has no field")
        if cond.operator not in OPERATORS:
            rule.problems.append(f"unknown operator {cond.operator!r}")
        elif cond.operator.endswith("regex_match"):
            problem = pattern_problem(cond.pattern)
            if problem:
                rule.problems.append(f"{cond.field}: {problem}")
    if not rule.message:
        rule.problems.append("empty message body")
    return rule


def rule_files(workspace: Path | None) -> list[Path]:
    """Rule files, personal first then project, so a project rule overrides."""
    roots = [Path.home() / ".navin"]
    if workspace is not None:
        roots.append(Path(workspace) / ".navin")
    files: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        try:
            found = sorted(root.glob(RULE_GLOB)) if root.is_dir() else []
        except OSError:
            found = []
        for path in found:
            resolved = path.resolve()
            if resolved not in seen and path.is_file():
                seen.add(resolved)
                files.append(path)
    return files


@lru_cache(maxsize=256)
def _cached_rule(path: str, mtime_ns: int, size: int) -> Rule | None:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Hookify rule {} unreadable: {}", path, exc)
        return None
    return parse_rule(text, source=path)


def load_rules(workspace: Path | None, *, include_disabled: bool = False) -> list[Rule]:
    """Every rule by name (project wins), skipping disabled and broken ones."""
    by_name: dict[str, Rule] = {}
    for path in rule_files(workspace):
        try:
            stat = path.stat()
        except OSError:
            continue
        rule = _cached_rule(str(path), stat.st_mtime_ns, stat.st_size)
        if rule is None:
            continue
        by_name[rule.name] = rule
    rules = list(by_name.values())
    if include_disabled:
        return rules
    return [r for r in rules if r.enabled and not r.problems]


def tool_kind(tool_name: str) -> str:
    if tool_name in BASH_TOOLS:
        return "bash"
    if tool_name in FILE_TOOLS:
        return "file"
    return "tool"


def _event_kind(event: str, tool_name: str) -> str:
    if event in ("PreToolUse", "PostToolUse"):
        return tool_kind(tool_name)
    if event in ("Stop", "SubagentStop"):
        return "stop"
    if event == "UserPromptSubmit":
        return "prompt"
    return ""


def _items(tool_input: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = tool_input.get(key)
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _join(values: list[Any]) -> str:
    return "\n".join(str(v) for v in values if isinstance(v, str) and v)


def _file_field(name: str, tool_input: dict[str, Any]) -> str | None:
    """Navin tool params (write_file, edit_file, apply_patch) and common aliases."""
    nested = _items(tool_input, "files") + _items(tool_input, "edits")
    if name == "file_path":
        return _join([tool_input.get("path"), tool_input.get("file_path")]
                     + [i.get("path") or i.get("file_path") for i in nested])
    if name in ("new_text", "new_string", "content"):
        return _join([tool_input.get("content"), tool_input.get("new_text"), tool_input.get("new_string")]
                     + [i.get("content") or i.get("new_text") or i.get("new_string") for i in nested])
    if name in ("old_text", "old_string"):
        return _join([tool_input.get("old_text"), tool_input.get("old_string")]
                     + [i.get("old_text") or i.get("old_string") for i in nested])
    return None


def render_transcript(messages: list[Any] | None) -> str:
    """Conversation text a Stop rule can search: messages, tool calls, results."""
    if not messages:
        return ""
    parts: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(
                str(block.get("text", "")) for block in content if isinstance(block, dict)
            )
        if content:
            parts.append(f"{msg.get('role', '')}: {content}")
        for call in msg.get("tool_calls") or []:
            fn = call.get("function") if isinstance(call, dict) else None
            if isinstance(fn, dict):
                parts.append(f"tool {fn.get('name', '')}: {fn.get('arguments', '')}")
    text = "\n".join(parts)
    return text[-_MAX_TEXT:]


def extract_field(name: str, kind: str, payload: dict[str, Any]) -> str | None:
    """Text a condition checks, or None when this event has no such field."""
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    if kind == "bash" and name == "command":
        return str(tool_input.get("command") or tool_input.get("cmd") or "")
    if kind == "file":
        value = _file_field(name, tool_input)
        if value is not None:
            return value
    if name in ("output", "tool_response") and "tool_response" in payload:
        return str(payload.get("tool_response") or "")
    if name in tool_input:
        return str(tool_input[name])
    if name in ("user_prompt", "prompt") and kind == "prompt":
        return str(payload.get("prompt") or "")
    if name in ("last_message", "last_assistant_message") and kind == "stop":
        return str(payload.get("last_assistant_message") or "")
    if name == "transcript" and kind == "stop":
        return str(payload.get("transcript") or "")
    if name == "reason" and kind == "stop":
        return str(payload.get("reason") or "")
    if name == "content":
        if kind == "bash":
            return str(tool_input.get("command") or tool_input.get("cmd") or "")
        if kind == "prompt":
            return str(payload.get("prompt") or "")
        if kind == "stop":
            return str(payload.get("last_assistant_message") or "")
    return None


@lru_cache(maxsize=256)
def _regex(pattern: str) -> re.Pattern[str] | None:
    if pattern_problem(pattern):
        return None
    return re.compile(pattern, re.IGNORECASE)


def _check(cond: Condition, value: str) -> bool:
    value = value[:_MAX_TEXT]
    op, pattern = cond.operator, cond.pattern
    if op in ("regex_match", "not_regex_match"):
        regex = _regex(pattern)
        if regex is None:
            return False
        found = bool(regex.search(value))
        return found if op == "regex_match" else not found
    if op == "contains":
        return pattern in value
    if op == "not_contains":
        return pattern not in value
    if op == "equals":
        return value == pattern
    if op == "starts_with":
        return value.startswith(pattern)
    if op == "ends_with":
        return value.endswith(pattern)
    return False


def _tool_allowed(rule: Rule, tool_name: str) -> bool:
    from navin.agent.user_hooks import matches

    if not rule.tool_matcher:
        return True
    if not tool_name:
        return False
    return matches(rule.tool_matcher, tool_name)


def rule_matches(rule: Rule, kind: str, payload: dict[str, Any]) -> bool:
    tool_name = str(payload.get("tool_name") or "")
    # Other tools (read_file, web_fetch...) are only seen by "all" rules.
    if rule.event not in ("all", kind):
        return False
    if not _tool_allowed(rule, tool_name):
        return False
    if not rule.conditions:
        return False
    for cond in rule.conditions:
        value = extract_field(cond.field, kind, payload)
        if value is None or not _check(cond, value):
            return False
    return True


def evaluate(event: str, payload: dict[str, Any], workspace: Path | None) -> Verdict:
    """Rules of *workspace* that fire on this event; never raises."""
    verdict = Verdict()
    if not enabled():
        return verdict
    kind = _event_kind(event, str(payload.get("tool_name") or ""))
    if not kind:
        return verdict
    try:
        rules = load_rules(workspace)
        for rule in rules:
            if rule_matches(rule, kind, payload):
                (verdict.blocking if rule.action == "block" else verdict.warning).append(rule)
    except Exception:  # noqa: BLE001 - a broken rule never breaks a turn
        logger.exception("Hookify evaluation failed")
        return Verdict()
    return verdict


def summary(workspace: Path | None) -> str:
    """One line for the system prompt ('' when no rule is active)."""
    if not enabled():
        return ""
    try:
        rules = load_rules(workspace)
    except Exception:  # noqa: BLE001
        return ""
    if not rules:
        return ""
    names = ", ".join(f"{r.name} ({r.event}, {r.action})" for r in rules[:12])
    more = f" and {len(rules) - 12} more" if len(rules) > 12 else ""
    return f"- hookify rules: {names}{more}"


def _describe(rule: Rule) -> str:
    state = "on" if rule.enabled else "off"
    if rule.problems:
        state = "broken"
    conds = "; ".join(f"{c.field} {c.operator} {c.pattern!r}" for c in rule.conditions) or "-"
    tools = f" tools={rule.tool_matcher}" if rule.tool_matcher else ""
    return f"{rule.name}  [{state}] event={rule.event} action={rule.action}{tools}  {conds}  ({rule.source})"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] in ("list", "check"):
        workspace = Path(args[1]).expanduser()
        rules = load_rules(workspace, include_disabled=True)
        if not rules:
            print(f"no hookify rules ({workspace / '.navin' / RULE_GLOB} or ~/.navin/{RULE_GLOB})")
            return 0
        broken = 0
        for rule in rules:
            print(_describe(rule))
            for problem in rule.problems:
                broken += 1
                print(f"    problem: {problem}")
        if not enabled():
            print("NAVIN_HOOKIFY=0: rules are switched off")
        return 1 if (args[0] == "check" and broken) else 0
    print("usage: python -m navin.agent.hookify list|check <workspace>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
