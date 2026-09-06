"""Best-of-N candidate generation with an independent judge.

For decisions where a single trajectory is the risk - an architecture
choice, a security-sensitive review verdict, a plan for an irreversible
migration - one sampled answer is one roll of the dice. This module rolls
N dice and then has a judge, prompted as an outsider who wrote none of
the candidates, score each against explicit criteria and pick a winner.

Two design points matter for the judge's independence:

- Candidates are shuffled and anonymized (Candidate A, B, ...), so the
  judge cannot favor "the first answer" or the generation order.
- The judge runs at temperature 0 with a strict-JSON contract, so the
  verdict is reproducible and machine-checkable rather than vibes.

When the judge's output cannot be parsed, the result says so honestly
(``judge_ok=False``) and falls back to the first candidate instead of
silently pretending a verdict happened.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

from navin.utils.llm_runtime import LLMRuntime

_MIN_N = 2
_MAX_N = 4
_DEFAULT_CRITERIA = (
    "technical correctness, completeness against the task, risk awareness "
    "(what could break and how the candidate mitigates it), and simplicity"
)
_JUDGE_MAX_TOKENS = 1500
_LABELS = "ABCD"

_CANDIDATE_SYSTEM = (
    "You are an expert software engineer producing one complete, "
    "self-contained proposal for the task below. Be specific and "
    "actionable: name files, steps, checks and failure modes. Commit to "
    "one approach; do not enumerate alternatives or hedge."
)

_JUDGE_SYSTEM = (
    "You are an independent reviewer. You did not write any of the "
    "candidate proposals below and you have no stake in which one wins. "
    "Score each candidate from 0 to 10 against these criteria: {criteria}. "
    "Then pick exactly one winner.\n"
    "Answer with strict JSON only, no prose around it, in this shape:\n"
    '{{"scores": [{{"candidate": "A", "score": 7, "reason": "..."}}], '
    '"winner": "A", "rationale": "..."}}'
)


class BestOfError(RuntimeError):
    """Raised when no candidate could be generated at all."""


@dataclass(frozen=True, slots=True)
class CandidateVerdict:
    """The judge's score for one candidate, in original generation order."""

    index: int
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class BestOfResult:
    winner: int
    candidates: tuple[str, ...]
    verdicts: tuple[CandidateVerdict, ...]
    rationale: str
    judge_ok: bool

    def render(self) -> str:
        lines = [
            f"Best-of-{len(self.candidates)}: candidate #{self.winner + 1} wins."
        ]
        if self.verdicts:
            lines.append("")
            lines.append("Judge scores:")
            for verdict in sorted(self.verdicts, key=lambda v: -v.score):
                mark = " (winner)" if verdict.index == self.winner else ""
                lines.append(
                    f"  - candidate #{verdict.index + 1}: "
                    f"{verdict.score:g}/10{mark} - {verdict.reason}"
                )
        if self.rationale:
            lines.append("")
            lines.append(f"Judge rationale: {self.rationale}")
        if not self.judge_ok:
            lines.append("")
            lines.append(
                "Note: the judge's verdict could not be parsed; the first "
                "candidate was kept by default. Treat the pick with care."
            )
        lines.append("")
        lines.append("Winning proposal:")
        lines.append(self.candidates[self.winner])
        return "\n".join(lines)


def _candidate_temperatures(base: float, n: int) -> list[float]:
    """Spread temperatures around the base so candidates actually differ.

    Identical settings often produce near-identical answers, which makes
    the whole exercise theater. The first candidate keeps the session's
    own temperature; the rest step upward, capped at 1.0.
    """
    first = max(0.2, min(1.0, base))
    return [min(1.0, first + 0.15 * i) for i in range(n)]


async def _generate_candidates(
    runtime: LLMRuntime,
    task: str,
    context: str | None,
    n: int,
) -> list[str]:
    user_content = task if not context else f"{task}\n\nContext:\n{context}"
    messages = [
        {"role": "system", "content": _CANDIDATE_SYSTEM},
        {"role": "user", "content": user_content},
    ]

    async def _one(temperature: float) -> str | None:
        try:
            response = await runtime.provider.chat(
                messages=[dict(m) for m in messages],
                model=runtime.model,
                max_tokens=runtime.generation.max_tokens,
                temperature=temperature,
                reasoning_effort=runtime.generation.reasoning_effort,
            )
        except Exception as exc:
            logger.debug("best_of candidate generation failed: {}", exc)
            return None
        if response.finish_reason == "error":
            return None
        content = (response.content or "").strip()
        return content or None

    temperatures = _candidate_temperatures(runtime.generation.temperature, n)
    results = await asyncio.gather(*(_one(t) for t in temperatures))
    return [candidate for candidate in results if candidate]


