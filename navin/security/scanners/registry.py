# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Registry of external scanners: identity, licence, permissions, run, parse.

Every entry says what the tool is allowed to do (read the tree, reach the
network, write a report file) so the planner and the user see it before a run.
A run is bounded by a timeout, never reads stdin and never opens a console
window on Windows. Most of these tools exit non zero when they find
something, so each entry lists the exit codes that still carry a report; any
other code, an empty or unreadable report, or an error document is a failed
run. A missing binary is reported as such: a run that did not happen never
reads as a clean result.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from navin.security import catalog
from navin.security.model import Finding
from navin.security.scanners import adapters
from navin.utils.proc import no_window_kwargs

REPORT_PLACEHOLDER = "{report}"
ROOT_PLACEHOLDER = "{root}"
_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")
_VERSION_TIMEOUT_S = 10
RUN_STATUSES: tuple[str, ...] = ("ok", "not_installed", "not_applicable", "timeout", "failed", "invalid_output")


@dataclass(frozen=True)
class Permissions:
    """What a scanner does on the machine, shown before it runs."""

    read_tree: bool = True
    network: bool = False
    writes_report: bool = False
    note: str = ""


@dataclass(frozen=True)
class ScannerSpec:
    id: str
    binary: str
    license: str
    homepage: str
    families: tuple[str, ...]
    argv: tuple[str, ...]
    parse: Callable[[Any], list[Finding]]
    permissions: Permissions = field(default_factory=Permissions)
    version_args: tuple[str, ...] = ("--version",)
    requires: tuple[str, ...] = ()  # the root must hold one of these files
    ok_exit_codes: frozenset[int] = frozenset({0})
    not_applicable_exit_codes: frozenset[int] = frozenset()
    argv_for: Callable[[Path], tuple[str, ...]] | None = None  # argv that depends on the root
    timeout_s: int = 300
    install_hint: str = ""

    def applicable(self, root: Path) -> bool:
        return not self.requires or any((root / name).exists() for name in self.requires)

    def arguments(self, root: Path) -> tuple[str, ...]:
        return self.argv_for(root) if self.argv_for else self.argv

    def uses_report_file(self, root: Path) -> bool:
        return any(REPORT_PLACEHOLDER in part for part in self.arguments(root))

    def command(self, binary_path: str, root: Path, report: Path | None = None) -> list[str]:
        out = [binary_path]
        for part in self.arguments(root):
            part = part.replace(ROOT_PLACEHOLDER, str(root))
            part = part.replace(REPORT_PLACEHOLDER, str(report or ""))
            out.append(part)
        return out

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "binary": self.binary,
            "license": self.license,
            "homepage": self.homepage,
            "families": list(self.families),
            "permissions": asdict(self.permissions),
            "requires": list(self.requires),
            "timeout_s": self.timeout_s,
            "install_hint": self.install_hint,
            "controls": [c.id for c in catalog.controls_for_scanner(self.id)],
        }


@dataclass
class ScannerStatus:
    id: str
    available: bool
    path: str = ""
    version: str = ""
    error: str = ""


@dataclass
class ScannerRun:
    id: str
    ok: bool
    status: str
    findings: list[Finding] = field(default_factory=list)
    exit_code: int | None = None
    duration_s: float = 0.0
    detail: str = ""
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [f.to_dict() for f in self.findings]
        return data


def _pip_audit_argv(root: Path) -> tuple[str, ...]:
    # Without an input pip-audit audits the environment it runs in (its own
    # pipx venv), not the project: always hand it the project's dependencies.
    base = ("-f", "json", "--progress-spinner", "off")
    if (root / "requirements.txt").exists():
        return ("-r", "requirements.txt", *base)
    return (*base, ROOT_PLACEHOLDER)


_TREE = Permissions(read_tree=True)
_DB = Permissions(read_tree=True, network=True, note="downloads or queries a vulnerability database")

