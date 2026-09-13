# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Generate, repair, compare and adopt skills using fresh execution evidence."""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy

from filelock import FileLock

from navin.skills_evolve.drafts import journal, read_draft, save_draft, write_draft
from navin.skills_evolve.execution import compare_execution
from navin.skills_evolve.execution_tools import ExecutionUnavailableError
from navin.skills_evolve.paths import draft_dir, draft_skill_file, drafts_dir, project_skill_file
from navin.skills_evolve.promote import PromotionError, promote_draft
from navin.skills_evolve.sandbox import project_skill_texts
from navin.skills_evolve.settings import read_settings


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def layer_digest(workspace) -> str:
    return digest(json.dumps(project_skill_texts(workspace), ensure_ascii=False))


def validate_promotion(workspace, record, markdown):
    proof = record.execution_evidence or {}
    if (proof.get("kind") != "paired_execution" or not proof.get("holdout_passed")
            or not proof.get("train_passed") or proof.get("candidate_digest") != digest(markdown)):
        raise PromotionError("Automatic adoption requires matching execution and held-out evidence.")
    if proof.get("baseline_digest") != layer_digest(workspace):
        raise PromotionError("The active skills changed after the comparison. Re-evaluate before adoption.")
    if not read_settings(workspace).feature("promote_project"):
        raise PromotionError("Automatic skill adoption is paused.")


def _version(workspace, name, markdown):
    folder = draft_dir(workspace, name) / "versions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{digest(markdown)}.md").write_text(markdown, encoding="utf-8")


def _restore_record(workspace, previous, parent, record, reason):
    if previous and parent:
        previous.push_history({"event": "candidate_rejected", "reason": reason})
        save_draft(workspace, previous)
        draft_skill_file(workspace, previous.name).write_text(parent, encoding="utf-8")
    elif record:
        record.touch("rejected")
        record.note = reason
        save_draft(workspace, record)


def run_execution_pipeline(workspace, brief, deps, *, max_attempts=None):
    from navin.skills_evolve.pipeline import PipelineResult

    root = drafts_dir(workspace)
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(root / "execution.lock"), timeout=2):
        existing = read_draft(workspace, brief.name)
        if existing and existing.status == "promoted" and existing.execution_evidence:
            # A new observed failure of an adopted correction first checks for
            # regression against its actual predecessor, then starts repair.
            execution_guard(workspace, deps, name=brief.name, _locked=True)
        elif brief.kind in {"repeated_failure", "observed_recovery"} and brief.module:
            execution_guard(workspace, deps, module=brief.module, automatic=True, _locked=True)
        return _run(workspace, brief, deps, max_attempts, PipelineResult)


