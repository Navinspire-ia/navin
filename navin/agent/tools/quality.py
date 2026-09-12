# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Quality tools: lint, run tests, and verify a change end to end.

These exist so the agent can *verify* instead of assume. Each returns parsed,
structured results - diagnostics with positions, test failures with names and
messages - rather than raw tool output the model has to interpret.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path
from typing import Any

from navin.agent.tool_output import emit_tool_meta, emit_tool_output
from navin.agent.tools.base import Tool, ToolResult
from navin.agent.tools.filesystem import _FsTool
from navin.utils.path import normalize_relative_path
from navin.utils.proc import no_window_kwargs

_MAX_SHOWN = 40
_GIT_TIMEOUT_S = 20


def _test_output_callback():
    """Forward synchronous runner progress to the active async tool call."""
    from navin.utils.task_progress import parse_progress_from_output

    loop = asyncio.get_running_loop()

    async def publish(output: str) -> None:
        parsed = parse_progress_from_output(output)
        await emit_tool_output(output, **parsed)

    def callback(output: str) -> None:
        asyncio.run_coroutine_threadsafe(publish(output), loop).result()

    return callback


def _tracked_modifications(root: Path, paths: list[str] | None) -> list[str]:
    """Tracked files whose content differs from HEAD, staged or not.

    ``changed_files`` cannot answer this question: it includes untracked files,
    which a git rollback leaves alone, and asking the user about work that is
    not actually at risk would teach them to wave the question through.
    """
    git = shutil.which("git")
    if git is None:
        return []
    argv = [git, "-C", str(root), "diff", "--name-only", "HEAD", "--"]
    if paths:
        argv.extend(paths)
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GIT_TIMEOUT_S,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        # Includes a repository with no commit yet: HEAD does not resolve, and
        # git itself will refuse the restore with its own explanation.
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


class _QualityTool(_FsTool):
    """Shared project-root resolution for the quality tools."""

    _scopes = {"core", "subagent"}

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        tool = super().create(ctx)
        # Used to auto-open HTML reports in the WebUI File Preview panel.
        tool._bus = getattr(ctx, "bus", None)
        return tool

    def _open_file_preview(self, path: Path | str) -> bool:
        """Best-effort open in WebUI File Preview when on a websocket turn."""
        target = Path(path).expanduser().resolve(strict=False)
        if not target.is_file():
            return False
        bus = getattr(self, "_bus", None)
        if bus is None:
            return False
        try:
            from navin.agent.tools.context import current_request_context
            from navin.bus.outbound_events import (
                FilePreviewOpenRequestedEvent,
                outbound_message_for_event,
            )

            ctx = current_request_context()
            if ctx is None or ctx.channel != "websocket":
                return False
            bus.outbound.put_nowait(
                outbound_message_for_event(
                    channel="websocket",
                    chat_id=ctx.chat_id,
                    event=FilePreviewOpenRequestedEvent(path=str(target)),
                )
            )
            return True
        except Exception:
            return False

    def _project_root(self) -> Path:
        root = self._display_workspace() or self._workspace
        if root is None:
            raise ValueError("no workspace configured")
        return Path(root).expanduser().resolve(strict=False)

    def _root_or_error(self) -> tuple[Path | None, str]:
        try:
            root = self._project_root()
        except Exception as exc:
            return None, ToolResult.error(f"Error: {exc}")
        if not root.is_dir():
            return None, ToolResult.error(f"Error: project root not found: {root}")
        return root, ""

    def _project_relative(self, path: str | None, root: Path) -> tuple[str, str]:
        """Turn a model-supplied path into a project-relative key, or explain why not.

        read_file and edit_file accept the absolute spelling of a workspace
        file, so a model that just read /home/user/proj/src/app.py hands the
        same string here. ``normalize_relative_path`` alone strips the leading
        slash and the lookup then reports "File not found" for a file that
        plainly exists; an absolute path inside the root is therefore
        re-anchored to it first.

        A leading slash can also be a model writing the project-relative path
        with a root anchor ("/src/app.py"). That reading gets a chance before
        an absolute path outside the workspace is refused.

        Returns ``(relative, error)``; a missing path is ``("", "")`` so each
        caller keeps its own "requires 'path'" wording.
        """
        raw = (path or "").strip()
        if raw:
            candidate = Path(raw.replace("\\", "/")).expanduser()
            if candidate.is_absolute():
                try:
                    rel = candidate.resolve(strict=False).relative_to(root)
                except ValueError:
                    fallback = normalize_relative_path(raw)
                    if fallback and (root / fallback).exists():
                        return fallback, ""
                    return "", ToolResult.error(
                        f"Error: {raw} is outside the workspace root ({root}). "
                        "This tool only works on files inside the project."
                    )
                return normalize_relative_path(str(rel)), ""
        return normalize_relative_path(raw), ""