REGISTRY: dict[str, ScannerSpec] = {
    spec.id: spec
    for spec in (
        ScannerSpec(
            id="semgrep", binary="semgrep", license="LGPL-2.1", homepage="https://semgrep.dev",
            families=("injection", "code_execution", "files", "cryptography", "authentication"),
            # p/default, not auto: semgrep refuses --config auto when metrics are off.
            argv=("scan", "--config", "p/default", "--json", "--quiet", "--metrics", "off",
                  "--disable-version-check", ROOT_PLACEHOLDER),
            parse=adapters.parse_semgrep,
            permissions=Permissions(read_tree=True, network=True, note="downloads the p/default rules from the Semgrep registry; metrics off"),
            ok_exit_codes=frozenset({0, 1}), timeout_s=600, install_hint="pipx install semgrep",
        ),
        ScannerSpec(
            id="gitleaks", binary="gitleaks", license="MIT", homepage="https://github.com/gitleaks/gitleaks",
            families=("secrets",),
            argv=("detect", "--source", ROOT_PLACEHOLDER, "--no-git", "--no-banner", "--redact", "--exit-code", "0",
                  "--report-format", "json", "--report-path", REPORT_PLACEHOLDER),
            parse=adapters.parse_gitleaks,
            permissions=Permissions(read_tree=True, writes_report=True, note="redacted report in a private temp file, deleted after parsing"),
            install_hint="https://github.com/gitleaks/gitleaks/releases",
        ),
        ScannerSpec(
            id="trivy", binary="trivy", license="Apache-2.0", homepage="https://trivy.dev",
            families=("supply_chain", "infrastructure", "secrets"),
            argv=("fs", "--format", "json", "--quiet", "--scanners", "vuln,misconfig,secret", ROOT_PLACEHOLDER),
            parse=adapters.parse_trivy, permissions=_DB, timeout_s=600,
            install_hint="https://trivy.dev/latest/getting-started/installation/",
        ),
        ScannerSpec(
            id="osv_scanner", binary="osv-scanner", license="Apache-2.0", homepage="https://google.github.io/osv-scanner/",
            families=("supply_chain",),
            argv=("--format", "json", "-r", ROOT_PLACEHOLDER),
            parse=adapters.parse_osv_scanner, permissions=_DB,
            # 1 = vulnerabilities found; 128 = no lockfile or manifest to scan.
            ok_exit_codes=frozenset({0, 1}), not_applicable_exit_codes=frozenset({128}),
            install_hint="https://google.github.io/osv-scanner/installation/",
        ),
        ScannerSpec(
            id="bandit", binary="bandit", license="Apache-2.0", homepage="https://bandit.readthedocs.io",
            families=("injection", "code_execution", "cryptography", "secrets"),
            # -x replaces bandit's default exclusions and matches substrings.
            argv=("-r", ROOT_PLACEHOLDER, "-f", "json", "-q",
                  "-x", ".git,.svn,.hg,__pycache__,.tox,.eggs,venv,node_modules,site-packages,tests"),
            parse=adapters.parse_bandit, permissions=_TREE,
            requires=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"),
            ok_exit_codes=frozenset({0, 1}), install_hint="pipx install bandit",
        ),
        ScannerSpec(
            id="pip_audit", binary="pip-audit", license="Apache-2.0", homepage="https://github.com/pypa/pip-audit",
            families=("supply_chain",),
            argv=(), argv_for=_pip_audit_argv,
            parse=adapters.parse_pip_audit,
            permissions=Permissions(read_tree=True, network=True, note="resolves the dependencies in a temporary venv and queries PyPI / OSV"),
            requires=("requirements.txt", "pyproject.toml"),
            ok_exit_codes=frozenset({0, 1}), timeout_s=600, install_hint="pipx install pip-audit",
        ),
        ScannerSpec(
            id="npm_audit", binary="npm", license="Artistic-2.0", homepage="https://docs.npmjs.com/cli/commands/npm-audit",
            families=("supply_chain",),
            argv=("audit", "--json"),
            parse=adapters.parse_npm_audit,
            permissions=Permissions(read_tree=True, network=True, note="sends the lockfile dependency list to the npm registry"),
            requires=("package-lock.json",), ok_exit_codes=frozenset({0, 1}), install_hint="ships with Node.js",
        ),
        ScannerSpec(
            id="cargo_audit", binary="cargo-audit", license="Apache-2.0 OR MIT", homepage="https://rustsec.org",
            families=("supply_chain",),
            # A cargo subcommand binary expects its own name as first argument.
            argv=("audit", "--json"), version_args=("audit", "--version"),
            parse=adapters.parse_cargo_audit, permissions=_DB,
            requires=("Cargo.lock",), ok_exit_codes=frozenset({0, 1}), install_hint="cargo install cargo-audit --locked",
        ),
    )
}


def get_scanner(scanner_id: str) -> ScannerSpec:
    try:
        return REGISTRY[scanner_id]
    except KeyError:
        raise KeyError(f"unknown scanner {scanner_id!r}; known: {sorted(REGISTRY)}") from None


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({"NO_COLOR": "1", "CI": "1", "SEMGREP_SEND_METRICS": "off", "SEMGREP_ENABLE_VERSION_CHECK": "0"})
    return env


def link_controls(scanner_id: str, findings: list[Finding]) -> list[Finding]:
    """Attach the catalog controls this scanner covers for each finding's family."""
    covered = catalog.controls_for_scanner(scanner_id)
    for item in findings:
        for ctl in covered:
            if item.family in ctl.families and ctl.id not in item.controls:
                item.controls.append(ctl.id)
    return findings


