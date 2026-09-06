"""Who writes and corrects a draft (S2.1, S2.3).

``TemplateAuthor`` is deterministic and offline: it turns a brief (which
tool failed, how, how often) into a SKILL.md built from a fixed set of good
practices for the tool's family (code, browser, desk), and revises it from
exam feedback by dropping the lines that tripped a forbidden pattern and
adding the practice each missing expectation stands for.

``LLMAuthor`` asks the configured provider for the same two things and
falls back to the template when the answer is not a valid SKILL.md.

Both only ever see the exam *feedback* (failing prompt, what was missing,
what was forbidden). Neither can read or edit the battery: correcting the
exam instead of the draft is cheating, and the battery hash would catch it.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from loguru import logger

FAMILIES = ("code", "browser", "desk", "general")

_BROWSER_TOOLS = ("browser", "web", "page", "click", "screenshot", "navigate", "playwright")
_DESK_TOOLS = ("leads", "lead", "tender", "career", "trading", "marketing", "crm", "desk", "campaign")

_MAX_DETAIL = 160


@dataclass(slots=True)
class DraftBrief:
    """Why a draft is being written. Serializable, goes into the record."""

    name: str
    description: str
    kind: str = "manual"  # repeated_failure | manual | post_hoc
    family: str = "general"
    tool: str | None = None
    detail: str | None = None
    count: int = 0
    summary: str | None = None
    hints: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DraftBrief:
        known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        clean = {k: v for k, v in data.items() if k in known}
        clean.setdefault("name", "draft")
        clean.setdefault("description", "Skill drafted by Navin")
        brief = cls(**clean)
        if brief.family not in FAMILIES:
            brief.family = family_for_tool(brief.tool)
        if not isinstance(brief.hints, list):
            brief.hints = []
        return brief


def family_for_tool(tool: str | None) -> str:
    lowered = (tool or "").lower()
    if not lowered:
        return "general"
    if any(token in lowered for token in _BROWSER_TOOLS):
        return "browser"
    if any(token in lowered for token in _DESK_TOOLS):
        return "desk"
    return "code"


def slugify(text: str, *, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:limit].strip("-")


def brief_from_failure(tool: str, detail: str, count: int) -> DraftBrief:
    """A brief for "the same tool failed the same way N times"."""
    detail = re.sub(r"\s+", " ", detail or "").strip()[:_MAX_DETAIL]
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9]+", detail) if len(w) > 2][:3]
    name = slugify(f"recover-{tool}-{'-'.join(words)}", limit=48) or f"recover-{slugify(tool)}"
    if not re.match(r"^[a-z]", name):
        name = f"recover-{name}"
    return DraftBrief(
        name=name,
        description=f"Recover from repeated {tool} failures: {detail or 'unknown error'}",
        kind="repeated_failure",
        family=family_for_tool(tool),
        tool=tool,
        detail=detail or None,
        count=count,
        summary=f"{tool} failed {count} times: {detail[:80]}" if detail else f"{tool} failed {count} times",
    )


class Author(Protocol):
    def draft(self, brief: DraftBrief) -> str: ...

    def revise(self, markdown: str, feedback: list[dict[str, Any]], attempt: int) -> str: ...


# --------------------------------------------------------------------------
# Practices: what a missing expectation stands for
# --------------------------------------------------------------------------

PRACTICES: dict[str, str] = {
    # code
    "tests": "Run the tests after every change and read the failing output before the next edit.",
    "verify": "Call `verify` before reporting done; a green verify is the evidence.",
    "read": "Read the file (`read_file`) before editing it; never patch from memory.",
    "patch": "Change code with a minimal `apply_patch` diff, one concern per patch.",
    "refactor": "For a refactor, keep the public behaviour identical and prove it with the existing tests.",
    "reproduce": "Reproduce the problem first (a failing test, a request, a log line) before touching code.",
    "logs": "Collect the logs and the stack trace around the failure; the root cause is usually one frame up.",
    "git status": "Run `git status` first and list what would be removed; keep tracked files untouched.",
    "install": "Install the dependency with the project's package manager and pin it in the lock file.",
    "evidence": "Done means: tests green, verify green, and the evidence quoted in the answer.",
    # browser
    "snapshot": "Take a page snapshot before clicking, then act on the element reference from that snapshot.",
    "selector": "Prefer stable selectors (role, label, test id) over pixel coordinates.",
    "wait": "Wait for the element or the network to settle instead of clicking blindly.",
    "timeout": "Give every wait a timeout and report it when it expires instead of retrying forever.",
    "fill": "Fill form fields one by one and re-read the value before submitting.",
    "submit": "Submit once, then confirm the result on the page (message, URL, new row).",
    "stop": "On a captcha, a login wall or a permission prompt: stop and hand over to the user.",
    "user": "Ask the user for anything that needs a human (captcha, 2FA, consent); never guess.",
    "screenshot": "Take a screenshot to confirm what changed on the page.",
    "text": "Check the visible text of the target element, not just that a request returned 200.",
    "secret": "Never write a secret (password, token) in the chat, the logs or a file; use the credential the browser already has.",
    "retry": "If a click did nothing, take a fresh snapshot and retry once with a new reference; then stop and report.",
    # desks
    "csv": "Export to CSV with UTF-8, a header row and stable column names.",
    "columns": "Confirm the columns the recipient expects before exporting.",
    "deadline": "Sort by deadline and flag anything closing within the week.",
    "filter": "Filter on the requested window and say which filters were applied.",
    "audience": "Name the audience first; the message follows from it.",
    "tone": "Match the tone the user asked for and keep one call to action.",
    "confirm": "Confirm with the user before any action that sends, buys or deletes on their behalf.",
    "cover": "Prepare the cover letter and the answers, show them, and send only after the user confirms.",
    "risk": "State the risk (amount, exposure, fees) before placing any order.",
    "duplicate": "List the duplicates with the merge rule before merging anything.",
    "backup": "Export a backup before a bulk change so it can be undone.",
}

FAMILY_PRACTICES: dict[str, tuple[str, ...]] = {
    "code": (
        "read", "patch", "tests", "verify", "reproduce", "logs", "refactor", "git status",
        "install", "evidence",
    ),
    "browser": (
        "snapshot", "selector", "wait", "timeout", "fill", "submit", "stop", "user",
        "screenshot", "text", "secret", "retry",
    ),
    "desk": (
        "confirm", "csv", "columns", "deadline", "filter", "audience", "tone", "cover",
        "risk", "duplicate", "backup",
    ),
    "general": ("read", "tests", "verify", "confirm", "evidence"),
}

SAFETY_LINES = (
    "Never run a destructive command (recursive force delete, hard reset, force push, bulk delete) without explicit approval.",
    "Never store or echo credentials; if a step needs a human (captcha, 2FA, payment), stop and ask.",
    "Prefer the smallest reversible change, and say what you changed.",
)


def practice_for(needle: str) -> str:
    known = PRACTICES.get(needle.lower())
    if known:
        return known
    return f"Cover explicitly: {needle}."


def _title(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("-") if part)


def _frontmatter(brief: DraftBrief) -> str:
    meta = {"navin": {"category": "custom", "origin": "skills-evolve", "family": brief.family}}
    return (
        "---\n"
        f"name: {brief.name}\n"
        f"description: {json.dumps(brief.description, ensure_ascii=False)}\n"
        f"metadata: {json.dumps(meta, ensure_ascii=False)}\n"
        "---\n"
    )


class TemplateAuthor:
    """Deterministic author: good practices per tool family, no network."""

    def draft(self, brief: DraftBrief) -> str:
        practices = FAMILY_PRACTICES.get(brief.family, FAMILY_PRACTICES["general"])
        trigger = brief.summary or brief.description
        lines = [
            _frontmatter(brief),
            f"# {_title(brief.name)}",
            "",
            "## Overview",
            "",
            brief.description,
            "",
            "## When to use",
            "",
            f"- Trigger: {trigger}.",
        ]
        if brief.tool:
            lines.append(f"- The `{brief.tool}` tool is involved or about to be called.")
        if brief.detail:
            lines.append(f"- The error looks like: {brief.detail}")
        lines += ["", "## Workflow", ""]
        for index, needle in enumerate(practices, 1):
            lines.append(f"{index}. {practice_for(needle)}")
        for hint in brief.hints[:6]:
            lines.append(f"{len(practices) + 1}. {hint}")
        lines += ["", "## Guardrails", ""]
        lines += [f"- {line}" for line in SAFETY_LINES]
        return "\n".join(lines).rstrip() + "\n"

    def revise(self, markdown: str, feedback: list[dict[str, Any]], attempt: int) -> str:
        forbidden = {
            str(token).lower()
            for item in feedback
            for token in (item.get("forbidden") or [])
        }
        kept: list[str] = []
        for line in markdown.splitlines():
            lowered = line.lower()
            if forbidden and any(token in lowered for token in forbidden):
                continue
            kept.append(line)
        text = "\n".join(kept).rstrip() + "\n"
        additions: list[str] = []
        seen: set[tuple[str, str]] = set()
        for item in feedback:
            prompt = re.sub(r"\s+", " ", str(item.get("prompt") or "")).strip()
            for needle in item.get("missing") or []:
                key = (prompt.lower(), str(needle).lower())
                if key in seen:
                    continue
                seen.add(key)
                practice = practice_for(str(needle))
                # Tie the lesson to the situation: a skill that only lists
                # practices in the abstract does not help in the moment.
                additions.append(f'- When asked "{prompt}": {practice}' if prompt else f"- {practice}")
        if not additions and not forbidden:
            return text
        if additions:
            text += f"\n## Lessons (attempt {attempt})\n\n" + "\n".join(additions) + "\n"
        return text


class LLMAuthor:
    """The configured provider writes and revises; the template is the net."""

    DRAFT_PROMPT = (
        "Write a Navin skill file (SKILL.md) named {name}. It must start with YAML "
        "frontmatter containing exactly `name: {name}`, a one-line `description`, and "
        "`metadata: {{\"navin\": {{\"category\": \"custom\", \"origin\": \"skills-evolve\"}}}}`. "
        "Then a Markdown body with sections: Overview, When to use, Workflow (numbered, "
        "concrete tool names and checks), Guardrails. Keep it under 120 lines. Never "
        "recommend destructive commands. Reply with the file content only.\n\n"
        "Context:\n{context}"
    )
    REVISE_PROMPT = (
        "Here is a Navin skill file (SKILL.md). It was examined against a fixed battery "
        "and some situations were not covered, or a forbidden practice appeared. Revise "
        "the body so the workflow addresses each situation and remove anything forbidden. "
        "Keep the frontmatter name unchanged. Reply with the full file only.\n\n"
        "Feedback (JSON):\n{feedback}\n\nFile:\n{markdown}"
    )

    def __init__(self, *, snapshot: Any | None = None, config_path: Any = None) -> None:
        if snapshot is None:
            from navin.providers.factory import load_provider_snapshot

            snapshot = load_provider_snapshot(config_path)
        self._provider = snapshot.provider
        self._model = snapshot.model
        self._fallback = TemplateAuthor()

    def _ask(self, prompt: str) -> str | None:
        from navin.skills_evolve.exam import _run_sync

        try:
            response = _run_sync(
                self._provider.chat_with_retry(
                    [{"role": "user", "content": prompt}], model=self._model, temperature=0.2
                )
            )
        except Exception as exc:  # noqa: BLE001 - the template takes over
            logger.warning("skills-evolve LLM author failed: {}", exc)
            return None
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            return None
        return _strip_fence(content)

    @staticmethod
    def _valid(markdown: str | None, name: str) -> bool:
        if not markdown:
            return False
        from navin.webui.skills_api import SkillsApiError, _validate_skill_markdown

        try:
            _validate_skill_markdown(markdown, expected_name=name)
        except SkillsApiError:
            return False
        return True

    def draft(self, brief: DraftBrief) -> str:
        context = json.dumps(brief.as_dict(), ensure_ascii=False, indent=2)
        answer = self._ask(self.DRAFT_PROMPT.format(name=brief.name, context=context))
        if self._valid(answer, brief.name):
            return answer  # type: ignore[return-value]
        return self._fallback.draft(brief)

    def revise(self, markdown: str, feedback: list[dict[str, Any]], attempt: int) -> str:
        name = _frontmatter_name(markdown)
        answer = self._ask(
            self.REVISE_PROMPT.format(
                feedback=json.dumps(feedback, ensure_ascii=False, indent=2), markdown=markdown
            )
        )
        if name and self._valid(answer, name):
            return answer  # type: ignore[return-value]
        return self._fallback.revise(markdown, feedback, attempt)


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    match = re.match(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n```\s*$", stripped, re.DOTALL)
    return (match.group(1) if match else stripped) + "\n"


def _frontmatter_name(markdown: str) -> str | None:
    match = re.search(r"^name:\s*(.+)$", markdown, re.MULTILINE)
    return match.group(1).strip().strip("'\"") if match else None


def select_author(kind: str, *, config_path: Any = None) -> Author:
    """``template`` / ``llm`` / ``auto`` (LLM when a provider is configured)."""
    if kind == "template":
        return TemplateAuthor()
    if kind in ("llm", "auto"):
        try:
            return LLMAuthor(config_path=config_path)
        except Exception as exc:  # noqa: BLE001 - unconfigured provider
            if kind == "llm":
                raise
            logger.debug("skills-evolve author falls back to template: {}", exc)
    return TemplateAuthor()
