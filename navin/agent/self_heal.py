"""Self-healing loop: CI failure -> local reproduction -> repair plan -> draft PR.

Every stage is an injectable callable so the loop runs in tests with no
network and no subprocess. Defaults reuse the existing ``forge`` plumbing
(``forge_api.ci_failed_logs`` / ``create_request``, the same calls behind the
``forge`` tool actions ``ci_logs`` and ``pr_create``).

Entry point: ``python -m navin.agent.self_heal [--branch B] [--open-pr]``.
A draft PR is only opened when the failure was reproduced AND the command
passes again after the fix step.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from navin.utils.proc import no_window_kwargs

FetchLogs = Callable[[str], dict[str, Any]]
RunCommand = Callable[[str], "CommandResult"]
ApplyFix = Callable[["HealPlan"], bool]
OpenPr = Callable[[dict[str, Any]], dict[str, Any]]

logger = logging.getLogger(__name__)


@dataclass
class CommandResult:
    returncode: int
    output: str = ""


@dataclass
class HealPlan:
    kind: str
    command: str
    skills: list[str]
    steps: list[str]
    output: str = ""


@dataclass
class HealReport:
    status: str = "no_failure"
    branch: str = ""
    job: str = ""
    command: str = ""
    reproduced: bool = False
    verified: bool = False
    plan: HealPlan | None = None
    pr: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


# kind -> (regex on command, skills from the existing catalog, steps)
_PLAYBOOKS: list[tuple[str, re.Pattern[str], list[str], list[str]]] = [
    (
        "lint",
        re.compile(r"\b(ruff|eslint|flake8|pylint|mypy|tsc|clippy|shellcheck)\b"),
        ["code-reviewer", "quality-gate"],
        ["Run the linter on the failing files", "Apply safe auto-fixes", "Fix the rest by hand"],
    ),
    (
        "test",
        re.compile(r"\b(pytest|vitest|jest|go test|cargo test|npm test|unittest)\b"),
        ["self-healing-retry", "test-generator", "debug-live"],
        [
            "Rerun the single failing test",
            "Locate the root cause, not the assertion",
            "Fix the code (adjust the test only if it is wrong)",
            "Rerun the failing test then the suite",
        ],
    ),
    (
        "build",
        re.compile(r"\b(build|compile|cargo build|make|webpack|vite)\b"),
        ["self-healing-retry", "cicd-agent"],
        ["Rerun the build", "Read the first error", "Fix and rebuild"],
    ),
]
_DEFAULT_PLAYBOOK = (
    "unknown",
    ["sre-incident-responder", "self-healing-retry"],
    ["Rerun the command", "Isolate the first error", "Fix and rerun"],
)

_RUN_LINE = re.compile(r"^(?:\S+\s+)?##\[group\]Run\s+(.+)$|^\s*(?:Run|\$)\s+(\S.*)$", re.M)
_PYTEST_FAIL = re.compile(r"^FAILED\s+(\S+?)(?:::|\s|$)", re.M)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def extract_failing_command(log: str) -> str:
    """Best effort: the last ``Run <cmd>`` / ``$ <cmd>`` before the failure."""
    text = _ANSI.sub("", log or "")
    last = ""
    for match in _RUN_LINE.finditer(text):
        cmd = (match.group(1) or match.group(2) or "").strip()
        if cmd:
            last = cmd
    failed = _PYTEST_FAIL.findall(text)
    if failed and (not last or "pytest" in last or last.startswith("python")):
        return "python -m pytest " + " ".join(dict.fromkeys(failed))
    return last


def build_plan(command: str) -> HealPlan:
    for kind, pattern, skills, steps in _PLAYBOOKS:
        if pattern.search(command):
            return HealPlan(kind, command, list(skills), list(steps))
    kind, skills, steps = _DEFAULT_PLAYBOOK
    return HealPlan(kind, command, list(skills), list(steps))


def _local_run(command: str, cwd: Path | None = None) -> CommandResult:
    proc = subprocess.run(  # noqa: S602 - command comes from the project's own CI log
        command, shell=True, capture_output=True, text=True, timeout=600, cwd=cwd,
        **no_window_kwargs(),
    )
    return CommandResult(proc.returncode, (proc.stdout + proc.stderr)[-6000:])


def build_fix_prompt(plan: HealPlan) -> str:
    steps = "\n".join(f"- {s}" for s in plan.steps)
    return (
        f"CI failure to repair ({plan.kind}).\n"
        f"Failing command: {plan.command}\n"
        f"Suggested skills: {', '.join(plan.skills)}\n\n"
        f"Repair plan:\n{steps}\n\n"
        f"Output of the command reproduced locally:\n{plan.output}\n\n"
        "Fix the code in this repository so the command passes. "
        "Do not commit or push. Reply with a short summary."
    )


def agent_fixer(root: Path, run_agent: Callable[[str, Path], Any] | None = None) -> ApplyFix:
    """Default fixer: one Navin agent turn in ``root`` with the repair prompt.

    ``run_agent(prompt, root)`` returns an object with ``content`` / ``error``
    (the SDK ``RunResult``). An agent error raises, it is never swallowed.
    """

    def run_sdk(prompt: str, workdir: Path) -> Any:
        import asyncio

        from navin import Navin

        bot = Navin.from_config(workspace=workdir)
        return asyncio.run(bot.run(prompt, session_key="self_heal:fix", ephemeral=True))

    runner = run_agent or run_sdk

    def fix(plan: HealPlan) -> bool:
        result = runner(build_fix_prompt(plan), root)
        error = getattr(result, "error", None)
        if error:
            raise RuntimeError(f"agent fixer error: {error}")
        return True

    return fix


def _forge_fetch(root: Path) -> FetchLogs:
    def fetch(branch: str) -> dict[str, Any]:
        from navin.agent.tools.forge import _current_branch, resolve_forge
        from navin.webui import forge_api

        remote, token = resolve_forge(root)
        ref = branch or _current_branch(root)
        return forge_api.ci_failed_logs(remote, token, ref=ref)

    return fetch


def _forge_open_pr(root: Path) -> OpenPr:
    def open_pr(spec: dict[str, Any]) -> dict[str, Any]:
        from navin.agent.tools.forge import resolve_forge
        from navin.webui import forge_api

        remote, token = resolve_forge(root)
        base = forge_api.default_branch(remote, token)
        return forge_api.create_request(
            remote,
            token,
            head=spec["head"],
            base=base,
            title=spec["title"],
            body=spec["body"],
            draft=True,
        )

    return open_pr


def pr_spec(report: HealReport) -> dict[str, Any]:
    plan = report.plan
    steps = "\n".join(f"- {s}" for s in (plan.steps if plan else []))
    body = (
        f"Automated self-heal for failing CI job `{report.job or 'unknown'}`.\n\n"
        f"Failing command: `{report.command}`\n"
        f"Reproduced locally: {report.reproduced}; passes after fix: {report.verified}\n\n"
        f"Plan:\n{steps}\n"
    )
    return {
        "head": report.branch,
        "title": f"fix(ci): repair {report.job or report.command}"[:120],
        "body": body,
        "draft": True,
    }


def heal(
    branch: str = "",
    *,
    fetch_logs: FetchLogs,
    run_command: RunCommand = _local_run,
    apply_fix: ApplyFix | None = None,
    open_pr: OpenPr | None = None,
) -> HealReport:
    """Run the loop. Stops at the first stage that cannot proceed."""
    report = HealReport(branch=branch)
    data = fetch_logs(branch) or {}
    jobs = data.get("jobs") or []
    if not jobs:
        report.notes.append(str(data.get("note") or "no failing job"))
        return report
    job = jobs[0]
    report.job = str(job.get("name") or "")
    command = extract_failing_command(str(job.get("log") or ""))
    if not command:
        report.status = "no_command"
        report.notes.append("could not extract a failing command from the log")
        return report
    report.command = command
    report.plan = build_plan(command)

    first = run_command(command)
    report.reproduced = first.returncode != 0
    if not report.reproduced:
        report.status = "not_reproduced"
        report.notes.append("command passes locally: flaky or environment specific")
        return report

    report.plan.output = first.output
    fixed = False
    if apply_fix is not None:
        try:
            fixed = apply_fix(report.plan)
        except Exception as exc:  # reported in the report, never hidden
            report.status = "fix_error"
            report.notes.append(f"fixer failed: {exc}")
            return report
    if not fixed:
        report.status = "plan_ready"
        report.notes.append("no fix applied: plan only, no PR")
        return report

    report.verified = run_command(command).returncode == 0
    if not report.verified:
        report.status = "fix_failed"
        return report

    spec = pr_spec(report)
    report.pr = spec
    if open_pr is None:
        report.status = "pr_prepared"
        return report
    report.pr = {**spec, "result": open_pr(spec)}
    report.status = "pr_opened"
    return report


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=300, check=False,
        **no_window_kwargs(),
    )


@contextmanager
def isolated_checkout(root: Path, ref: str = "HEAD") -> Iterator[Path]:
    """Disposable ``git worktree`` of ``ref``; always removed on exit.

    The user's working tree is never touched: reproduction and fixes run in
    the worktree only. Cleanup errors are logged, never raised over the body.
    """
    root = Path(root)
    tmp = Path(tempfile.mkdtemp(prefix="navin-heal-"))
    path = tmp / "wt"
    added = _git(root, "worktree", "add", "--detach", str(path), ref)
    if added.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"git worktree add failed: {(added.stderr or added.stdout).strip()}")
    try:
        yield path
    finally:
        removed = _git(root, "worktree", "remove", "--force", str(path))
        if removed.returncode != 0:
            logger.warning("self_heal: git worktree remove failed: %s", removed.stderr.strip())
        pruned = _git(root, "worktree", "prune")
        if pruned.returncode != 0:
            logger.warning("self_heal: git worktree prune failed: %s", pruned.stderr.strip())
        try:
            shutil.rmtree(tmp)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("self_heal: could not delete %s: %s", tmp, exc)


def _worktree_open_pr(root: Path, worktree: Path, opener: OpenPr | None = None) -> OpenPr:
    """Commit the fix in the worktree on ``navin/heal-<sha>``, push it, open the PR."""
    inner = opener or _forge_open_pr(root)

    def open_pr(spec: dict[str, Any]) -> dict[str, Any]:
        sha = _git(worktree, "rev-parse", "--short", "HEAD").stdout.strip() or "head"
        head = f"navin/heal-{sha}"
        steps = [
            ("switch", "-c", head),
            ("add", "-A"),
            ("commit", "-m", spec["title"]),
            ("push", "--set-upstream", "origin", head),
        ]
        for step in steps:
            proc = _git(worktree, *step)
            if proc.returncode != 0:
                raise RuntimeError(f"git {step[0]} failed: {(proc.stderr or proc.stdout).strip()}")
        return inner({**spec, "head": head})

    return open_pr


def heal_in_worktree(
    root: Path,
    branch: str = "",
    *,
    fix: bool = False,
    open_pr: bool = False,
    fetch_logs: FetchLogs | None = None,
    run_agent: Callable[[str, Path], Any] | None = None,
    opener: OpenPr | None = None,
) -> HealReport:
    """Run :func:`heal` with reproduction and fix confined to a throwaway worktree."""
    root = Path(root)
    ref = branch if branch and _git(root, "rev-parse", "--verify", "--quiet", branch).returncode == 0 else "HEAD"
    with isolated_checkout(root, ref) as worktree:
        report = heal(
            branch,
            fetch_logs=fetch_logs or _forge_fetch(root),
            run_command=lambda command: _local_run(command, worktree),
            apply_fix=agent_fixer(worktree, run_agent) if fix else None,
            open_pr=_worktree_open_pr(root, worktree, opener) if open_pr else None,
        )
    if branch and ref == "HEAD":
        report.notes.append(f"branch {branch!r} not found locally: reproduced on HEAD")
    report.notes.append("ran in a disposable git worktree (removed)")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="navin.agent.self_heal")
    parser.add_argument("--branch", default="")
    parser.add_argument("--open-pr", action="store_true")
    parser.add_argument("--fix", action="store_true", help="run a Navin agent to apply the fix")
    args = parser.parse_args(argv)
    root = Path.cwd()
    report = heal_in_worktree(root, args.branch, fix=args.fix, open_pr=args.open_pr)
    print(json.dumps(report.to_dict(), indent=2, default=str))
    return 0 if report.status in ("no_failure", "plan_ready", "pr_prepared", "pr_opened") else 1


if __name__ == "__main__":
    raise SystemExit(main())
