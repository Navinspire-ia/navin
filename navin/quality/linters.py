"""Declarative linter execution and output normalization.

Every linter described in ``linters.json`` is reduced to the same
:class:`Diagnostic` shape, so the editor gutter, the agent tool, and the
verification loop all consume one format regardless of the underlying tool.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from navin.utils.proc import no_window_kwargs

_TABLE_PATH = Path(__file__).with_name("linters.json")
_MAX_DIAGNOSTICS = 500
_MAX_OUTPUT_CHARS = 2_000_000


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One normalized problem reported by a linter."""

    path: str
    line: int
    col: int
    end_line: int
    end_col: int
    severity: str
    code: str
    message: str
    tool: str

    def to_json(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "end_line": self.end_line,
            "end_col": self.end_col,
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "tool": self.tool,
        }

    def render(self) -> str:
        head = f"{self.path}:{self.line}:{self.col} {self.severity}"
        if self.code:
            head += f" [{self.code}]"
        return f"{head} {self.message}"


@dataclass(slots=True)
class LinterResult:
    """Outcome of running one linter."""

    linter: str
    ran: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
    skipped_reason: str = ""
    duration_ms: int = 0
    exit_code: int | None = None

    @property
    def errors(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for d in self.diagnostics if d.severity == "warning")

    def to_json(self) -> dict[str, Any]:
        return {
            "linter": self.linter,
            "ran": self.ran,
            "skipped_reason": self.skipped_reason,
            "duration_ms": self.duration_ms,
            "errors": self.errors,
            "warnings": self.warnings,
            "diagnostics": [d.to_json() for d in self.diagnostics],
        }


# ---------------------------------------------------------------------------
# Table loading
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def linter_table() -> dict[str, dict[str, Any]]:
    """Packaged linters merged with an optional user override."""
    packaged = _read_json(_TABLE_PATH).get("linters", {})
    merged: dict[str, dict[str, Any]] = dict(packaged) if isinstance(packaged, dict) else {}
    with suppress(Exception):
        from navin.config.loader import get_config_path

        user = _read_json(get_config_path().parent / "linters.json").get("linters")
        if isinstance(user, dict):
            merged.update(user)
    return merged


