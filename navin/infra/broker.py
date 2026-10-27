# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Execution Broker: one infrastructure command, run under control.

1. The line is parsed and classified; shell operators are refused (one
   program per call, no pipes, no redirections).
2. The policy decides allow / ask / deny; the caller asks the user for ``ask``.
3. Before a change, the current state is captured (snapshot).
4. The program runs without a shell, with a timeout, with temporary
   credentials pinned by the policy (AWS profile, GCP impersonation).
5. The output is redacted (keys, tokens, passwords, secret data).
6. The change is verified; a failure is diagnosed and a recovery plan built.
7. Everything lands in the project audit log; a verified success marks the
   operation as tested.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from navin.infra.parse import Classification, classify_command, split_commands, tokenize, worst
from navin.infra.policy import Decision, InfraPolicy, current_contexts, decide, infra_dir
from navin.infra.registry import Registry
from navin.infra.runner import Runner, default_runner
from navin.infra.verify import (
    Check,
    check_passed,
    clean_kube_yaml,
    diagnose,
    recovery_plan,
    snapshot_plan,
    verification_plan,
)

_SHELL_OPERATORS = re.compile(r"(\|\||&&|[|;&<>`]|\$\()")
_SECRET_PATTERNS = [
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "<aws-access-key>"),
    (re.compile(r"(?i)(aws_secret_access_key|secretaccesskey|sessiontoken|aws_session_token)(\"?\s*[:=]\s*\"?)[A-Za-z0-9/+=]{16,}"), r"\1\2<redacted>"),
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{20,}"), r"\1 <redacted>"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})\b"), "<token>"),
    (re.compile(r"(?i)(\"?(?:password|passwd|secret|token|api[_-]?key|client[_-]?secret|private[_-]?key)\"?\s*[:=]\s*\"?)[^\s\",]{4,}"), r"\1<redacted>"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"), "<private-key>"),
    (re.compile(r"(?m)^(\s*(?:client-key-data|client-certificate-data|token|certificate-authority-data):\s*)\S+"), r"\1<redacted>"),
]
_MAX_OUTPUT = 12000


def redact(text: str) -> str:
    """Hide credentials and secret values before anything is shown or logged."""
    if not text:
        return ""
    out = text
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    if re.search(r"(?m)^kind:\s*Secret\b|\"kind\":\s*\"Secret\"", out):
        out = re.sub(r"(?m)^(\s{2,}[\w.-]+:\s*)[A-Za-z0-9+/=]{8,}\s*$", r"\1<redacted>", out)
        out = re.sub(r"(\"[\w.-]+\":\s*\")[A-Za-z0-9+/=]{12,}(\")", r"\1<redacted>\2", out)
    return out