def _parse_judge_json(raw: str) -> dict[str, Any] | None:
    """Extract the first JSON object from the judge's reply, or None."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|```$", "", text, flags=re.MULTILINE)
    start = text.find("{")
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _judge(
    runtime: LLMRuntime,
    task: str,
    candidates: list[str],
    criteria: str,
    *,
    seed: int | None = None,
) -> tuple[int, list[CandidateVerdict], str, bool]:
    """Score candidates and pick a winner; returns original-order results."""
    order = list(range(len(candidates)))
    random.Random(seed).shuffle(order)
    label_to_index = {_LABELS[pos]: original for pos, original in enumerate(order)}

    blocks = [
        f"Candidate {_LABELS[pos]}:\n{candidates[original]}"
        for pos, original in enumerate(order)
    ]
    messages = [
        {"role": "system", "content": _JUDGE_SYSTEM.format(criteria=criteria)},
        {
            "role": "user",
            "content": f"Task:\n{task}\n\n" + "\n\n---\n\n".join(blocks),
        },
    ]
    try:
        response = await runtime.provider.chat(
            messages=messages,
            model=runtime.model,
            max_tokens=_JUDGE_MAX_TOKENS,
            temperature=0.0,
            reasoning_effort=runtime.generation.reasoning_effort,
        )
        raw = (response.content or "").strip()
    except Exception as exc:
        logger.debug("best_of judge call failed: {}", exc)
        raw = ""

    parsed = _parse_judge_json(raw) if raw else None
    if parsed is None:
        return 0, [], raw[:500], False

    verdicts: list[CandidateVerdict] = []
    for row in parsed.get("scores", []) or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("candidate", "")).strip().upper()[:1]
        if label not in label_to_index:
            continue
        try:
            score = max(0.0, min(10.0, float(row.get("score", 0))))
        except (TypeError, ValueError):
            score = 0.0
        verdicts.append(
            CandidateVerdict(
                index=label_to_index[label],
                score=score,
                reason=str(row.get("reason", "")).strip()[:400],
            )
        )

    winner_label = str(parsed.get("winner", "")).strip().upper()[:1]
    if winner_label in label_to_index:
        winner = label_to_index[winner_label]
    elif verdicts:
        winner = max(verdicts, key=lambda v: v.score).index
    else:
        return 0, [], raw[:500], False
    rationale = str(parsed.get("rationale", "")).strip()[:800]
    return winner, verdicts, rationale, True


async def best_of_n(
    runtime: LLMRuntime,
    task: str,
    *,
    n: int = 3,
    criteria: str | None = None,
    context: str | None = None,
    judge_seed: int | None = None,
) -> BestOfResult:
    """Generate ``n`` independent candidates and let a judge pick one."""
    clean_task = (task or "").strip()
    if not clean_task:
        raise BestOfError("best_of_n needs a task")
    n = max(_MIN_N, min(_MAX_N, int(n)))

    candidates = await _generate_candidates(runtime, clean_task, context, n)
    if not candidates:
        raise BestOfError("no candidate could be generated (provider errors)")
    if len(candidates) == 1:
        # Nothing to compare; be explicit rather than staging a fake contest.
        return BestOfResult(
            winner=0,
            candidates=tuple(candidates),
            verdicts=(),
            rationale="only one candidate was generated; no comparison happened",
            judge_ok=False,
        )

    winner, verdicts, rationale, judge_ok = await _judge(
        runtime,
        clean_task,
        candidates,
        criteria or _DEFAULT_CRITERIA,
        seed=judge_seed,
    )
    return BestOfResult(
        winner=winner,
        candidates=tuple(candidates),
        verdicts=tuple(verdicts),
        rationale=rationale,
        judge_ok=judge_ok,
    )