def linters_for(suffix: str, *, scope: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Linters that handle ``suffix``, optionally filtered by scope."""
    out: list[tuple[str, dict[str, Any]]] = []
    for name, spec in linter_table().items():
        if suffix.lower() not in [str(e).lower() for e in spec.get("extensions", [])]:
            continue
        if scope and str(spec.get("scope", "file")) != scope:
            continue
        out.append((name, spec))
    return out


# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def _executable_suffixes() -> tuple[str, ...]:
    """Suffixes to append to a bare tool path before giving up on it.

    Only Windows needs this. npm writes its shims twice, an extensionless shell
    script for POSIX and a ``.cmd`` beside it, and a virtualenv stores
    ``ruff.exe`` rather than ``ruff``. The bare name therefore either does not
    exist or is a shell script Windows cannot launch, so the suffixed forms have
    to be tried first and the empty suffix kept last as the POSIX case.
    """
    if os.name != "nt":
        return ("",)
    pathext = os.environ.get("PATHEXT") or ".COM;.EXE;.BAT;.CMD"
    # Always semicolon-separated: PATHEXT is a Windows variable, so os.pathsep
    # would only agree by coincidence of already running there.
    suffixes = [ext.lower() for ext in pathext.split(";") if ext.strip()]
    return (*suffixes, "")


def _first_executable(base: Path) -> Path | None:
    for suffix in _executable_suffixes():
        candidate = Path(f"{base}{suffix}") if suffix else base
        if candidate.is_file():
            return candidate
    return None


_PYTHON_NAMES = frozenset({"python", "python3"})


def _packaged_interpreter(name: str) -> str | None:
    """The shim that means "this build's own Python", in a packaged install.

    Several entries here name ``python3``: a packaged build has no interpreter on
    disk to find, so without this a machine with no system Python loses
    ``pyflakes``, ``unittest`` and every other tool spelled as an interpreter
    call, while the interpreter is sitting inside the bundle.
    """
    from navin.python_runtime import interpreter_shim_dir, packaged

    if not packaged() or name not in _PYTHON_NAMES:
        return None
    from navin.config.paths import get_runtime_subdir

    try:
        shims = interpreter_shim_dir(get_runtime_subdir("bin"))
    except OSError:
        return None
    if shims is None:
        return None
    return str(_first_executable(shims / name) or "") or None


@lru_cache(maxsize=256)
def _resolve_binary(name: str, project_paths: tuple[str, ...], python_env: bool, root: str) -> str | None:
    root_path = Path(root)
    for rel in project_paths:
        found = _first_executable(root_path / rel)
        if found is not None:
            return str(found)
    if python_env:
        # A virtualenv keeps its tools next to the interpreter, but a system
        # install on Windows puts them one level down in Scripts/.
        interpreter_dir = Path(sys.executable).parent
        for directory in (interpreter_dir, interpreter_dir / "Scripts"):
            found = _first_executable(directory / name)
            if found is not None:
                return str(found)
        shim = _packaged_interpreter(name)
        if shim is not None:
            return shim
    found_on_path = shutil.which(name)
    if found_on_path is not None:
        return found_on_path
    # Last: what a packaged build brought with it. A tool belonging to the
    # project comes first, since it matches the project's configuration.
    from navin.python_runtime import bundled_tool

    return bundled_tool(name)


def _binary_for(spec: dict[str, Any], root: Path) -> str | None:
    binary = spec.get("binary")
    if not isinstance(binary, dict):
        return None
    name = str(binary.get("name") or "")
    if not name:
        return None
    return _resolve_binary(
        name,
        tuple(str(p) for p in binary.get("project_paths", [])),
        bool(binary.get("python_env")),
        str(root),
    )


def _bundled_node_argv(script: str) -> list[str] | None:
    """Launch a JavaScript tool the build ships, e.g. ``typescript/bin/tsc``.

    Needs node on the machine: navin bundles the package, not a JavaScript
    runtime. Without one the tool is simply unavailable, as before.
    """
    package, _, relative = script.partition("/")
    if not package or not relative:
        return None
    from navin.python_runtime import bundled_node_package

    root = bundled_node_package(package)
    if root is None:
        return None
    entry = root / relative
    if not entry.is_file():
        return None
    node = shutil.which("node")
    return [node, str(entry)] if node else None


def tool_argv(spec: dict[str, Any], root: Path) -> list[str] | None:
    """How to launch this tool here, or None when it is not available.

    An installed program is used as it is. Otherwise, a tool declared with
    ``run_as_module`` is launched through the interpreter, which is what makes
    yamllint, pytest and pylsp work in a packaged build: the module is inside the
    bundle, but the console script that would normally start it is not.
    ``node_script`` is the same idea for JavaScript tooling such as tsc, and
    comes last so a copy belonging to the project always wins.
    """
    binary = _binary_for(spec, root)
    if binary is not None:
        return [binary]
    module = str((spec.get("binary") or {}).get("run_as_module") or "")
    from navin.python_runtime import module_available, python_command

    if module_available(module):
        return [*python_command(), "-m", module]
    script = str((spec.get("binary") or {}).get("node_script") or "")
    return _bundled_node_argv(script) if script else None


def _matched_requirement(spec: dict[str, Any], root: Path) -> Path | None:
    """The configuration file that makes this linter apply, if any.

    ``requires`` is satisfied by a file merely existing, which is enough for a
    dedicated config such as ``tsconfig.json``. ``requires_content`` additionally
    demands a marker inside the file, because the file that configures a Python
    type checker is usually ``pyproject.toml``, which nearly every project has:
    keying off its existence alone would unleash mypy on repositories that never
    asked for it and bury the real diagnostics.
    """
    for rel in spec.get("requires", []):
        candidate = root / str(rel)
        if candidate.exists():
            return candidate
    for rule in spec.get("requires_content", []):
        if not isinstance(rule, dict):
            continue
        candidate = root / str(rule.get("file", ""))
        marker = str(rule.get("contains", ""))
        if not marker or not candidate.is_file():
            continue
        try:
            if marker in candidate.read_text(encoding="utf-8", errors="replace"):
                return candidate
        except OSError:
            continue
    return None


def _requirements_met(spec: dict[str, Any], root: Path) -> bool:
    if not (spec.get("requires") or spec.get("requires_content")):
        return True
    return _matched_requirement(spec, root) is not None


def _work_dir(spec: dict[str, Any], root: Path) -> Path:
    """Directory to run the linter from.

    Type checkers resolve their configuration relative to the working
    directory, so in a monorepo a linter configured at ``webui/tsconfig.json``
    must run from ``webui/``: running it from the repository root makes it exit
    immediately with "no config found", which would otherwise look like a clean
    result.
    """
    matched = _matched_requirement(spec, root)
    if matched is None:
        return root
    return matched.parent if matched.is_file() else matched


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------


def _dig(row: dict[str, Any], dotted: str) -> Any:
    current: Any = row
    for part in dotted.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _severity_for(
    spec: dict[str, Any], raw_row: dict[str, Any] | None, raw_severity: str, code: str
) -> str:
    mapping = spec.get("severity_map")
    if isinstance(mapping, dict):
        field_name = spec.get("severity_field")
        value = raw_severity
        if field_name and raw_row is not None:
            value = str(_dig(raw_row, str(field_name)) or "")
        mapped = mapping.get(value)
        if isinstance(mapped, str):
            return mapped
    # Some tools have no severity channel: a code allowlist marks hard errors.
    error_codes = spec.get("error_codes")
    if isinstance(error_codes, list) and code in {str(c) for c in error_codes}:
        return "error"
    default = str(spec.get("default_severity", "warning"))
    return default if default in {"error", "warning", "info"} else "warning"


def _normalize_path(raw: str, root: Path, fallback: str, base: Path | None = None) -> str:
    """Report a tool-reported path relative to the project root.

    ``base`` is the directory the tool ran from, which is what its relative
    paths are relative to; it differs from ``root`` for a linter configured in a
    subdirectory.
    """
    text = (raw or "").strip()
    if not text:
        return fallback
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = (base or root) / candidate
    # Resolve both sides: comparing a resolved candidate against an unresolved
    # root silently fails whenever the caller passes a root that resolve() would
    # rewrite, which on Windows is any drive-less path, and the whole absolute
    # path then leaks into the report.
    with suppress(ValueError):
        return (
            candidate.resolve(strict=False)
            .relative_to(root.resolve(strict=False))
            .as_posix()
        )
    return candidate.as_posix()


def _parse_json_rows(
    spec: dict[str, Any],
    name: str,
    stdout: str,
    root: Path,
    fallback_path: str,
    base: Path | None = None,
) -> list[Diagnostic]:
    try:
        payload = json.loads(stdout or "[]")
    except ValueError:
        return []
    for key in spec.get("rows_path", []):
        if isinstance(payload, dict):
            payload = payload.get(str(key))
    if not isinstance(payload, list):
        return []

    fields = spec.get("fields", {})
    if not isinstance(fields, dict):
        return []
    nested_key = spec.get("nested")
    prefix = str(spec.get("code_prefix", ""))
    # Pyright reports LSP-style ranges, counted from zero. Everything downstream -
    # the editor gutter, the agent's read_file line numbers - is 1-based, so an
    # unadjusted report points one line above the actual problem.
    shift = 1 if spec.get("zero_based_positions") else 0

    out: list[Diagnostic] = []
    for group in payload:
        if not isinstance(group, dict):
            continue
        if nested_key:
            rows = group.get(str(nested_key))
            group_path = str(group.get("filePath") or group.get("path") or "")
            if not isinstance(rows, list):
                continue
        else:
            rows = [group]
            group_path = ""
        for row in rows:
            if not isinstance(row, dict):
                continue
            line = _as_int(_dig(row, str(fields.get("line", "line"))), 1) + shift
            col = _as_int(_dig(row, str(fields.get("col", "col"))), 1) + shift
            code_value = _dig(row, str(fields.get("code", "code")))
            code = f"{prefix}{code_value}" if code_value not in (None, "") else ""
            row_path = str(
                _dig(row, str(fields.get("file", "filename")))
                or group_path
                or fallback_path
            )
            out.append(
                Diagnostic(
                    path=_normalize_path(row_path, root, fallback_path, base),
                    line=line,
                    col=col,
                    end_line=_as_int(
                        _dig(row, str(fields.get("end_line", ""))), line - shift
                    )
                    + shift,
                    end_col=_as_int(
                        _dig(row, str(fields.get("end_col", ""))), col + 1 - shift
                    )
                    + shift,
                    severity=_severity_for(spec, row, "", code),
                    code=code,
                    message=str(_dig(row, str(fields.get("message", "message"))) or "").strip(),
                    tool=name,
                )
            )
            if len(out) >= _MAX_DIAGNOSTICS:
                return out
    return out


def _parse_regex_lines(
    spec: dict[str, Any],
    name: str,
    output: str,
    root: Path,
    fallback_path: str,
    base: Path | None = None,
) -> list[Diagnostic]:
    raw = spec.get("regex")
    if not raw:
        return []
    try:
        pattern = re.compile(str(raw))
    except re.error:
        return []
    out: list[Diagnostic] = []
    for line in output.splitlines():
        match = pattern.search(line)
        if match is None:
            continue
        groups = match.groupdict()
        line_no = _as_int(groups.get("line"), 1)
        col_no = _as_int(groups.get("col"), 1)
        code = str(groups.get("code") or "")
        out.append(
            Diagnostic(
                path=_normalize_path(
                    str(groups.get("file") or ""), root, fallback_path, base
                ),
                line=line_no,
                col=col_no,
                end_line=line_no,
                end_col=col_no + 1,
                severity=_severity_for(spec, None, str(groups.get("severity") or ""), code),
                code=code,
                message=str(groups.get("message") or "").strip(),
                tool=name,
            )
        )
        if len(out) >= _MAX_DIAGNOSTICS:
            break
    return out


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _run_linter(
    name: str,
    spec: dict[str, Any],
    root: Path,
    target: Path | None,
) -> LinterResult:
    import time

    if not _requirements_met(spec, root):
        return LinterResult(
            linter=name,
            ran=False,
            skipped_reason="no matching config file in the project",
        )
    launch = tool_argv(spec, root)
    if launch is None:
        wanted = (spec.get("binary") or {}).get("name", name)
        return LinterResult(
            linter=name, ran=False, skipped_reason=f"{wanted} not installed"
        )

    argv = list(launch)
    for arg in spec.get("args", []):
        text = str(arg)
        text = text.replace("{root}", str(root))
        if "{file}" in text:
            if target is None:
                continue
            text = text.replace("{file}", str(target))
        argv.append(text)

    work_dir = _work_dir(spec, root)
    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603
            argv,
            cwd=str(work_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(spec.get("timeout_s", 30)),
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return LinterResult(
            linter=name,
            ran=False,
            skipped_reason=f"timed out after {spec.get('timeout_s', 30)}s",
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return LinterResult(linter=name, ran=False, skipped_reason=str(exc))

    duration_ms = int((time.monotonic() - started) * 1000)
    ok_codes = {int(c) for c in spec.get("ok_exit_codes", [0])}
    # Tools like `php -l` print nothing useful on success; only parse failures.
    if spec.get("only_when_failing") and completed.returncode == 0:
        return LinterResult(
            linter=name, ran=True, duration_ms=duration_ms, exit_code=0
        )
    if completed.returncode not in ok_codes:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        first = detail[0][:200] if detail else f"exit code {completed.returncode}"
        return LinterResult(
            linter=name,
            ran=False,
            skipped_reason=f"{name} failed to run: {first}",
            duration_ms=duration_ms,
            exit_code=completed.returncode,
        )

    fallback = ""
    if target is not None:
        with suppress(ValueError):
            fallback = (
                target.resolve(strict=False)
                .relative_to(root.resolve(strict=False))
                .as_posix()
            )
        if not fallback:
            fallback = target.name

    stdout = (completed.stdout or "")[:_MAX_OUTPUT_CHARS]
    stderr = (completed.stderr or "")[:_MAX_OUTPUT_CHARS]
    if str(spec.get("parser")) == "json_rows":
        diagnostics = _parse_json_rows(spec, name, stdout, root, fallback, work_dir)
    else:
        diagnostics = _parse_regex_lines(
            spec, name, stdout + "\n" + stderr, root, fallback, work_dir
        )

    if completed.returncode != 0 and not diagnostics:
        # A nonzero exit with nothing parsed means the tool refused to run
        # (missing config, bad flag, broken install). Reporting that as "clean"
        # would manufacture false confidence, so surface it as not-run.
        detail = (stderr or stdout).strip().splitlines()
        first = detail[0][:200] if detail else f"exit code {completed.returncode}"
        return LinterResult(
            linter=name,
            ran=False,
            skipped_reason=f"{name} reported no diagnostics but failed: {first}",
            duration_ms=duration_ms,
            exit_code=completed.returncode,
        )

    return LinterResult(
        linter=name,
        ran=True,
        diagnostics=diagnostics,
        duration_ms=duration_ms,
        exit_code=completed.returncode,
    )


def lint_file(root: Path, rel_path: str) -> list[LinterResult]:
    """Run every file-scope linter that handles ``rel_path``."""
    target = (root / rel_path).resolve(strict=False)
    suffix = target.suffix.lower()
    results: list[LinterResult] = []
    for name, spec in linters_for(suffix, scope="file"):
        results.append(_run_linter(name, spec, root, target))
    if suffix == ".json":
        results.append(_lint_json_file(target, root, rel_path))
    # Product UI gate (em dashes, fake buttons, framer-motion) on every edit.
    from navin.quality.product_ui import lint_product_ui

    results.append(lint_product_ui(root, [rel_path]))
    return results


def _lint_json_file(target: Path, root: Path, rel_path: str) -> LinterResult:
    """JSON validity via the stdlib, so it needs no external tool."""
    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return LinterResult(linter="json", ran=False, skipped_reason=str(exc))
    try:
        json.loads(raw)
    except ValueError as exc:
        line = getattr(exc, "lineno", 1) or 1
        col = getattr(exc, "colno", 1) or 1
        return LinterResult(
            linter="json",
            ran=True,
            diagnostics=[
                Diagnostic(
                    path=rel_path,
                    line=line,
                    col=col,
                    end_line=line,
                    end_col=col + 1,
                    severity="error",
                    code="json",
                    message=getattr(exc, "msg", str(exc)),
                    tool="json",
                )
            ],
        )
    return LinterResult(linter="json", ran=True)


def fix_file(root: Path, rel_path: str) -> list[LinterResult]:
    """Apply in-place auto-fixes from linters that define a safe fixer."""
    target = (root / rel_path).resolve(strict=False)
    suffix = target.suffix.lower()
    results: list[LinterResult] = []
    for name, spec in linters_for(suffix, scope="file"):
        if not spec.get("fix_args"):
            continue
        fix_spec = {**spec, "args": spec["fix_args"], "parser": "none"}
        result = _run_linter(name, fix_spec, root, target)
        result.diagnostics = []
        results.append(result)
    return results


def fixable_extensions() -> set[str]:
    """Extensions for which at least one linter can auto-fix."""
    out: set[str] = set()
    for spec in linter_table().values():
        if spec.get("fix_args"):
            out.update(str(e).lower() for e in spec.get("extensions", []))
    return out


def lint_project(
    root: Path,
    *,
    linter_names: list[str] | None = None,
    include_project_scope: bool = True,
) -> list[LinterResult]:
    """Run project-scope linters (type checkers, vet) across the repository."""
    table = linter_table()
    results: list[LinterResult] = []
    for name, spec in table.items():
        if linter_names is not None and name not in linter_names:
            continue
        scope = str(spec.get("scope", "file"))
        if scope != "project":
            continue
        if not include_project_scope:
            continue
        results.append(_run_linter(name, spec, root, None))
    return results


def available_linters(root: Path) -> list[dict[str, Any]]:
    """Report which linters are usable for this project, and why not."""
    out: list[dict[str, Any]] = []
    for name, spec in sorted(linter_table().items()):
        launch = tool_argv(spec, root)
        reason = ""
        if launch is None:
            reason = f"{(spec.get('binary') or {}).get('name', name)} not installed"
        elif not _requirements_met(spec, root):
            reason = "no matching config file in the project"
        out.append(
            {
                "linter": name,
                "language": str(spec.get("language", "")),
                "scope": str(spec.get("scope", "file")),
                "extensions": [str(e) for e in spec.get("extensions", [])],
                "available": not reason,
                "reason": reason,
            }
        )
    return out