def _run(workspace, brief, deps, max_attempts, result_type):
    settings = read_settings(workspace)
    if not settings.feature("draft"):
        return result_type(brief.name, "skipped", reason="Skill evolution is paused.")
    inferred = brief.module or (brief.tool if brief.tool in {"career", "tenders", "trading", "leads", "marketing", "crm"}
                               else {"code": "code", "browser": "scraping"}.get(brief.family))
    evaluator = deps.execution.for_module(inferred)
    evaluator.continue_check = lambda: read_settings(workspace).feature("draft")
    before = project_skill_texts(workspace)
    baseline_digest = layer_digest(workspace)
    path = project_skill_file(workspace, brief.name)
    parent = path.read_text(encoding="utf-8") if path.is_file() else ""
    previous = deepcopy(read_draft(workspace, brief.name))
    if parent:
        _version(workspace, brief.name, parent)
    record = None
    try:
        # Mutate the current accepted version, never restart from a fixed menu.
        markdown = deps.author.revise(parent, [{"observed_problem": brief.as_dict(),
                                               "instruction": "Improve the existing workflow using this evidence. Preserve useful prior corrections."}], 0) if parent else deps.author.draft(brief)
        record = write_draft(workspace, brief.name, markdown, origin=brief.as_dict())
        record.generation = previous.generation if previous else 0
        baseline = evaluator.evaluate(before)
        if baseline.failed:
            raise ExecutionUnavailableError(baseline.reason or "Baseline execution unavailable.")
        record.baseline = baseline.as_dict(with_outcomes=True)
        record.battery_version = baseline.battery_version
        training = None
        training_verdict = None
        attempt = 0
        for attempt in range(1, (max_attempts or settings.max_attempts) + 1):
            if not read_settings(workspace).feature("draft"):
                raise PromotionError("Skill evolution was paused during evaluation.")
            record.touch("examining")
            record.attempts = attempt
            save_draft(workspace, record)
            _version(workspace, brief.name, markdown)
            candidate_layer = [*project_skill_texts(workspace, exclude=brief.name), markdown]
            training = evaluator.evaluate(candidate_layer)
            training_verdict = compare_execution(training, baseline)
            record.best = training.as_dict(with_outcomes=True)
            record.verdict = training_verdict.as_dict()
            record.push_history({"event": "execution_exam", "attempt": attempt, "score": training.score,
                                 "baseline": baseline.score, "verdict": training_verdict.overall})
            save_draft(workspace, record)
            journal(workspace, "execution_exam", name=brief.name, attempt=attempt,
                    score=training.score, baseline=baseline.score, verdict=training_verdict.overall,
                    evaluation_kind="execution", model=evaluator.snapshot.model)
            if training.failed:
                raise ExecutionUnavailableError(training.reason or "Candidate execution unavailable.")
            if training_verdict.eligible:
                break
            if attempt < (max_attempts or settings.max_attempts):
                # Only development feedback is visible to the author. Held-out
                # task inputs and answers never enter this correction prompt.
                feedback = [{"suite": row.suite, "task": row.prompt, "failed_checks": list(row.missing),
                             "violations": list(row.forbidden)} for row in training.outcomes if not row.passed]
                if not feedback:
                    feedback = [{"instruction": "Behavior is correct. Improve efficiency while preserving all checks.",
                                 "execution_metrics": list(training.metrics)}]
                revised = deps.author.revise(markdown, feedback, attempt)
                if revised == markdown:
                    break
                write_draft(workspace, brief.name, revised, origin=brief.as_dict())
                markdown = revised
        if not training_verdict or not training_verdict.eligible:
            reason = "No measured execution improvement. The active skills are preserved."
            _restore_record(workspace, previous, parent, record, reason)
            journal(workspace, "execution_rejected", name=brief.name, reason=reason)
            return result_type(brief.name, "flat", attempts=attempt, baseline_score=baseline.score,
                               best_score=training.score if training else None, reason=reason)
        # This fresh, independently parameterized set is tested only once after
        # correction. A failure cannot be used to tune this candidate to the exam.
        candidate_layer = [*project_skill_texts(workspace, exclude=brief.name), markdown]
        if int(digest(evaluator.seed)[-1], 16) % 2:
            held_candidate = evaluator.evaluate(candidate_layer, split="holdout")
            held_baseline = evaluator.evaluate(before, split="holdout")
        else:
            held_baseline = evaluator.evaluate(before, split="holdout")
            held_candidate = evaluator.evaluate(candidate_layer, split="holdout")
        held_verdict = compare_execution(held_candidate, held_baseline)
        if held_candidate.failed or held_baseline.failed:
            raise ExecutionUnavailableError("Held-out execution unavailable. The active skills are preserved.")
        if not held_verdict.eligible:
            raise PromotionError("The proposed improvement did not generalize to held-out executions.")
        if baseline_digest != layer_digest(workspace):
            raise PromotionError("The active skills changed during evaluation. A new comparison is required.")
        if not evaluator.configuration_current():
            raise ExecutionUnavailableError("The configured model changed during evaluation.")
        record = write_draft(workspace, brief.name, markdown, origin=brief.as_dict())
        record.attempts = attempt
        record.generation = (previous.generation if previous else 0) + 1
        record.baseline, record.best = held_baseline.as_dict(with_outcomes=True), held_candidate.as_dict(with_outcomes=True)
        record.verdict = held_verdict.as_dict()
        record.battery_version = held_candidate.battery_version
        record.execution_evidence = {
            "kind": "paired_execution", "at": time.time(), "model": evaluator.snapshot.model,
            "provider_signature": evaluator.signature, "train_passed": True, "holdout_passed": True,
            "modules": list(dict.fromkeys(case.suite for case in evaluator.cases())),
            "task_ids": [case.id for case in evaluator.cases()],
            "baseline_digest": baseline_digest, "candidate_digest": digest(markdown),
            "parent_digest": digest(parent) if parent else None, "generation": record.generation,
            "train": {"before": baseline.as_dict(), "after": training.as_dict()},
            "holdout": {"before": held_baseline.as_dict(), "after": held_candidate.as_dict()},
        }
        record.touch("eligible")
        save_draft(workspace, record)
        journal(workspace, "execution_eligible", name=brief.name, generation=record.generation,
                before=held_baseline.score, after=held_candidate.score, model=evaluator.snapshot.model)
        promoted = False
        if read_settings(workspace).feature("promote_project"):
            promote_draft(workspace, brief.name, actor="auto")
            promoted = True
        return result_type(brief.name, "promoted" if promoted else "eligible", attempts=attempt,
                           baseline_score=held_baseline.score, best_score=held_candidate.score,
                           verdict=held_verdict.as_dict(), promoted=promoted)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {str(exc)[:300]}"
        _restore_record(workspace, previous, parent, record, reason)
        journal(workspace, "execution_rejected", name=brief.name, reason=reason)
        return result_type(brief.name, "retry" if isinstance(exc, (ExecutionUnavailableError, RuntimeError, OSError)) else "rejected", reason=reason)