class LintTool(_QualityTool):
    """Run the project's linters and return normalized diagnostics."""

    @property
    def name(self) -> str:
        return "lint"

    @property
    def description(self) -> str:
        return (
            "Run the project's real linters and get diagnostics with exact "
            "positions, rule codes and severities. Handles whichever tools the "
            "project has (ruff, ESLint, tsc, go vet, cargo check, shellcheck, "
            "yamllint, syntax checks). Use 'file' after editing a file, "
            "'changed' to lint everything modified since the last commit, "
            "'project' for whole-repo type checking, 'fix' to apply safe "
            "auto-fixes in place, and 'available' to see which linters this "
            "project can actually run. Prefer this over running linters through "
            "exec: the output is parsed instead of raw text."
        )

    @property
    def read_only(self) -> bool:
        return False  # action=fix rewrites files

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["file", "changed", "project", "fix", "available"],
                    "description": (
                        "file: lint one path; changed: lint modified files; "
                        "project: whole-repo linters/type checkers; "
                        "fix: apply safe auto-fixes; "
                        "available: report installed linters"
                    ),
                },
                "path": {
                    "type": "string",
                    "description": "Project-relative path (for action=file/fix)",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, path: str | None = None, **kwargs: Any) -> str:
        root, error = self._root_or_error()
        if root is None:
            return error

        from navin.quality.linters import (
            available_linters,
            fix_file,
            lint_file,
            lint_project,
        )
        from navin.quality.verify import changed_files

        if action == "available":
            rows = available_linters(root)
            lines = ["Linters for this project:"]
            for row in rows:
                mark = "yes" if row["available"] else "no "
                detail = f" - {row['reason']}" if row["reason"] else ""
                lines.append(
                    f"  [{mark}] {row['linter']} ({row['language']}, "
                    f"{row['scope']}-scope){detail}"
                )
            return "\n".join(lines)

        # Every linter run below is a subprocess behind a synchronous call, and
        # so is the git query for changed files. On the event loop they would
        # freeze the gateway and every other session for the whole run.
        if action in {"file", "fix"}:
            cleaned, path_error = self._project_relative(path, root)
            if path_error:
                return path_error
            if not cleaned:
                return ToolResult.error(f"Error: action={action} requires 'path'")
            if not (root / cleaned).is_file():
                return self._missing_project_file(cleaned, root)
            if action == "fix":
                results = await asyncio.to_thread(fix_file, root, cleaned)
                applied = [r.linter for r in results if r.ran]
                if not applied:
                    return (
                        f"No auto-fixer available for {cleaned}. "
                        "Fix the diagnostics manually."
                    )
                after = await asyncio.to_thread(lint_file, root, cleaned)
                from navin.quality.evidence import lint_evidence

                return ToolResult("\n".join([
                    f"Auto-fix applied to {cleaned} via {', '.join(applied)}.",
                    "",
                    self._render(after, root),
                ]), verification=lint_evidence(after))
            return self._render(await asyncio.to_thread(lint_file, root, cleaned), root)

        if action == "changed":
            changed = await asyncio.to_thread(changed_files, root)
            if not changed:
                return "No modified files detected (working tree is clean)."

            def _lint_each() -> list[Any]:
                results: list[Any] = []
                for rel in changed:
                    results.extend(lint_file(root, rel))
                return results

            results = await asyncio.to_thread(_lint_each)
            header = f"Linted {len(changed)} modified file(s):\n"
            rendered = self._render(results, root)
            return ToolResult(header + rendered, verification=rendered.verification)

        if action == "project":
            results = await asyncio.to_thread(lint_project, root)
            if not results:
                return (
                    "No project-scope linter applies here (no tsconfig.json, "
                    "go.mod or Cargo.toml found)."
                )
            return self._render(results, root)

        return self.unknown_action(action)

    @staticmethod
    def _render(results: list[Any], root: Path) -> ToolResult:
        from navin.quality.evidence import lint_evidence

        diagnostics = [d for r in results for d in r.diagnostics]
        errors = [d for d in diagnostics if d.severity == "error"]
        warnings = [d for d in diagnostics if d.severity == "warning"]
        ran = sorted({r.linter for r in results if r.ran})
        skipped = [r for r in results if not r.ran and r.skipped_reason]

        lines: list[str] = []
        if not diagnostics:
            lines.append(
                f"Clean - no diagnostics ({', '.join(ran)})."
                if ran
                else "No linter ran."
            )
        else:
            lines.append(
                f"{len(errors)} error(s), {len(warnings)} warning(s) "
                f"from {', '.join(ran)}:"
            )
            for diagnostic in [*errors, *warnings][:_MAX_SHOWN]:
                lines.append(f"  {diagnostic.render()}")
            if len(diagnostics) > _MAX_SHOWN:
                lines.append(f"  … {len(diagnostics) - _MAX_SHOWN} more")
        if skipped:
            lines.append("")
            lines.append("Not run:")
            for result in skipped:
                lines.append(f"  {result.linter}: {result.skipped_reason}")
        return ToolResult("\n".join(lines), verification=lint_evidence(results))


