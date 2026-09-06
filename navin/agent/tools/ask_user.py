"""Ask the user to pick a path when the next step is a real fork."""

from __future__ import annotations

import re
from typing import Any

from navin.agent.choice import OTHER_OPTION_ID, ChoiceOption, ChoiceRequest, request_choice
from navin.agent.tools.base import Tool, ToolResult

_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,16}$")
_RESERVED_IDS = frozenset({OTHER_OPTION_ID})


class AskUserTool(Tool):
    """Stop the turn and show a choice card with a recommended option."""

    _scopes = {"core"}

    @property
    def read_only(self) -> bool:
        return True

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "ask_user"

    @property
    def description(self) -> str:
        return (
            "Stop and ask the user to pick a path when you do not know which "
            "fork to take. Use this for scope, approach, risk, time, or any "
            "decision that would waste work if guessed. Give 2 to 4 concrete "
            "options, mark exactly one as recommended, and explain each in one "
            "line (time, trade-off, or outcome). The card always adds a last "
            "option where the user types their own answer: if they use it, "
            "follow that text and do not substitute a listed option. Do not "
            "use this for a yes/no on a dangerous command (that is an "
            "approval). After the user answers, follow that path; if they "
            "skip, take the recommended one and say so."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The decision in one sentence, in the user's language.",
                },
                "options": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Short id: a, b, c, or a slug.",
                            },
                            "label": {
                                "type": "string",
                                "description": "What the user is choosing, one line.",
                            },
                            "detail": {
                                "type": "string",
                                "description": (
                                    "Why this path: time, trade-off, or outcome."
                                ),
                            },
                            "recommended": {
                                "type": "boolean",
                                "description": "True on exactly one option.",
                            },
                        },
                        "required": ["id", "label"],
                    },
                    "description": "Two to four paths. Exactly one recommended.",
                },
                "allow_skip": {
                    "type": "boolean",
                    "description": "Offer Skip. Default true. Skip takes the recommended path.",
                },
            },
            "required": ["question", "options"],
        }

    async def execute(
        self,
        question: str = "",
        options: list[dict[str, Any]] | None = None,
        allow_skip: bool = True,
        **_: Any,
    ) -> ToolResult:
        parsed, error = _parse_options(options or [])
        if error:
            return ToolResult(error)
        text = str(question or "").strip()
        if not text or len(text) > 240:
            return ToolResult("question must be 1 to 240 characters.")
        answer = await request_choice(
            ChoiceRequest(
                question=text,
                options=tuple(parsed),
                allow_skip=bool(allow_skip),
            )
        )
        return ToolResult(answer.as_text())


def _parse_options(raw: list[dict[str, Any]]) -> tuple[list[ChoiceOption], str]:
    if not isinstance(raw, list) or not 2 <= len(raw) <= 4:
        return [], "Give 2 to 4 options."
    out: list[ChoiceOption] = []
    seen: set[str] = set()
    recommended = 0
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            return [], "Each option must be an object."
        option_id = str(entry.get("id") or "").strip() or chr(ord("a") + index)
        if (
            not _ID_RE.match(option_id)
            or option_id.lower() in seen
            or option_id.lower() in _RESERVED_IDS
        ):
            return [], "Each option needs a unique short id."
        seen.add(option_id.lower())
        label = str(entry.get("label") or "").strip()
        if not label or len(label) > 120:
            return [], "Each option needs a label of 1 to 120 characters."
        detail = str(entry.get("detail") or "").strip()[:200]
        is_recommended = bool(entry.get("recommended"))
        if is_recommended:
            recommended += 1
        out.append(
            ChoiceOption(
                id=option_id,
                label=label,
                detail=detail,
                recommended=is_recommended,
            )
        )
    if recommended == 0:
        out[0] = ChoiceOption(
            id=out[0].id,
            label=out[0].label,
            detail=out[0].detail,
            recommended=True,
        )
    elif recommended > 1:
        first = True
        fixed: list[ChoiceOption] = []
        for option in out:
            flag = option.recommended and first
            if option.recommended:
                first = False
            fixed.append(
                ChoiceOption(
                    id=option.id,
                    label=option.label,
                    detail=option.detail,
                    recommended=flag,
                )
            )
        out = fixed
    return out, ""