def execution_guard(workspace, deps, *, name=None, module=None, automatic=False, _locked=False):
    from navin.skills_evolve.drafts import list_drafts
    from navin.skills_evolve.promote import rollback_promotion

    if not _locked:
        from filelock import Timeout
        try:
            with FileLock(str(drafts_dir(workspace) / "execution.lock"), timeout=2):
                return execution_guard(workspace, deps, name=name, module=module, automatic=automatic, _locked=True)
        except Timeout as exc:
            raise PromotionError("An execution comparison is already running for this project.") from exc
    checked, retired = [], []
    for record in list_drafts(workspace):
        if record.status != "promoted" or not record.execution_evidence:
            continue
        if name and record.name != name:
            continue
        if module and module not in record.execution_evidence.get("modules", []):
            continue
        if automatic and time.time() - record.execution_evidence.get("guard_checked_at", 0) < 6 * 3600:
            continue
        from dataclasses import replace
        evaluator = replace(deps.execution, modules=tuple(record.execution_evidence.get("modules") or ()) or None,
                            continue_check=lambda: read_settings(workspace).feature("draft"))
        path = project_skill_file(workspace, record.name)
        if not path.is_file() or digest(path.read_text()) != record.execution_evidence.get("candidate_digest"):
            # A user's subsequent edit is preserved; it is not the tested version.
            journal(workspace, "guard_skipped", name=record.name, reason="The active skill was edited outside evolution.")
            continue
        active = project_skill_texts(workspace)
        predecessor = project_skill_texts(workspace, exclude=record.name)
        if record.previous_markdown:
            predecessor.append(record.previous_markdown)
        before = evaluator.evaluate(predecessor, split="guard")
        after = evaluator.evaluate(active, split="guard")
        verdict = compare_execution(after, before)
        if not path.is_file() or digest(path.read_text()) != record.execution_evidence.get("candidate_digest"):
            journal(workspace, "guard_skipped", name=record.name, reason="The skill changed during evaluation; the user edit is preserved.")
            continue
        checked.append({"name": record.name, "verdict": verdict.overall, "before": before.score, "after": after.score})
        if not before.failed and not after.failed:
            record.execution_evidence["guard_checked_at"] = time.time()
            save_draft(workspace, record)
        if not before.failed and not after.failed and verdict.regressed:
            rollback_promotion(workspace, record.name, actor="guard")
            retired.append(record.name)
            journal(workspace, "execution_rollback", name=record.name, reason=verdict.reason)
            from navin.skills_evolve.experience import remember
            remember(workspace, "rollback", module=record.origin.get("module"), name=record.name, detail=verdict.reason or "Execution regression.")
    return {"status": "ok", "evaluation_kind": "execution", "checked": checked, "retired": retired}