class TestRunTool(_QualityTool):
    """Run the project's test suites with parsed results."""

    @property
    def name(self) -> str:
        return "test_run"

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return super().cast_params({"action": "run", **params})

    @property
    def description(self) -> str:
        return (
            "Run the project's tests and get structured results: pass/fail/skip "
            "counts plus each failure's test name, file and assertion message. "
            "Auto-detects the runner (pytest, vitest, jest, go test, cargo "
            "test). The default action is 'run', optionally with 'target' to "
            "run a single file or test, and 'detect' to see which suites exist. "
            "Prefer this over running tests through exec: failures come back "
            "parsed instead of buried in truncated logs."
        )

    @property
    def read_only(self) -> bool:
        return False  # tests execute project code

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "detect"],
                    "description": "run: execute tests (default); detect: list test suites",
                    "default": "run",
                },
                "target": {
                    "type": "string",
                    "description": (
                        "Optional single test file, directory, or test id to run "
                        "instead of the whole suite (much faster while iterating)"
                    ),
                },
                "runner": {
                    "type": "string",
                    "description": (
                        "Optional runner name to force (pytest, vitest, jest, "
                        "go_test, cargo_test)"
                    ),
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str = "run",
        target: str | None = None,
        runner: str | None = None,
        **kwargs: Any,
    ) -> str:
        root, error = self._root_or_error()
        if root is None:
            return error

        from navin.quality.testing import detect_suites, run_tests

        if action == "detect":
            await emit_tool_output("Detecting test suites…")
            rows = detect_suites(root)
            lines = ["Test suites in this project:"]
            for row in rows:
                mark = "yes" if row["available"] else "no "
                detail = f" - {row['reason']}" if row["reason"] else ""
                lines.append(f"  [{mark}] {row['runner']} ({row['language']}){detail}")
            if not any(row["available"] for row in rows):
                lines.append("")
                lines.append("No runnable test suite detected.")
            return "\n".join(lines)

        if action == "run":
            scope = target or runner or "the project suite"
            await emit_tool_output(f"Running tests on {scope}…")
            # A full suite legitimately takes minutes; run it off the event
            # loop so the gateway and its other sessions stay responsive.
            outcomes = await asyncio.to_thread(
                run_tests,
                root,
                runners=[runner] if runner else None,
                target=(target or None),
                on_output=_test_output_callback(),
            )
            if outcomes and all(outcome.ran for outcome in outcomes):
                await emit_tool_meta(percent=100, label="Tests finished", indeterminate=False)
            _record_test_outcomes(root, outcomes, target=target)
            from navin.quality.evidence import test_evidence

            return ToolResult(
                "\n\n".join(outcome.render() for outcome in outcomes),
                verification=test_evidence(outcomes),
            )

        return self.unknown_action(action)