def _strip_root(value: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if value == prefix.rstrip("/\\"):
            return "."
        if value.startswith(prefix):
            return value[len(prefix):]
    return value[2:] if value.startswith(("./", ".\\")) else value


def relative_paths(findings: list[Finding], root: Path) -> list[Finding]:
    """Report paths relative to the scanned root, so two tools agree on a file."""
    base = str(root)
    prefixes = tuple(dict.fromkeys([base.rstrip("/\\") + os.sep, base.rstrip("/\\") + "/"]))
    for item in findings:
        if item.file_path:
            item.file_path = _strip_root(item.file_path, prefixes).replace("\\", "/")
        for evidence in item.evidence:
            if evidence.location:
                evidence.location = _strip_root(evidence.location, prefixes).replace("\\", "/")
    return findings


def detect_scanner(spec: ScannerSpec) -> ScannerStatus:
    """Is the binary on PATH, and which version. Blocking: call it off the loop."""
    path = shutil.which(spec.binary)
    if not path:
        return ScannerStatus(id=spec.id, available=False, error="not_installed")
    try:
        completed = subprocess.run(
            [path, *spec.version_args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_VERSION_TIMEOUT_S, stdin=subprocess.DEVNULL, env=_env(), **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return ScannerStatus(id=spec.id, available=True, path=path, error="version_timeout")
    except OSError as exc:
        return ScannerStatus(id=spec.id, available=False, path=path, error=str(exc)[:200])
    match = _VERSION_RE.search(f"{completed.stdout}\n{completed.stderr}")
    return ScannerStatus(id=spec.id, available=True, path=path, version=match.group(1) if match else "")


def detect_scanners(ids: list[str] | None = None) -> dict[str, ScannerStatus]:
    """Installed state and version of every (or the named) registered scanner."""
    return {sid: detect_scanner(get_scanner(sid)) for sid in (ids or list(REGISTRY))}


async def detect_scanners_async(ids: list[str] | None = None) -> dict[str, ScannerStatus]:
    return await asyncio.to_thread(detect_scanners, ids)


def available_scanner_ids(statuses: dict[str, ScannerStatus]) -> set[str]:
    """The ids a plan may use (input of ``plan_audit(available_scanners=...)``)."""
    return {sid for sid, status in statuses.items() if status.available}


def _failed(spec: ScannerSpec, status: str, detail: str, **extra: Any) -> ScannerRun:
    return ScannerRun(id=spec.id, ok=False, status=status, detail=detail[:500], **extra)


def run_scanner(scanner_id: str, root: str | Path, *, timeout_s: int | None = None) -> ScannerRun:
    """Run one scanner on ``root`` and normalize its report. Blocking: call it off the loop."""
    spec = get_scanner(scanner_id)
    root = Path(root).resolve()
    if not spec.applicable(root):
        return _failed(spec, "not_applicable", f"needs one of {', '.join(spec.requires)} in {root}")
    path = shutil.which(spec.binary)
    if not path:
        return _failed(spec, "not_installed", f"{spec.binary} is not on PATH; install: {spec.install_hint}")
    limit = timeout_s or spec.timeout_s
    report: Path | None = None
    if spec.uses_report_file(root):
        handle, name = tempfile.mkstemp(prefix=f"navin-{spec.id}-", suffix=".json")
        os.close(handle)
        report = Path(name)
    argv = spec.command(path, root, report)
    started = time.monotonic()
    try:
        try:
            completed = subprocess.run(
                argv, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=limit, stdin=subprocess.DEVNULL, env=_env(), **no_window_kwargs(),
            )
        except subprocess.TimeoutExpired:
            return _failed(spec, "timeout", f"stopped after {limit} s", duration_s=round(time.monotonic() - started, 2), command=argv)
        except OSError as exc:
            return _failed(spec, "failed", str(exc), command=argv)
        timing = {"exit_code": completed.returncode, "duration_s": round(time.monotonic() - started, 2), "command": argv}
        stderr_tail = (completed.stderr or "").strip()[-500:]
        if completed.returncode in spec.not_applicable_exit_codes:
            return _failed(spec, "not_applicable", stderr_tail or "nothing this scanner can read in the tree", **timing)
        if completed.returncode not in spec.ok_exit_codes:
            return _failed(spec, "failed", stderr_tail or f"exit code {completed.returncode}", **timing)
        raw = report.read_text(encoding="utf-8", errors="replace") if report is not None else completed.stdout
        raw = (raw or "").strip()
        if not raw:
            return _failed(spec, "failed", stderr_tail or "the scanner wrote no report", **timing)
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            return _failed(spec, "invalid_output", f"not JSON: {raw[:200]}", **timing)
        try:
            findings = spec.parse(doc)
        except adapters.ReportError as exc:
            return _failed(spec, "failed", str(exc), **timing)
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            return _failed(spec, "invalid_output", f"unexpected report shape: {exc}", **timing)
        findings = link_controls(spec.id, relative_paths(findings, root))
        return ScannerRun(id=spec.id, ok=True, status="ok", findings=findings, **timing)
    finally:
        if report is not None:
            report.unlink(missing_ok=True)


async def run_scanner_async(scanner_id: str, root: str | Path, *, timeout_s: int | None = None) -> ScannerRun:
    """Same as :func:`run_scanner`, off the event loop."""
    return await asyncio.to_thread(run_scanner, scanner_id, root, timeout_s=timeout_s)
