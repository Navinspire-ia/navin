"""The corridor: draft, exam, correct up to K times, promote (S2.1 to S2.4).

``run_pipeline`` is the only function a job runner or the CLI needs. It
runs **outside** any agent turn (a daemon thread fed by a queue, a cron, or
``navin agi run``), and does nothing when the project flag is off.

Order of operations::

    author.draft(brief)            -> .navin/skills-draft/<name>/SKILL.md
    baseline = exam(project skills)
    for attempt in 1..K:
        candidate = exam(project skills + draft)      # in a sandbox copy
        verdict   = grade(candidate, baseline)
        keep the best version (highest score without a regression)
        eligible? stop : author.revise(draft, feedback)
    eligible  -> status eligible, then promote when promote_project is on
    flat      -> kept for a human "force"
    down      -> discarded (journal keeps the trace)

The battery is re-hashed after every exam. If it changed, the run stops as
``tampered`` and nothing is promoted.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.skills_evolve.author import Author, DraftBrief, select_author
from navin.skills_evolve.battery import Battery, ExamTamperedError, load_battery
from navin.skills_evolve.drafts import (
    DraftError,
    DraftRecord,
    journal,
    read_draft,
    read_draft_markdown,
    save_draft,
    write_draft,
)
from navin.skills_evolve.exam import (
    ExamBudget,
    ExamReport,
    ModelFactory,
    Verdict,
    feedback_for,
    grade,
    model_factory_for,
    run_exam,
)
from navin.skills_evolve.paths import baseline_cache_file, draft_skill_file
from navin.skills_evolve.promote import PromotionError, discard_draft, promote_draft
from navin.skills_evolve.sandbox import ExamSandbox, project_skill_texts
from navin.skills_evolve.settings import SkillsEvolveSettings, read_settings


@dataclass(slots=True)
class PipelineDeps:
    """Everything the corridor needs that a test may want to swap."""

    author: Author
    model_factory: ModelFactory
    battery: Battery
    budget: ExamBudget = field(default_factory=ExamBudget)


def default_deps(settings: SkillsEvolveSettings, *, config_path: Any = None) -> PipelineDeps:
    return PipelineDeps(
        author=select_author(settings.author, config_path=config_path),
        model_factory=model_factory_for(settings.exam_model),
        battery=load_battery(),
    )


@dataclass(slots=True)
class PipelineResult:
    name: str
    status: str
    attempts: int = 0
    baseline_score: int | None = None
    best_score: int | None = None
    verdict: dict[str, Any] | None = None
    promoted: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "attempts": self.attempts,
            "baseline_score": self.baseline_score,
            "best_score": self.best_score,
            "verdict": self.verdict,
            "promoted": self.promoted,
            "reason": self.reason,
        }


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


def _skills_fingerprint(texts: list[str]) -> str:
    digest = hashlib.sha256()
    for text in texts:
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def baseline_report(
    workspace: Path | str,
    deps: PipelineDeps,
    *,
    exclude: str | None = None,
    use_cache: bool = True,
) -> ExamReport:
    """Exam of the project skill layer as it is (``exclude`` left out).

    Cached per battery version and skill-layer fingerprint under the draft
    folder, so ten drafts in a row pay one baseline.
    """
    texts = project_skill_texts(workspace, exclude=exclude)
    fingerprint = _skills_fingerprint(texts)
    cache = baseline_cache_file(workspace, deps.battery.version)
    if use_cache and cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("skills_fingerprint") == fingerprint:
                return ExamReport.from_dict(data)
        except (OSError, json.JSONDecodeError, ValueError, KeyError):
            pass
    report = run_exam(deps.battery, texts, model_factory=deps.model_factory, budget=deps.budget)
    if use_cache and not report.failed:
        payload = report.as_dict()
        payload["skills_fingerprint"] = fingerprint
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
    return report


def examine_markdown(
    workspace: Path | str,
    name: str,
    markdown: str,
    deps: PipelineDeps,
    baseline: ExamReport,
) -> tuple[ExamReport, Verdict]:
    """Exam of the project layer plus one candidate text, in a sandbox copy."""
    with ExamSandbox(workspace) as sandbox:
        sandbox.place(name, markdown)
        texts = sandbox.skill_texts()
    report = run_exam(deps.battery, texts, model_factory=deps.model_factory, budget=deps.budget)
    return report, grade(report, baseline)


# --------------------------------------------------------------------------
# Corridor
# --------------------------------------------------------------------------


def focused_feedback(report: ExamReport, verdict: Verdict, family: str) -> list[dict[str, Any]]:
    """The failures the corrector should act on, and only those.

    Every forbidden hit (a regression anywhere) and every miss in a suite that
    went ``down``, plus the misses of the draft's own family. Misses in an
    unrelated suite that stayed flat are not this skill's job: a browser
    draft must not grow a trading chapter to pass the exam.
    """
    focused: list[dict[str, Any]] = []
    for item in feedback_for(report):
        suite = str(item.get("suite") or "")
        if item.get("forbidden") or verdict.suites.get(suite) == "down" or suite == family:
            focused.append(item)
    return focused


def _better(candidate: tuple[ExamReport, Verdict], best: tuple[ExamReport, Verdict] | None) -> bool:
    """Best = no regression first, then the higher score."""
    if best is None:
        return True
    report, verdict = candidate
    best_report, best_verdict = best
    if verdict.regressed != best_verdict.regressed:
        return not verdict.regressed
    return report.score > best_report.score


def run_pipeline(
    workspace: Path | str,
    brief: DraftBrief | dict[str, Any],
    deps: PipelineDeps | None = None,
    *,
    max_attempts: int | None = None,
) -> PipelineResult:
    workspace = Path(workspace)
    if isinstance(brief, dict):
        brief = DraftBrief.from_dict(brief)
    settings = read_settings(workspace)
    if not settings.feature("draft"):
        return PipelineResult(name=brief.name, status="skipped", reason="skills-evolve flag off")
    deps = deps or default_deps(settings)
    attempts_max = max(1, max_attempts or settings.max_attempts)

    try:
        markdown = deps.author.draft(brief)
        record = write_draft(workspace, brief.name, markdown, origin=brief.as_dict())
    except DraftError as exc:
        journal(workspace, "invalid", name=brief.name, reason=str(exc))
        return PipelineResult(name=brief.name, status="rejected", reason=str(exc))
    name = record.name

    try:
        baseline = baseline_report(workspace, deps)
        record.baseline = baseline.as_dict()
        record.battery_version = baseline.battery_version
        if baseline.failed:
            raise PromotionError(f"baseline exam failed: {baseline.reason}")

        best: tuple[ExamReport, Verdict] | None = None
        best_markdown = markdown
        for attempt in range(1, attempts_max + 1):
            record.touch("examining")
            save_draft(workspace, record)
            report, verdict = examine_markdown(workspace, name, markdown, deps, baseline)
            record.attempts = attempt
            record.push_history(
                {
                    "attempt": attempt,
                    "score": report.score,
                    "verdict": verdict.overall,
                    "suites": verdict.suites,
                    "failed": report.failed,
                }
            )
            journal(
                workspace,
                "examined",
                name=name,
                attempt=attempt,
                score=report.score,
                baseline=baseline.score,
                verdict=verdict.overall,
                suites=verdict.suites,
                battery=baseline.battery_version,
                reason=report.reason,
            )
            if _better((report, verdict), best):
                best = (report, verdict)
                best_markdown = markdown
            if verdict.eligible:
                break
            if attempt >= attempts_max:
                break
            revised = deps.author.revise(
                markdown, focused_feedback(report, verdict, brief.family), attempt
            )
            if not revised or revised == markdown:
                journal(workspace, "no_change", name=name, attempt=attempt)
                break
            markdown = revised
            journal(workspace, "revised", name=name, attempt=attempt)

        assert best is not None
        best_report, best_verdict = best
        draft_skill_file(workspace, name).write_text(best_markdown, encoding="utf-8")
        record.best = best_report.as_dict()
        record.verdict = best_verdict.as_dict()
        result = PipelineResult(
            name=name,
            attempts=record.attempts,
            status="eligible",
            baseline_score=baseline.score,
            best_score=best_report.score,
            verdict=best_verdict.as_dict(),
        )

        if best_verdict.eligible:
            record.touch("eligible")
            save_draft(workspace, record)
            journal(workspace, "kept", name=name, status="eligible", score=best_report.score)
            if settings.feature("promote_project"):
                try:
                    promote_draft(workspace, name, actor="auto")
                    result.status = "promoted"
                    result.promoted = True
                except PromotionError as exc:
                    result.status = "rollback"
                    result.reason = str(exc)
            return result

        if best_verdict.regressed:
            reason = best_verdict.reason or "regression on the frozen battery"
            record.touch("rejected")
            record.note = reason
            save_draft(workspace, record)
            discard_draft(workspace, name, actor="auto", reason=reason)
            result.status = "discarded"
            result.reason = reason
            return result

        record.touch("flat")
        record.note = "no regression, no progress: a human may force it"
        save_draft(workspace, record)
        journal(workspace, "kept", name=name, status="flat", score=best_report.score)
        result.status = "flat"
        return result

    except ExamTamperedError as exc:
        record.touch("rejected")
        record.note = str(exc)
        save_draft(workspace, record)
        journal(workspace, "tampered", name=name, reason=str(exc))
        logger.error("skills-evolve: {}", exc)
        return PipelineResult(name=name, status="tampered", attempts=record.attempts, reason=str(exc))
    except PromotionError as exc:
        record.touch("rejected")
        record.note = str(exc)
        save_draft(workspace, record)
        journal(workspace, "kept", name=name, status="rejected", reason=str(exc))
        return PipelineResult(name=name, status="rejected", attempts=record.attempts, reason=str(exc))


def reexamine_draft(workspace: Path | str, name: str, deps: PipelineDeps | None = None) -> PipelineResult:
    """Run the *same* battery again on an existing draft (no new exam invented)."""
    workspace = Path(workspace)
    settings = read_settings(workspace)
    if not settings.enabled:
        return PipelineResult(name=name, status="skipped", reason="skills-evolve flag off")
    record: DraftRecord | None = read_draft(workspace, name)
    markdown = read_draft_markdown(workspace, name)
    if record is None or not markdown:
        raise DraftError(f"no draft named {name}")
    deps = deps or default_deps(settings)
    baseline = baseline_report(workspace, deps, exclude=name if record.status == "promoted" else None)
    report, verdict = examine_markdown(workspace, name, markdown, deps, baseline)
    record.attempts += 1
    record.baseline = baseline.as_dict()
    record.battery_version = baseline.battery_version
    record.best = report.as_dict()
    record.verdict = verdict.as_dict()
    record.push_history({"attempt": record.attempts, "score": report.score, "verdict": verdict.overall, "suites": verdict.suites})
    if record.status not in ("promoted", "retired"):
        record.touch("eligible" if verdict.eligible else ("rejected" if verdict.regressed else "flat"))
    else:
        record.touch()
    save_draft(workspace, record)
    journal(
        workspace,
        "examined",
        name=name,
        attempt=record.attempts,
        score=report.score,
        baseline=baseline.score,
        verdict=verdict.overall,
        suites=verdict.suites,
        battery=baseline.battery_version,
        manual=True,
    )
    return PipelineResult(
        name=name,
        status=record.status,
        attempts=record.attempts,
        baseline_score=baseline.score,
        best_score=report.score,
        verdict=verdict.as_dict(),
    )
