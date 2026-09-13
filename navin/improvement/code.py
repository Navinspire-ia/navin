# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Runner adapter: learn from verified revisions, never from final-answer claims."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from navin.improvement.engine import ImprovementEngine, Observation, Trial, fingerprint
from navin.improvement.policies import code_guidance


@dataclass
class CodeExperiment:
    engine: ImprovementEngine
    trial: Trial
    started: float
    initial_revision: int
    prompt_message: dict[str, Any] | None = None


def experiment_context(spec):
    provider = type(spec.runtime.provider)
    return {"version": 1, "model": spec.runtime.model, "provider": f"{provider.__module__}.{provider.__qualname__}",
            "mode": spec.composer_mode, "tools": sorted(spec.tools.tool_names), "denied": sorted(spec.denied_tools),
            "locked_denied": sorted(spec.locked_denied_tools),
            "allowed": sorted(spec.allowed_tools) if spec.allowed_tools is not None else None,
            "verify": spec.requires_verify_before_done, "validate": spec.validate_code_changes,
            "generation": {key: getattr(spec.runtime.generation, key, None) for key in ("temperature", "reasoning_effort", "max_tokens")}}


async def begin(spec, messages) -> CodeExperiment | None:
    if (spec.learning_evaluation or not spec.workspace or spec.read_only_tools or spec.plan_read_only
            or not (spec.requires_verify_before_done or spec.validate_code_changes)):
        return None
    try:
        engine = ImprovementEngine(Path(spec.workspace) / ".navin" / "improvement", "code")
        # Changing the model, gates or available tools starts separate evidence.
        trial = await asyncio.to_thread(engine.choose, experiment_context(spec))
        if trial is None:
            return None
        guidance = code_guidance(trial.policy)
        message = {"role": "system", "content": guidance} if guidance else None
        if message:
            messages.insert(1 if messages and messages[0].get("role") == "system" else 0, message)
        validation = spec.loop_guard.validation if spec.loop_guard else None
        return CodeExperiment(engine, trial, time.monotonic(), validation.revision if validation else 0, message)
    except Exception as exc:  # noqa: BLE001 - improvement cannot stop the user's work
        logger.debug("Code improvement unavailable: {}", type(exc).__name__)
        return None


async def finish(experiment: CodeExperiment | None, spec, result=None) -> None:
    if experiment is None:
        return
    try:
        validation = spec.loop_guard.validation if spec.loop_guard else None
        # Continuations, provider failures, cancellations and conversations that
        # made no code changes are not quality measurements of a coding policy.
        eligible = (result is not None and result.stop_reason not in {"cancelled", "max_iterations", "error"}
                    and not result.had_injections and validation is not None
                    and fingerprint(experiment_context(spec)) == experiment.trial.context
                    and validation.revision > experiment.initial_revision)
        if not eligible:
            await asyncio.to_thread(experiment.engine.abandon, experiment.trial)
            return
        verified = not validation.pending and not validation.failed
        success = verified and result.stop_reason == "completed"
        calls = len(result.tool_events)
        score = (.8 + .2 / (1 + calls / 10)) if success else 0
        # The runner marks policy denials as errors, with a structured prefix.
        boundary_prefixes = ("blocked by ", "ssrf_violation:", "workspace_violation:", "workspace_violation_escalated:")
        safe = not any(event.get("status") == "blocked" or (
            event.get("status") == "error" and str(event.get("detail", "")).startswith(boundary_prefixes)
        ) for event in result.tool_events)
        await asyncio.to_thread(experiment.engine.observe, experiment.trial, Observation(
            score, success, (time.monotonic() - experiment.started) * 1000,
            sum(result.usage.get(key, 0) for key in ("prompt_tokens", "completion_tokens")), safe))
    except Exception as exc:  # noqa: BLE001 - keep the main run's result
        logger.debug("Code improvement observation unavailable: {}", type(exc).__name__)


def remove_guidance(experiment: CodeExperiment | None, messages: list[dict[str, Any]]) -> None:
    if experiment is not None and experiment.prompt_message is not None:
        # Do not persist a turn's experiment into future conversation history.
        messages[:] = [message for message in messages if message != experiment.prompt_message]