class VerifyTool(_QualityTool):
    """Chain lint and tests into a single verdict, with rollback."""

    @property
    def name(self) -> str:
        return "verify"

    @property
    def description(self) -> str:
        return (
            "Verify a change end to end: lints exactly the files you modified, "
            "runs the test suite, and returns a single verdict (PASS/FAIL) with "
            "the concrete next step. Use 'check' after finishing an edit - this "
            "is the standard way to confirm work is correct before reporting it "
            "done. Use 'fix' to auto-fix safe lint problems and re-verify. Use "
            "'snapshot' before a risky refactor, and 'rollback' to discard a "
            "change that cannot be salvaged (restores tracked files from git, "
            "or from a snapshot id). 'snapshots' lists available snapshots."
        )

    @property
    def read_only(self) -> bool:
        return False

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["check", "fix", "snapshot", "rollback", "snapshots"],
                    "description": (
                        "check: lint changed files + run tests; "
                        "fix: auto-fix lint then re-verify; "
                        "snapshot: save current file contents; "
                        "rollback: restore files; "
                        "snapshots: list saved snapshots"
                    ),
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional explicit file list. Defaults to everything "
                        "modified according to git."
                    ),
                },
                "with_tests": {
                    "type": "boolean",
                    "description": "Run tests as part of the check (default true)",
                },
                "project_lint": {
                    "type": "boolean",
                    "description": (
                        "Also run whole-project type checking (tsc, go vet). "
                        "Slower but catches cross-file breakage."
                    ),
                },
                "test_target": {
                    "type": "string",
                    "description": "Restrict the test run to this file or test id",
                },
                "snapshot_id": {
                    "type": "string",
                    "description": "Snapshot to restore (for action=rollback)",
                },
            },
            "required": ["action"],
        }

    async def execute(
        self,
        action: str,
        paths: list[str] | None = None,
        with_tests: bool = True,
        project_lint: bool = False,
        test_target: str | None = None,
        snapshot_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        root, error = self._root_or_error()
        if root is None:
            return error

        from navin.quality.verify import (
            create_snapshot,
            list_snapshots,
            restore_from_git,
            restore_snapshot,
            verify_changes,
        )

        cleaned_paths = [
            normalized for p in (paths or []) if (normalized := normalize_relative_path(p))
        ]

        if action == "snapshot":
            # Reads every changed file and shells out to git for the file list.
            result = await asyncio.to_thread(create_snapshot, root, cleaned_paths or None)
            if result.get("error"):
                return ToolResult.error(f"Error creating snapshot: {result['error']}")
            if not result.get("files"):
                return "Nothing to snapshot - no modified files detected."
            return (
                f"Snapshot {result['id']} saved ({result['files']} file(s)). "
                f"Restore with: verify action=rollback snapshot_id={result['id']}"
            )

        if action == "snapshots":
            rows = list_snapshots(root)
            if not rows:
                return "No snapshots saved for this project."
            lines = ["Saved snapshots (most recent first):"]
            for row in rows[:20]:
                lines.append(f"  {row['id']} - {row['files']} file(s)")
                for path in row["paths"][:4]:
                    lines.append(f"      {path}")
            return "\n".join(lines)

        if action == "rollback":
            if snapshot_id:
                result = await asyncio.to_thread(restore_snapshot, root, snapshot_id.strip())
                source = f"snapshot {snapshot_id.strip()}"
            else:
                # Without a snapshot this restores from HEAD, which is the same
                # unrecoverable discard the git tool asks the user about before
                # a reset --hard. The same question is owed here: a snapshot
                # rollback has a saved baseline, this one throws work away.
                at_risk = await asyncio.to_thread(
                    _tracked_modifications, root, cleaned_paths or None
                )
                if at_risk:
                    from navin.agent.approval import ApprovalRequest, request_approval

                    shown = ", ".join(at_risk[:8]) + (" ..." if len(at_risk) > 8 else "")
                    decision = await request_approval(ApprovalRequest(
                        tool="verify",
                        action="Throw away uncommitted changes by restoring from git HEAD",
                        reason=(
                            f"{len(at_risk)} tracked file(s) have changes that "
                            "are in neither a commit nor a snapshot."
                        ),
                        detail=f"Restore from git HEAD: {shown}",
                        consequence="The working tree is overwritten; those changes are gone.",
                        # This rollback worked before it could ask, and a CLI or
                        # scheduled run still has nobody to ask: refusing there
                        # would take a working operation away from unattended runs.
                        allow_when_unattended=True,
                    ))
                    if not decision.allowed:
                        return ToolResult.error(
                            "Error: refused to restore "
                            f"{len(at_risk)} tracked file(s) from git HEAD. "
                            f"{decision.reason}"
                        )
                result = await asyncio.to_thread(restore_from_git, root, cleaned_paths or None)
                source = "git HEAD"
            if result.get("error"):
                return ToolResult.error(f"Error restoring from {source}: {result['error']}")
            restored = result.get("restored", 0)
            if not restored:
                return result.get("note") or f"Nothing restored from {source}."
            lines = [f"Restored {restored} file(s) from {source}:"]
            lines.extend(f"  {path}" for path in result.get("paths", [])[:_MAX_SHOWN])
            if result.get("skipped"):
                lines.append("")
                lines.append(
                    "Left untouched (untracked, so git has no baseline): "
                    + ", ".join(result["skipped"][:10])
                )
            return "\n".join(lines)

        if action in {"check", "fix"}:
            # Lints plus a test run: the slowest thing a tool can do here, so
            # it must not hold the event loop while it does it.
            report = await asyncio.to_thread(
                verify_changes,
                root,
                cleaned_paths or None,
                with_tests=with_tests,
                with_project_lint=project_lint,
                test_target=(test_target or None),
                auto_fix=(action == "fix"),
                on_test_output=_test_output_callback(),
            )
            _record_verify_report(root, report)
            if report.test_outcomes and all(outcome.ran for outcome in report.test_outcomes):
                await emit_tool_meta(percent=100, label="Tests finished", indeterminate=False)
            from navin.quality.evidence import verify_evidence

            return ToolResult(report.render(), verification=verify_evidence(report))

        return self.unknown_action(action)


def _record_verify_report(root: Path, report: Any) -> None:
    """Log this verify run so the board can demand real proof before 'done'."""
    from navin.quality.evidence import verify_evidence
    from navin.quality.verification_log import record_verification
    from navin.quality.verify import VERDICT_NO_CHANGES

    if report.verdict == VERDICT_NO_CHANGES:
        return  # nothing was checked, so nothing is proven either way
    outcomes = list(report.test_outcomes or [])
    ran = [o for o in outcomes if o.ran]
    passed = sum(o.passed for o in ran)
    failed = sum(o.failed for o in ran)
    summary = f"verdict={report.verdict}"
    evidence = verify_evidence(report)
    if ran:
        summary += f"; tests: {passed} passed, {failed} failed"
    record_verification(
        root,
        source="verify",
        ok=evidence.ok,
        tests_ran=passed + failed > 0,
        summary=summary,
    )


def _record_test_outcomes(
    root: Path,
    outcomes: list[Any],
    *,
    target: str | None = None,
) -> None:
    """Log attempted runs too, so no-tests cannot leave an earlier green proof."""
    from navin.quality.evidence import test_evidence
    from navin.quality.verification_log import record_verification

    ran = [o for o in outcomes if o.ran]
    passed = sum(o.passed for o in ran)
    failed = sum(o.failed for o in ran)
    evidence = test_evidence(outcomes)
    summary = f"tests: {passed} passed, {failed} failed" if evidence.tests_ok is not None else "No tests ran."
    if target:
        summary += f" (target: {target})"
    record_verification(
        root,
        source="test_run",
        ok=evidence.ok,
        tests_ran=passed + failed > 0,
        summary=summary,
    )
