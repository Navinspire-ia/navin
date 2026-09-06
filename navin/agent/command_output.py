"""Compact noisy shell / git / test command output before it hits the LLM.

Inspired by RTK-style filters, but native to Navin: no external binary.
Cuts ANSI, progress bars, and verbose success noise while keeping failures
and actionable git summaries. Full unfiltered output can still be spooled
by the caller (exec already does that on char truncation).

Savings apply to *tool output* tokens, not the whole bill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Kind = Literal[
    "generic",
    "git_status",
    "git_diff",
    "git_log",
    "git_mutate",
    "pytest",
    "jest",
    "cargo_test",
    "cargo_build",
    "go_test",
    "generic_test",
    "docker_ps",
    "docker_logs",
    "docker",
    "npm_install",
    "python_lint",
    "pip",
    "js_lint",
    "tsc",
    "next_build",
    "playwright",
    "kubectl",
    "terraform",
    "make_build",
    "curl",
    "gh",
]

# CSI / OSC style escapes commonly dumped by CLIs and test runners.
_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\][^\x07\x1B]*(?:\x07|\x1B\\))")
_PROGRESS_RE = re.compile(
    r"(?i)^\s*(?:"
    r"[\|/-\\]\s+"  # spinner prefix
    r"|(?:\d{1,3}%|\[[=#\s-]{3,}\])\s*"  # percent / bar
    r"|(?:downloading|extracting|resolving|fetching|enumerating|counting|"
    r"compressing|writing objects|delta compression|remote:\s|"
    r"http\s+fetch|reify:|idealtree|timing\s)"
    r").*$"
)
_BLANK_RUN_RE = re.compile(r"\n{3,}")
# Leading shell wrappers the model sometimes adds.
_WRAPPER_RE = re.compile(
    r"(?i)^(?:(?:sudo|env|nice|time|nohup)\s+)+"
)


@dataclass(frozen=True)
class FilterResult:
    """Filtered text plus light telemetry for tests / logs."""

    text: str
    kind: Kind
    original_chars: int
    filtered_chars: int

    @property
    def saved_chars(self) -> int:
        return max(0, self.original_chars - self.filtered_chars)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def classify_command(command: str, *, _depth: int = 0) -> Kind:
    """Best-effort kind from the raw exec command string."""
    raw = (command or "").strip()
    if not raw:
        return "generic"
    # Drop env assignments: FOO=1 BAR=2 pytest ...
    parts = raw.split()
    while parts and "=" in parts[0] and not parts[0].startswith("-"):
        parts = parts[1:]
    line = " ".join(parts)
    line = _WRAPPER_RE.sub("", line).strip()
    lower = line.lower()

    # Unwrap ``sh -c '…'`` / ``bash -lc "…"`` so filters still apply.
    if _depth < 2:
        wrapped = re.match(
            r"""^(?:ba)?sh\s+(?:-[^\s]*c)\s+(['"])([\s\S]+)\1\s*$""",
            line,
        )
        if wrapped:
            inner = classify_command(wrapped.group(2), _depth=_depth + 1)
            if inner != "generic":
                return inner

    if re.search(r"(?:^|[;&|]\s*)git\s+status\b", lower):
        return "git_status"
    if re.search(r"(?:^|[;&|]\s*)git\s+diff\b", lower):
        return "git_diff"
    if re.search(r"(?:^|[;&|]\s*)git\s+log\b", lower):
        return "git_log"
    if re.search(
        r"(?:^|[;&|]\s*)git\s+(?:add|commit|push|pull|fetch|checkout|switch|restore|stash)\b",
        lower,
    ):
        return "git_mutate"

    if re.search(r"(?:^|[;&|]\s*)(?:python\d*(?:\.\d+)?\s+-m\s+)?pytest\b", lower):
        return "pytest"
    if re.search(
        r"(?:^|[;&|]\s*)(?:python\d*(?:\.\d+)?\s+-m\s+unittest\b|unittest\b)",
        lower,
    ):
        return "generic_test"
    if re.search(
        r"(?:^|[;&|]\s*)(?:npx\s+)?(?:jest|vitest)\b"
        r"|(?:^|[;&|]\s*)(?:npm|pnpm|yarn)\s+(?:test|run\s+test)\b",
        lower,
    ):
        return "jest"
    if re.search(
        r"(?:^|[;&|]\s*)(?:npx\s+)?playwright\s+test\b"
        r"|(?:^|[;&|]\s*)(?:npm|pnpm|yarn)\s+run\s+test:e2e\b",
        lower,
    ):
        return "playwright"
    if re.search(r"(?:^|[;&|]\s*)cargo\s+test\b", lower):
        return "cargo_test"
    if re.search(r"(?:^|[;&|]\s*)cargo\s+(?:build|check|clippy)\b", lower):
        return "cargo_build"
    if re.search(r"(?:^|[;&|]\s*)go\s+test\b", lower):
        return "go_test"
    if re.search(
        r"(?:^|[;&|]\s*)(?:make\s+test|tox\b|nosetests\b|rspec\b|coverage\s+run\b)\b",
        lower,
    ):
        return "generic_test"

    if re.search(
        r"(?:^|[;&|]\s*)(?:docker|podman)\s+(?:ps|container\s+ls|container\s+list)\b"
        r"|(?:^|[;&|]\s*)(?:docker|podman)\s+compose\s+ps\b"
        r"|(?:^|[;&|]\s*)docker-compose\s+ps\b",
        lower,
    ):
        return "docker_ps"
    if re.search(
        r"(?:^|[;&|]\s*)(?:docker|podman)\s+logs\b"
        r"|(?:^|[;&|]\s*)(?:docker|podman)\s+compose\s+logs\b",
        lower,
    ):
        return "docker_logs"
    if re.search(
        r"(?:^|[;&|]\s*)(?:docker|podman)\s+"
        r"(?:images|image\s+ls|stats|pull|build|run)\b"
        r"|(?:^|[;&|]\s*)(?:docker|podman)\s+compose\s+"
        r"(?:up|down|build|pull|images|run)\b"
        r"|(?:^|[;&|]\s*)docker-compose\b",
        lower,
    ):
        return "docker"

    if re.search(
        r"(?:^|[;&|]\s*)(?:npm|pnpm|yarn|bun)\s+(?:i|install|ci|add|update)\b",
        lower,
    ):
        return "npm_install"

    if re.search(
        r"(?:^|[;&|]\s*)(?:python\d*(?:\.\d+)?\s+-m\s+)?"
        r"(?:ruff|mypy|pylint|flake8|pyright|black|isort|bandit)\b",
        lower,
    ):
        return "python_lint"

    if re.search(
        r"(?:^|[;&|]\s*)(?:python\d*(?:\.\d+)?\s+-m\s+)?"
        r"(?:pip|pip3|uv\s+pip)\s+",
        lower,
    ) or re.search(r"(?:^|[;&|]\s*)uv\s+(?:add|sync|remove)\b", lower):
        return "pip"

    if re.search(
        r"(?:^|[;&|]\s*)(?:npx\s+)?(?:eslint|biome)(?:\s|$)"
        r"|(?:^|[;&|]\s*)(?:npm|pnpm|yarn)\s+run\s+lint\b",
        lower,
    ):
        return "js_lint"
    if re.search(
        r"(?:^|[;&|]\s*)(?:npx\s+)?tsc(?:\s|$)"
        r"|(?:^|[;&|]\s*)(?:npm|pnpm|yarn)\s+run\s+typecheck\b",
        lower,
    ):
        return "tsc"
    if re.search(
        r"(?:^|[;&|]\s*)(?:npx\s+)?next\s+build\b"
        r"|(?:^|[;&|]\s*)(?:npm|pnpm|yarn)\s+run\s+build\b",
        lower,
    ):
        return "next_build"

    if re.search(
        r"(?:^|[;&|]\s*)(?:kubectl|oc)\s+"
        r"(?:get|logs|describe|apply|delete|rollout|exec|top|config|"
        r"port-forward|wait|patch|scale|cordon|drain|taint|label|"
        r"annotate|create|replace|expose|set|auth|api-resources|"
        r"cluster-info|version)\b"
        r"|(?:^|[;&|]\s*)helm\s+"
        r"(?:list|ls|status|get|install|upgrade|uninstall|rollback|history|"
        r"template|diff)\b",
        lower,
    ):
        return "kubectl"
    if re.search(
        r"(?:^|[;&|]\s*)(?:terraform|tofu|pulumi)\s+"
        r"(?:plan|apply|destroy|preview|up|refresh)\b",
        lower,
    ):
        return "terraform"
    if re.search(
        r"(?:^|[;&|]\s*)make\s+(?:build|all|install|release|package)\b"
        r"|(?:^|[;&|]\s*)(?:gradle|mvn|cmake)\b",
        lower,
    ):
        return "make_build"
    if re.search(r"(?:^|[;&|]\s*)(?:curl|wget)\b", lower):
        return "curl"
    if re.search(r"(?:^|[;&|]\s*)gh\s+(?:pr|issue|run|api)\b", lower):
        return "gh"

    return "generic"


def _collapse_blank_lines(text: str) -> str:
    return _BLANK_RUN_RE.sub("\n\n", text).strip()


def _drop_progress_lines(lines: list[str]) -> list[str]:
    return [line for line in lines if not _PROGRESS_RE.match(line)]


def _dedupe_runs(lines: list[str], *, min_run: int = 4) -> list[str]:
    """Collapse 4+ identical consecutive lines into one + count note."""
    if not lines:
        return lines
    out: list[str] = []
    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        run = j - i
        if run >= min_run and lines[i].strip():
            out.append(lines[i])
            out.append(f"… ({run - 1} identical lines omitted)")
        else:
            out.extend(lines[i:j])
        i = j
    return out


def _split_exit_trailer(text: str) -> tuple[str, str]:
    """Keep ``Exit code: N`` / ``STDERR:`` trailer intact when present."""
    match = re.search(r"\n(?:STDERR:\n[\s\S]*?\n)?Exit code:\s*-?\d+\s*$", text)
    if not match:
        # Also accept a bare trailing Exit code line without leading newline edge.
        match = re.search(r"(?:^|\n)Exit code:\s*-?\d+\s*$", text)
    if not match:
        return text, ""
    return text[: match.start()].rstrip(), text[match.start() :].lstrip("\n")


def _filter_git_status(body: str) -> str:
    lines = [ln.rstrip() for ln in body.splitlines()]
    lines = _drop_progress_lines(lines)
    # Prefer porcelain-ish short view when verbose status was used.
    branch = ""
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    other: list[str] = []
    section: str | None = None
    for ln in lines:
        low = ln.lower().strip()
        if low.startswith("on branch ") or low.startswith("head detached"):
            branch = ln.strip()
            continue
        if low.startswith("your branch is"):
            other.append(ln.strip())
            continue
        if "changes to be committed" in low:
            section = "staged"
            continue
        if "changes not staged" in low:
            section = "unstaged"
            continue
        if "untracked files" in low:
            section = "untracked"
            continue
        if low.startswith("no changes added") or low.startswith("("):
            continue
        if not ln.strip():
            continue
        # short / porcelain: XY path
        m = re.match(r"^([ MADRCU?!]{1,2})\s+(.+)$", ln)
        if m and len(m.group(1).strip()) <= 2:
            code, path = m.group(1), m.group(2)
            if "?" in code:
                untracked.append(path)
            elif code[0] not in (" ", "?"):
                staged.append(f"{code.strip()} {path}")
            else:
                unstaged.append(f"{code.strip()} {path}")
            continue
        if section == "staged" and ln.strip().startswith(("new file", "modified", "deleted", "renamed")):
            staged.append(ln.strip())
        elif section == "unstaged" and ln.strip().startswith(("modified", "deleted", "new file")):
            unstaged.append(ln.strip())
        elif section == "untracked":
            untracked.append(ln.strip().lstrip("./"))
        elif section is None:
            other.append(ln.strip())

    parts: list[str] = []
    if branch:
        parts.append(branch)
    parts.extend(other[:3])
    if staged:
        parts.append(f"staged ({len(staged)}):")
        parts.extend(f"  {x}" for x in staged[:80])
        if len(staged) > 80:
            parts.append(f"  … +{len(staged) - 80} more")
    if unstaged:
        parts.append(f"unstaged ({len(unstaged)}):")
        parts.extend(f"  {x}" for x in unstaged[:80])
        if len(unstaged) > 80:
            parts.append(f"  … +{len(unstaged) - 80} more")
    if untracked:
        parts.append(f"untracked ({len(untracked)}):")
        parts.extend(f"  {x}" for x in untracked[:40])
        if len(untracked) > 40:
            parts.append(f"  … +{len(untracked) - 40} more")
    if not staged and not unstaged and not untracked and not other:
        # Nothing parsed: fall back to cleaned lines.
        return _collapse_blank_lines("\n".join(_dedupe_runs(lines)))
    if not staged and not unstaged and not untracked:
        parts.append("clean working tree")
    return "\n".join(parts)


def _filter_git_diff(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    out: list[str] = []
    context_kept = 0
    for ln in lines:
        if ln.startswith(("diff --git", "index ", "--- ", "+++ ", "@@", "Binary files")):
            out.append(ln)
            context_kept = 0
            continue
        if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---")):
            out.append(ln)
            context_kept = 0
            continue
        # Shrink unchanged context hunks.
        if ln.startswith(" "):
            if context_kept < 1:
                out.append(ln)
            context_kept += 1
            continue
        if ln.startswith("new file") or ln.startswith("deleted file") or ln.startswith("rename"):
            out.append(ln)
            continue
        if ln.strip():
            out.append(ln)
    return _collapse_blank_lines("\n".join(out))


def _filter_git_log(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines() if ln.strip()])
    # Collapse multi-line commit bodies to subject lines when possible.
    out: list[str] = []
    for ln in lines:
        if re.match(r"^(?:commit\s+)?[0-9a-f]{7,40}\b", ln, re.I):
            out.append(ln)
        elif ln.lower().startswith(("author:", "date:")):
            continue
        elif out and not ln.startswith((" ", "\t")):
            # Likely next subject in --oneline or after blank.
            out.append(ln)
        elif out and ln.startswith("    "):
            # First subject line of default git log format.
            if out[-1].startswith("commit ") or re.match(r"^[0-9a-f]{7,40}\b", out[-1], re.I):
                out[-1] = f"{out[-1][:12] if out[-1].startswith('commit ') else out[-1]} {ln.strip()}"
    if not out:
        out = lines[:40]
    return "\n".join(out[:60])


def _filter_git_mutate(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    keep = [
        ln
        for ln in lines
        if ln.strip()
        and not re.match(r"(?i)^(enumerating|counting|compressing|writing|remote:|unpacking)", ln)
    ]
    # Prefer a short confirmation.
    for ln in keep:
        low = ln.lower()
        if "changed" in low or "insertions" in low or "create mode" in low:
            return ln.strip()
        if re.search(r"\[[\w./-]+\s+[0-9a-f]{7,}\]", ln):
            return ln.strip()
        if low.startswith("ok ") or " -> " in ln or ln.startswith("To "):
            return ln.strip()
    compact = _collapse_blank_lines("\n".join(keep))
    if len(compact) > 400:
        return compact[:200] + "\n…\n" + compact[-150:]
    return compact or "ok"


def _filter_pytest(body: str) -> str:
    lines = [ln.rstrip() for ln in body.splitlines()]
    lines = _drop_progress_lines(lines)
    failures: list[str] = []
    summary: list[str] = []
    in_failure = False
    for ln in lines:
        low = ln.lower()
        if "=== failure" in low or "=== errors" in low or ln.startswith("FAILED "):
            in_failure = True
            failures.append(ln)
            continue
        if "=== short test summary" in low or "===.*failed" in low:
            in_failure = True
            failures.append(ln)
            continue
        if in_failure:
            if ln.startswith("=") and "passed" in low:
                in_failure = False
                summary.append(ln)
                continue
            failures.append(ln)
            if len(failures) > 200:
                in_failure = False
            continue
        if re.search(r"\d+ failed|\d+ error|\d+ passed|===.*in .*===", ln, re.I):
            summary.append(ln)
        elif "ERROR" in ln or "FAILED" in ln:
            failures.append(ln)
    if not failures and not summary:
        # All green or unrecognized: keep last ~30 lines + counts of dots line.
        kept = [ln for ln in lines if ln.strip()]
        if any("passed" in ln.lower() for ln in kept[-5:]):
            return "\n".join(kept[-8:])
        return _collapse_blank_lines("\n".join(kept[-40:]))
    parts = []
    if summary:
        parts.append(summary[-1] if len(summary) == 1 else "\n".join(summary[-3:]))
    if failures:
        parts.append(f"failures/errors ({min(len(failures), 120)} lines):")
        parts.extend(failures[:120])
        if len(failures) > 120:
            parts.append(f"… +{len(failures) - 120} lines omitted")
    else:
        parts.append("ok")
    return "\n".join(parts)


def _filter_jest_like(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    fails = [
        ln
        for ln in lines
        if re.search(r"(?i)\b(fail|error|●|✕|×)\b", ln) or ln.strip().startswith("Expected")
    ]
    summary = [ln for ln in lines if re.search(r"(?i)tests?:\s*\d+|test suites?:", ln)]
    if not fails:
        return "\n".join(summary[-5:] or lines[-20:]) or "ok"
    return "\n".join((summary[-3:] + fails[:100]))


def _filter_cargo_test(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    fails = [ln for ln in lines if "FAILED" in ln or ln.startswith("---- ") or "panicked at" in ln]
    summary = [ln for ln in lines if re.search(r"test result:|running \d+ test", ln, re.I)]
    oks = sum(1 for ln in lines if ln.endswith("... ok") or " ... ok" in ln)
    if not fails:
        if summary:
            return "\n".join(summary[-3:])
        return f"ok ({oks} tests)" if oks else _collapse_blank_lines("\n".join(lines[-30:]))
    head = summary[-2:] + [f"passed compact: {oks}"]
    return "\n".join(head + fails[:120])


def _filter_go_test(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    fails = [
        ln
        for ln in lines
        if ln.startswith("--- FAIL") or "\tError:" in ln or re.search(r"FAIL\s+\S+", ln)
    ]
    summary = [ln for ln in lines if re.match(r"^(ok|FAIL)\s+", ln) or ln.startswith("PASS")]
    if not fails:
        return "\n".join(summary[-10:] or ["ok"])
    return "\n".join(summary[-5:] + fails[:120])


def _filter_generic_test(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    fails = [
        ln
        for ln in lines
        if re.search(r"(?i)\b(fail(ed|ure)?|error|traceback|panic|assert)\b", ln)
    ]
    if not fails:
        return _collapse_blank_lines("\n".join(lines[-25:])) or "ok"
    return "\n".join(fails[:150])


def _filter_docker_ps(body: str) -> str:
    """Keep header + essential columns; drop wide padding noise."""
    lines = [ln.rstrip() for ln in body.splitlines() if ln.strip()]
    if not lines:
        return ""
    out: list[str] = []
    for i, ln in enumerate(lines):
        # Compact common `docker ps` table rows to id/image/status/names-ish.
        parts = re.split(r"\s{2,}", ln.strip())
        if i == 0 and parts and parts[0].upper() in {"CONTAINER", "CONTAINER ID", "ID"}:
            out.append("ID  IMAGE  STATUS  NAMES")
            continue
        if len(parts) >= 4:
            cid = parts[0][:12]
            image = parts[1] if len(parts) > 1 else "?"
            # STATUS is usually near the end before NAMES.
            status = parts[-2] if len(parts) >= 3 else parts[-1]
            names = parts[-1]
            out.append(f"{cid}  {image}  {status}  {names}")
        else:
            out.append(ln.strip())
    return "\n".join(out[:80])


def _filter_docker_logs(body: str) -> str:
    lines = [ln.rstrip() for ln in body.splitlines()]
    lines = _drop_progress_lines(lines)
    lines = _dedupe_runs(lines, min_run=3)
    # Prefer the tail (recent logs) after dedupe.
    if len(lines) > 80:
        head_n = 10
        return (
            "\n".join(lines[:head_n])
            + f"\n… ({len(lines) - 60} middle lines omitted)\n"
            + "\n".join(lines[-50:])
        )
    return _collapse_blank_lines("\n".join(lines))


def _filter_docker(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    lines = _dedupe_runs(lines)
    keep = [
        ln
        for ln in lines
        if ln.strip()
        and not re.match(r"(?i)^(deprecated|WARNING:|Using default tag)", ln)
    ]
    return _collapse_blank_lines("\n".join(keep[-60:] if len(keep) > 60 else keep))


def _filter_npm_install(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    lines = _dedupe_runs(lines)
    summary: list[str] = []
    warnings: list[str] = []
    errors: list[str] = []
    for ln in lines:
        if not ln.strip():
            continue
        if re.search(r"(?i)\berror\b|ERR!", ln):
            errors.append(ln)
        elif re.search(r"(?i)warn|deprecated", ln):
            if len(warnings) < 15:
                warnings.append(ln)
        elif re.search(
            r"(?i)added \d+|removed \d+|changed \d+|audited \d+|packages? in |"
            r"done in |found \d+ vulnerabilit",
            ln,
        ):
            summary.append(ln)
    parts: list[str] = []
    if summary:
        parts.extend(summary[-8:])
    if errors:
        parts.append(f"errors ({min(len(errors), 40)}):")
        parts.extend(errors[:40])
    elif warnings:
        parts.append(f"warnings (showing {min(len(warnings), 10)}):")
        parts.extend(warnings[:10])
    if not parts:
        # Fall back: last meaningful lines only.
        kept = [ln for ln in lines if ln.strip()]
        return _collapse_blank_lines("\n".join(kept[-25:])) or "ok"
    return "\n".join(parts)


def _filter_python_lint(body: str) -> str:
    """Ruff / mypy / pylint / flake8 / pyright - group by file, keep violations."""
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    findings: list[str] = []
    summary: list[str] = []
    current_file = ""
    for ln in lines:
        if not ln.strip():
            continue
        # File headers (ruff/mypy style).
        if re.match(r"^[\w./\\-]+\.(?:py|pyi)\s*:?\s*$", ln.strip()) or (
            ln.endswith(":") and "/" in ln and not ln.strip().startswith(("error", "note"))
        ):
            current_file = ln.strip().rstrip(":")
            continue
        if re.search(
            r"(?i)\berror\b|\bwarning\b|\bfail\b|\[([A-Z]\d+)\]|:\d+:\d+:|"
            r"Found \d+|All checks passed|would reformat|reformatted|"
            r"Success: no issues",
            ln,
        ):
            if current_file and not ln.strip().startswith(current_file):
                findings.append(f"{current_file}: {ln.strip()}")
            else:
                findings.append(ln.strip())
            if re.search(r"(?i)found \d+|all checks|success:|issues? in", ln):
                summary.append(ln.strip())
        elif re.match(r"^\s+\w+", ln) and findings:
            # continuation / caret lines - skip noise
            continue
    if not findings:
        kept = [ln for ln in lines if ln.strip()]
        if any(re.search(r"(?i)all checks passed|success: no issues|would reformat 0", ln) for ln in kept):
            return next(
                (
                    ln
                    for ln in kept
                    if re.search(r"(?i)all checks|success:|found 0", ln)
                ),
                "ok",
            )
        return _collapse_blank_lines("\n".join(kept[-40:])) or "ok"
    # Dedupe identical findings, cap list.
    seen: set[str] = set()
    uniq: list[str] = []
    for f in findings:
        if f in seen:
            continue
        seen.add(f)
        uniq.append(f)
    head = summary[-2:] if summary else [f"{len(uniq)} finding(s)"]
    return "\n".join(head + uniq[:120] + ([f"… +{len(uniq) - 120} more"] if len(uniq) > 120 else []))


def _filter_pip(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    lines = _dedupe_runs(lines)
    keep: list[str] = []
    for ln in lines:
        low = ln.lower()
        if not ln.strip():
            continue
        if re.match(r"(?i)^(requirement already|collecting |using cached|downloading )", ln):
            continue
        if "---" in ln and "progress" in low:
            continue
        keep.append(ln)
    # pip list / freeze: keep as-is but cap.
    if keep and all("==" in ln or re.match(r"^[\w.-]+\s+\S+", ln) for ln in keep[:5]):
        if len(keep) > 60:
            return "\n".join(keep[:50]) + f"\n… +{len(keep) - 50} packages"
        return "\n".join(keep)
    errors = [ln for ln in keep if re.search(r"(?i)\berror\b|failed|no matching", ln)]
    summary = [
        ln
        for ln in keep
        if re.search(r"(?i)successfully installed|already satisfied|audited|installed ", ln)
    ]
    if errors:
        return "\n".join((summary[-3:] + errors[:40]))
    if summary:
        return "\n".join(summary[-10:])
    return _collapse_blank_lines("\n".join(keep[-40:])) or "ok"


def _filter_errors_first(body: str, *, ok_hints: tuple[str, ...] = ()) -> str:
    """Keep error/warning lines + short summary; drop compile chatter."""
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    lines = _dedupe_runs(lines)
    errors: list[str] = []
    summary: list[str] = []
    for ln in lines:
        if not ln.strip():
            continue
        low = ln.lower()
        if re.search(
            r"(?i)\berror\b|\bfailed\b|\bwarning\b|panic:|undefined|cannot find|"
            r"TS\d+|error\[|FAILED:|✘|×",
            ln,
        ):
            errors.append(ln)
        elif ok_hints and any(h in low for h in ok_hints):
            summary.append(ln)
        elif re.search(r"(?i)finished|compiled|success|done in |built in ", ln):
            summary.append(ln)
    if errors:
        return "\n".join(summary[-4:] + errors[:150])
    if summary:
        return "\n".join(summary[-12:])
    kept = [ln for ln in lines if ln.strip()]
    return _collapse_blank_lines("\n".join(kept[-40:])) or "ok"


def _filter_cargo_build(body: str) -> str:
    return _filter_errors_first(
        body,
        ok_hints=("finished", "compiling", "checking", "warning: `"),
    )


def _filter_js_lint(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    findings = [
        ln
        for ln in lines
        if re.search(r"(?i)\berror\b|\bwarning\b|✖|✗|problem|prettier", ln)
        or re.search(r":\d+:\d+", ln)
    ]
    summary = [
        ln
        for ln in lines
        if re.search(r"(?i)\d+\s+problems?|\d+\s+errors?|eslint|biome", ln)
    ]
    if not findings:
        return "\n".join(summary[-5:] or ["ok"])
    return "\n".join(summary[-3:] + findings[:120])


def _filter_tsc(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    errors = [ln for ln in lines if re.search(r"error TS\d+|:\s*error\b", ln, re.I)]
    if not errors:
        kept = [ln for ln in lines if ln.strip()]
        return "\n".join(kept[-15:]) or "ok"
    return "\n".join(errors[:150])


def _filter_next_build(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    lines = _dedupe_runs(lines)
    keep = [
        ln
        for ln in lines
        if re.search(
            r"(?i)error|failed|warn|compiled|route|○|ƒ|λ|collecting page|"
            r"linting and checking|creating an optimized|done in |"
            r"build completed|failed to compile",
            ln,
        )
    ]
    if not keep:
        return _collapse_blank_lines("\n".join(lines[-30:])) or "ok"
    return "\n".join(keep[:120])


def _filter_playwright(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    fails = [
        ln
        for ln in lines
        if re.search(r"(?i)\b(fail|error|timeout|flaky)\b|✘|×|Error:", ln)
    ]
    summary = [
        ln
        for ln in lines
        if re.search(r"(?i)\d+\s+passed|\d+\s+failed|serving html|playwright", ln)
    ]
    if not fails:
        return "\n".join(summary[-8:] or lines[-20:]) or "ok"
    return "\n".join(summary[-4:] + fails[:120])


def _filter_kubectl(body: str) -> str:
    """Compact kubectl/oc/helm tables, describe blobs, and noisy events."""
    raw_lines = [ln.rstrip() for ln in body.splitlines()]
    lines = [ln for ln in raw_lines if ln.strip()]
    if not lines:
        return ""

    # describe: keep Name/Namespace/Status/Events and error-ish lines.
    if any(re.match(r"(?i)^(Name|Namespace|Status|Node|Pod):\s*", ln) for ln in lines[:8]):
        keep: list[str] = []
        in_events = False
        for ln in lines:
            if re.match(r"(?i)^Events?:\s*", ln):
                in_events = True
                keep.append(ln.strip())
                continue
            if in_events:
                if re.search(r"(?i)\b(warn|error|fail|backoff|oom|unhealthy|kill)\b", ln):
                    keep.append(ln.strip())
                elif re.match(r"^\S", ln) and not ln.startswith(" "):
                    in_events = False
                continue
            if re.match(
                r"(?i)^(Name|Namespace|Status|Node|Pod|Restart Count|Image|Ready|"
                r"Controlled By|QoS Class|Type|Reason|Message):\s*",
                ln,
            ) or re.search(r"(?i)\b(error|fail|crash|pending|oomkilled)\b", ln):
                keep.append(ln.strip())
        if keep:
            return "\n".join(keep[:120])

    # events / get tables: collapse wide columns; drop Normal spam if Warnings exist.
    warnings = [
        ln
        for ln in lines
        if re.search(r"(?i)\b(warning|error|failed|backoff|unhealthy)\b", ln)
    ]
    normals = [ln for ln in lines if re.search(r"(?i)\bNormal\b", ln)]
    if warnings and len(normals) > 5:
        lines = [ln for ln in lines if not re.search(r"(?i)\bNormal\b", ln)]

    out: list[str] = []
    for ln in lines[:120]:
        parts = re.split(r"\s{2,}", ln.strip())
        if len(parts) >= 3:
            out.append("  ".join(parts[:7]))
        else:
            out.append(ln.strip())
    if len(lines) > 120:
        out.append(f"… +{len(lines) - 120} lines")
    return "\n".join(_dedupe_runs(out, min_run=4))


def _filter_terraform(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    keep = [
        ln
        for ln in lines
        if re.search(
            r"(?i)error|warn|plan:|apply complete|destroy complete|"
            r"resources? to (?:add|change|destroy)|no changes|"
            r"pulumi\.|updating\(|created |deleted |failed ",
            ln,
        )
    ]
    if not keep:
        return _collapse_blank_lines("\n".join(lines[-40:])) or "ok"
    return "\n".join(keep[:150])


def _filter_make_build(body: str) -> str:
    return _filter_errors_first(
        body,
        ok_hints=("built", "success", "installing", "linking"),
    )


def _filter_curl(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    text = "\n".join(lines)
    if len(text) <= 4000:
        return _collapse_blank_lines(text)
    return text[:1500] + f"\n… ({len(text) - 3000:,} chars omitted)\n" + text[-1500:]


def _filter_gh(body: str) -> str:
    lines = _drop_progress_lines([ln.rstrip() for ln in body.splitlines()])
    lines = _dedupe_runs(lines)
    keep = [ln for ln in lines if ln.strip()]
    if len(keep) > 80:
        return "\n".join(keep[:60] + [f"… +{len(keep) - 60} lines"])
    return "\n".join(keep)


def _filter_generic(body: str) -> str:
    lines = [ln.rstrip() for ln in body.splitlines()]
    lines = _drop_progress_lines(lines)
    lines = _dedupe_runs(lines)
    return _collapse_blank_lines("\n".join(lines))


_KIND_FILTERS = {
    "git_status": _filter_git_status,
    "git_diff": _filter_git_diff,
    "git_log": _filter_git_log,
    "git_mutate": _filter_git_mutate,
    "pytest": _filter_pytest,
    "jest": _filter_jest_like,
    "cargo_test": _filter_cargo_test,
    "cargo_build": _filter_cargo_build,
    "go_test": _filter_go_test,
    "generic_test": _filter_generic_test,
    "docker_ps": _filter_docker_ps,
    "docker_logs": _filter_docker_logs,
    "docker": _filter_docker,
    "npm_install": _filter_npm_install,
    "python_lint": _filter_python_lint,
    "pip": _filter_pip,
    "js_lint": _filter_js_lint,
    "tsc": _filter_tsc,
    "next_build": _filter_next_build,
    "playwright": _filter_playwright,
    "kubectl": _filter_kubectl,
    "terraform": _filter_terraform,
    "make_build": _filter_make_build,
    "curl": _filter_curl,
    "gh": _filter_gh,
    "generic": _filter_generic,
}


def _exit_code_from_trailer(trailer: str) -> int | None:
    match = re.search(r"Exit code:\s*(-?\d+)", trailer)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def filter_command_output(command: str, output: str) -> FilterResult:
    """Return a compacted view of *output* for *command*.

    Never raises; on empty input returns as-is. Always strips ANSI.
    On non-zero exit codes, keep a larger failure-oriented view.
    """
    original = output if output is not None else ""
    if not original:
        return FilterResult(text="", kind="generic", original_chars=0, filtered_chars=0)

    kind = classify_command(command)
    cleaned = strip_ansi(original.replace("\r\n", "\n").replace("\r", "\n"))
    body, trailer = _split_exit_trailer(cleaned)
    stderr_prefix = ""
    if "\nSTDERR:\n" in body:
        body, stderr_chunk = body.rsplit("\nSTDERR:\n", 1)
        stderr_prefix = "STDERR:\n" + stderr_chunk.strip()
    elif body.startswith("STDERR:\n"):
        stderr_prefix = body.strip()
        body = ""

    exit_code = _exit_code_from_trailer(trailer)
    try:
        if exit_code not in (None, 0) and kind == "git_mutate":
            # Failed mutate: prefer cleaned stderr/body over a one-line "ok".
            filtered_body = _filter_generic(body)
        else:
            filtered_body = _KIND_FILTERS[kind](body)
    except Exception:
        filtered_body = _filter_generic(body)

    parts = [p for p in (filtered_body.strip(), stderr_prefix.strip(), trailer.strip()) if p]
    text = "\n".join(parts)
    # Never expand past the cleaned original.
    if len(text) > len(cleaned):
        text = cleaned
    return FilterResult(
        text=text,
        kind=kind,
        original_chars=len(original),
        filtered_chars=len(text),
    )


def should_replace_with_compaction(filtered: FilterResult) -> bool:
    """Whether exec should swap the raw tool result for *filtered*."""
    if filtered.filtered_chars >= filtered.original_chars:
        return False
    saved = filtered.saved_chars
    if saved >= 24:
        return True
    # Specialized kinds: apply even modest wins (git help text, etc.).
    return filtered.kind != "generic" and saved >= 8


def apply_exec_compaction(
    command: str,
    result: str,
    *,
    spool_path: str | None = None,
) -> str:
    """Compact *result* for LLM consumption, or return it unchanged.

    *spool_path* is an optional path already written with the raw output; when
    set it is mentioned in the compaction note. Callers that can spool should
    pass the path after writing.
    """
    if not result or result == "(no output)":
        return result
    try:
        filtered = filter_command_output(command, result)
    except Exception:
        return result
    if not should_replace_with_compaction(filtered):
        return result
    note = ""
    if spool_path:
        note = f"\n[compacted {filtered.saved_chars:,} chars; full output in {spool_path}]"
    else:
        note = f"\n[compacted {filtered.saved_chars:,} chars ({filtered.kind})]"
    return filtered.text + note


def compact_session_body(command: str, output: str, exit_code: int | None) -> str:
    """Compact a background-session body; strip a duplicated exit trailer."""
    if not output:
        return output
    src = output
    trailer = ""
    if exit_code is not None:
        trailer = f"Exit code: {exit_code}"
        if trailer not in src:
            src = src.rstrip() + f"\n{trailer}"
    compacted = apply_exec_compaction(command, src)
    if exit_code is not None and f"Exit code: {exit_code}" in compacted:
        compacted = compacted.rsplit(f"Exit code: {exit_code}", 1)[0].rstrip()
    return compacted
