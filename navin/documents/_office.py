# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""LibreOffice plumbing shared by the document tools.

LibreOffice is the one program that lays out a DOCX, a PPTX or an XLSX the way
Word, PowerPoint and Excel will: through it a finished file can be turned into
a PDF (a deliverable in itself) and then into page images the agent reads back
before handing the document over. It is optional: nothing here is required to
*build* a document, only to see the real result, and every caller degrades to
another route when the binary is missing.

The binary is driven from the command line with a private user profile, so two
conversions never fight over the lock file of the user's own installation and
a first-run dialog can never stall a headless run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

_SOFFICE_ENV_VARS = ("NAVIN_SOFFICE", "SOFFICE_PATH", "LIBREOFFICE_PATH")
_SOFFICE_COMMANDS = ("soffice", "libreoffice", "soffice.exe", "soffice.bin")
_WINDOWS_SOFFICE_RELATIVE = (
    r"LibreOffice\program\soffice.exe",
    r"LibreOffice 24\program\soffice.exe",
    r"LibreOffice 7\program\soffice.exe",
)
_WINDOWS_SOFFICE_BASE_VARS = ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")
_MAC_SOFFICE_APPS = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/Applications/LibreOffice Vanilla.app/Contents/MacOS/soffice",
)
_LINUX_SOFFICE_PATHS = (
    "/usr/lib/libreoffice/program/soffice",
    "/usr/lib64/libreoffice/program/soffice",
    "/opt/libreoffice/program/soffice",
    "/snap/bin/libreoffice",
    "/var/lib/flatpak/exports/bin/org.libreoffice.LibreOffice",
)
OFFICE_SUFFIXES = frozenset(
    {".docx", ".doc", ".odt", ".rtf", ".pptx", ".ppt", ".odp", ".xlsx", ".xls", ".ods", ".csv"}
)

DEFAULT_TIMEOUT_S = 180


class OfficeUnavailableError(RuntimeError):
    """Raised when no LibreOffice binary can be found."""


def _no_window_kwargs() -> dict[str, Any]:
    try:
        from navin.utils.proc import no_window_kwargs
    except Exception:
        flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return {"creationflags": flag} if flag else {}
    return no_window_kwargs()


def _windows_candidates() -> list[str]:
    found: list[str] = []
    for var in _WINDOWS_SOFFICE_BASE_VARS:
        base = (os.environ.get(var) or "").rstrip("\\/")
        if base:
            found.extend(f"{base}\\{relative}" for relative in _WINDOWS_SOFFICE_RELATIVE)
    return found


def find_soffice(explicit: str | None = None) -> str | None:
    """Locate the LibreOffice binary, or None when the machine has none."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(os.environ[var] for var in _SOFFICE_ENV_VARS if os.environ.get(var))
    for command in _SOFFICE_COMMANDS:
        found = shutil.which(command)
        if found:
            candidates.append(found)
    if os.name == "nt":
        candidates.extend(_windows_candidates())
    else:
        candidates.extend(_MAC_SOFFICE_APPS)
        candidates.extend(_LINUX_SOFFICE_PATHS)
        with suppress(OSError):
            candidates.extend(
                str(path / "program" / "soffice")
                for path in sorted(Path("/opt").glob("libreoffice*"), reverse=True)
            )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def install_hint() -> str:
    """One sentence telling the operator how to get LibreOffice."""
    if os.name == "nt":
        return "install LibreOffice (winget install TheDocumentFoundation.LibreOffice)"
    if sys.platform == "darwin":
        return "install LibreOffice (brew install --cask libreoffice)"
    return "install LibreOffice (apt install libreoffice-core, or the distribution package)"


def convert(
    source: Path,
    target_format: str,
    outdir: Path,
    *,
    soffice: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> Path:
    """Convert *source* with LibreOffice and return the produced file.

    ``target_format`` is a LibreOffice filter name such as ``pdf``, ``docx``,
    ``pptx`` or ``png``. Raises :class:`OfficeUnavailableError` when no binary exists
    and :class:`RuntimeError` when the conversion produced nothing.
    """
    binary = find_soffice(soffice)
    if binary is None:
        raise OfficeUnavailableError(
            "LibreOffice (soffice) is not installed; " + install_hint() + "."
        )
    source = Path(source)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    profile = Path(tempfile.mkdtemp(prefix="navin-lo-profile-"))
    try:
        command = [
            binary,
            "--headless",
            "--norestore",
            "--nologo",
            "--nolockcheck",
            f"-env:UserInstallation={profile.resolve().as_uri()}",
            "--convert-to",
            target_format,
            "--outdir",
            str(outdir),
            str(source),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                timeout=timeout,
                check=False,
                **_no_window_kwargs(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"LibreOffice timed out after {timeout}s on {source.name}") from exc
        extension = target_format.split(":", 1)[0].lower()
        produced = outdir / f"{source.stem}.{extension}"
        if not produced.is_file():
            # LibreOffice reports failures on stdout as often as on stderr.
            detail = (result.stderr or result.stdout or b"").decode("utf-8", "replace").strip()
            raise RuntimeError(
                f"LibreOffice produced no {extension} for {source.name}"
                + (f": {detail[-400:]}" if detail else "")
            )
        return produced
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def to_pdf(
    source: Path, outdir: Path, *, soffice: str | None = None, timeout: int = DEFAULT_TIMEOUT_S
) -> Path:
    """Render an Office document to PDF with LibreOffice."""
    return convert(source, "pdf", outdir, soffice=soffice, timeout=timeout)