def clip(text: str, limit: int = _MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = limit // 3
    return text[:head] + f"\n... [{len(text) - limit} characters cut] ...\n" + text[-(limit - head):]


@dataclass
class Prepared:
    command: str
    argv: list[str]
    env: dict[str, str]
    cwd: str
    classification: Classification
    decision: Decision
    op_id: str = ""
    error: str = ""


@dataclass
class Report:
    command: str
    status: str  # done | failed | verify_failed | refused | needs_approval | error
    decision: dict[str, Any]
    exit_code: int | None = None
    output: str = ""
    duration_ms: int = 0
    verified: bool | None = None
    checks: list[dict[str, Any]] = field(default_factory=list)
    diagnosis: list[dict[str, str]] = field(default_factory=list)
    recovery: list[str] = field(default_factory=list)
    snapshot: str = ""
    op_id: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {name: value for name, value in self.__dict__.items() if value not in ("", None, []) or name in {"status", "verified"}}


class Broker:
    def __init__(self, workspace: str | Path, *, runner: Runner = default_runner, registry: Registry | None = None,
                 policy: InfraPolicy | None = None) -> None:
        self.workspace = Path(workspace).expanduser()
        self.runner = runner
        self.registry = registry or Registry(self.workspace)
        self.policy = policy
        self.dir = infra_dir(self.workspace)

    def _policy(self) -> InfraPolicy:
        if self.policy is not None:
            return self.policy
        from navin.infra.policy import load_policy

        return load_policy(self.workspace)

    # -- prepare ------------------------------------------------------------------

    def prepare(self, command: str, *, cwd: str = "") -> Prepared:
        command = (command or "").strip()
        workdir = str((self.workspace / cwd).resolve()) if cwd and not Path(cwd).is_absolute() else (cwd or str(self.workspace))
        policy = self._policy()
        contexts = current_contexts(workdir)
        empty = Classification(command=command, binary="", provider="", risk="change")
        if not command:
            return Prepared(command, [], {}, workdir, empty, decide(empty, policy, contexts), error="empty command")
        if _SHELL_OPERATORS.search(_unquoted(command)) or len(split_commands(command)) > 1:
            return Prepared(command, [], {}, workdir, empty, decide(empty, policy, contexts),
                            error="the broker runs one program without a shell: no pipes, &&, ;, redirections or $(...). Split it into steps (infra action=plan), or post-process the output yourself.")
        classified = worst(classify_command(command))
        if classified is None:
            return Prepared(command, [], {}, workdir, empty, decide(empty, policy, contexts),
                            error="not an infrastructure command the broker knows; use exec for general shell work (it applies the same policy to infrastructure tools)")
        decision = decide(classified, policy, contexts)
        parsed = classified.parsed
        argv = list(parsed.argv) if parsed else tokenize(command)
        env = dict(parsed.env) if parsed else {}
        if classified.provider == "aws" and policy.auth.get("aws_profile") and "--profile" not in argv and "AWS_PROFILE" not in env:
            env["AWS_PROFILE"] = policy.auth["aws_profile"]
        if classified.binary in {"gcloud", "gsutil", "bq"} and policy.auth.get("gcp_impersonate") and "--impersonate-service-account" not in " ".join(argv):
            env["CLOUDSDK_AUTH_IMPERSONATE_SERVICE_ACCOUNT"] = policy.auth["gcp_impersonate"]
        match = self.registry.match_command(command)
        return Prepared(command, argv, env, workdir, classified, decision, op_id=match.id if match else "")

    # -- execute -----------------------------------------------------------------------

    def execute(self, prepared: Prepared, *, approved: bool = False, timeout: float = 300, verify: bool = True,
                run_id: str = "", step_id: str = "") -> Report:
        decision = prepared.decision.to_dict()
        if prepared.error:
            return self._audit(Report(prepared.command, "error", decision, error=prepared.error), prepared, run_id, step_id)
        if prepared.decision.action == "deny":
            return self._audit(Report(prepared.command, "refused", decision, error="; ".join(prepared.decision.reasons)), prepared, run_id, step_id)
        if prepared.decision.action == "ask" and not approved:
            return Report(prepared.command, "needs_approval", decision, op_id=prepared.op_id)
        snapshot_id, snapshot = "", None
        if prepared.classification.mutation:
            snapshot_id, snapshot = self._snapshot(prepared)
        result = self.runner(prepared.argv, timeout=timeout, env=prepared.env, cwd=prepared.cwd)
        output = clip(redact(result.output))
        report = Report(prepared.command, "done" if result.ok else "failed", decision, exit_code=result.exit_code,
                        output=output, duration_ms=result.duration_ms, snapshot=snapshot_id, op_id=prepared.op_id)
        rollback_hints = []
        if prepared.op_id:
            op = self.registry.find(prepared.op_id)
            rollback_hints = list(op.rollback) if op else []
        if result.ok and verify and prepared.classification.mutation:
            checks = verification_plan(prepared.classification, prepared.cwd)
            report.checks, report.verified = self._run_checks(checks, prepared)
            if report.verified is False:
                report.status = "verify_failed"
        if not result.ok:
            report.diagnosis = diagnose(result.output)
        if report.status != "done":
            report.recovery = recovery_plan(prepared.classification, snapshot, rollback_hints)
        if report.status == "done" and prepared.op_id:
            self.registry.mark_tested(prepared.op_id, prepared.command, report.verified)
        return self._audit(report, prepared, run_id, step_id, approved=approved)

    def verify(self, command: str, *, cwd: str = "") -> dict[str, Any]:
        prepared = self.prepare(command, cwd=cwd)
        if prepared.error:
            return {"error": prepared.error}
        checks = verification_plan(prepared.classification, prepared.cwd)
        if not checks:
            return {"verified": None, "checks": [], "note": "no automatic check is known for this command: verify by reading the resource (infra action=describe lists the verification method)"}
        results, verified = self._run_checks(checks, prepared)
        return {"verified": verified, "checks": results}

    def _run_checks(self, checks: list[Check], prepared: Prepared) -> tuple[list[dict[str, Any]], bool | None]:
        if not checks:
            return [], None
        results, passed = [], True
        for check in checks:
            outcome = self.runner(check.argv, timeout=330, env=prepared.env, cwd=prepared.cwd)
            ok = check_passed(check, outcome.exit_code, outcome.output)
            passed = passed and ok
            results.append({**check.to_dict(), "passed": ok, "exit_code": outcome.exit_code, "output": clip(redact(outcome.output), 1500)})
        return results, passed

    # -- snapshots -------------------------------------------------------------------------

    def _snapshot(self, prepared: Prepared) -> tuple[str, dict[str, Any] | None]:
        spec = snapshot_plan(prepared.classification)
        if spec is None:
            return "", None
        captured = self.runner(spec.argv, timeout=120, env=prepared.env, cwd=prepared.cwd)
        if not captured.ok or not captured.stdout.strip():
            return "", None
        snapshot_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        directory = self.dir / "snapshots"
        directory.mkdir(parents=True, exist_ok=True)
        suffix = {"yaml": ".yaml", "tfstate": ".tfstate", "json": ".json", "helm": ".json"}.get(spec.kind, ".txt")
        body = clean_kube_yaml(captured.stdout) if spec.kind == "yaml" else captured.stdout
        data_path = directory / f"{snapshot_id}{suffix}"
        data_path.write_text(body, encoding="utf-8")
        restore = [part.replace("{snapshot}", str(data_path)) for part in spec.restore]
        if spec.kind == "helm":
            try:
                history = json.loads(captured.stdout)
                deployed = [entry for entry in history if entry.get("status") in {"deployed", "superseded"}]
                revision = str((deployed or history)[-1].get("revision", "")) if history else ""
            except (ValueError, AttributeError, IndexError) as exc:
                # No revision to roll back to: the snapshot says so instead of offering a broken command.
                revision = ""
                spec.note = f"{spec.note} helm history was unreadable ({exc}): no automatic rollback.".strip()
            restore = [part.replace("{revision}", revision) for part in restore] if revision else []
        meta = {"id": snapshot_id, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "command": redact(prepared.command),
                "capture": " ".join(spec.argv), "kind": spec.kind, "path": str(data_path), "restore": restore, "note": spec.note}
        (directory / f"{snapshot_id}.meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return snapshot_id, meta

    def snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        path = self.dir / "snapshots" / f"{snapshot_id}.meta.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            logger.warning("infra: snapshot {} is unreadable: {}", path, exc)
            return None

    # -- audit -----------------------------------------------------------------------------------

    def _audit(self, report: Report, prepared: Prepared, run_id: str, step_id: str, *, approved: bool = False) -> Report:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "command": redact(prepared.command)[:500],
            "provider": prepared.classification.provider, "risk": prepared.decision.risk,
            "categories": prepared.decision.categories, "decision": prepared.decision.action, "approved": approved,
            "status": report.status, "exit_code": report.exit_code, "ok": report.status == "done",
            "duration_ms": report.duration_ms, "verified": report.verified, "snapshot": report.snapshot,
            "op_id": report.op_id, "run_id": run_id, "step_id": step_id,
            "error_kind": report.diagnosis[0]["cause"] if report.diagnosis else "",
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        with (self.dir / "audit.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
        return report

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            lines = (self.dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []  # nothing ran through the broker yet
        out = []
        damaged = 0
        for line in reversed(lines[-max(1, limit) * 2:]):
            try:
                out.append(json.loads(line))
            except ValueError:
                damaged += 1
                continue
            if len(out) >= limit:
                break
        if damaged:
            logger.warning("infra: {} damaged line(s) in {} were skipped", damaged, self.dir / "audit.jsonl")
        return out


def _unquoted(command: str) -> str:
    """The line with its quoted parts blanked, so ``--query 'a|b'`` is not a pipe."""
    return re.sub(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"", "''", command)
