#!/usr/bin/env python3
# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only
"""Headless LibreOffice conversion wrapper shared by the docx, pptx and xlsx skills.

Usage:
    office_convert.py <input> --to pdf|docx|pptx|xlsx|html [--out-dir DIR] [--timeout SEC] [--json]

Prints the produced file path (or a JSON object with --json).

Exit codes: 0 converted, 1 conversion failed, 3 LibreOffice (soffice) not installed,
4 timeout, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TARGETS = {
    "pdf": "pdf",
    "docx": "docx",
    "pptx": "pptx",
    "xlsx": "xlsx",
    "html": "html",
}

SOFFICE_CANDIDATES = ("soffice", "libreoffice", "soffice.bin")


class SofficeMissingError(RuntimeError):
    """Raised when no LibreOffice binary is available."""


def find_soffice() -> str | None:
    """Return the path of a LibreOffice binary, or None."""
    for name in SOFFICE_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        "/usr/lib/libreoffice/program/soffice",
        "/opt/libreoffice/program/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _no_window() -> dict:
    """No console window for soffice on Windows, through navin's helper when navin is importable."""
    try:
        from navin.utils.proc import no_window_kwargs

        return dict(no_window_kwargs())
    except ImportError:
        return {"creationflags": 0x08000000} if os.name == "nt" else {}


def convert(source: Path, to: str, out_dir: Path | None = None, timeout: int = 120) -> Path:
    """Convert source to the target format. Returns the output path.

    Raises SofficeMissingError, subprocess.TimeoutExpired or RuntimeError.
    """
    if to not in TARGETS:
        raise ValueError(f"unsupported target {to!r}; choose one of {', '.join(TARGETS)}")
    if not source.is_file():
        raise FileNotFoundError(source)
    binary = find_soffice()
    if not binary:
        raise SofficeMissingError(
            "LibreOffice (soffice) is not installed or not on PATH; install it or use a library fallback"
        )
    out_dir = (out_dir or source.parent).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # A private profile directory avoids lock files from other sessions.
    with tempfile.TemporaryDirectory(prefix="navin-soffice-") as profile:
        cmd = [
            binary,
            "--headless",
            "--norestore",
            "--nologo",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to",
            TARGETS[to],
            "--outdir",
            str(out_dir),
            str(source.resolve()),
        ]
        env = dict(os.environ)
        env.setdefault("HOME", profile)
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False, **_no_window())
    expected = out_dir / f"{source.stem}.{to}"
    if proc.returncode != 0 or not expected.is_file():
        detail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise RuntimeError(f"soffice failed (exit {proc.returncode}): {detail or 'no output produced'}")
    return expected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="office_convert.py",
        description="Convert office documents with headless LibreOffice.",
    )
    parser.add_argument("source", type=Path, help="input file (.docx, .pptx, .xlsx, .odt, .html ...)")
    parser.add_argument("--to", required=True, choices=sorted(TARGETS), help="target format")
    parser.add_argument("--out-dir", type=Path, default=None, help="output folder (default: next to input)")
    parser.add_argument("--timeout", type=int, default=120, help="seconds before giving up")
    parser.add_argument("--json", action="store_true", help="print a JSON result")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    def emit(payload: dict, code: int) -> int:
        if args.json:
            print(json.dumps(payload))
        elif payload.get("ok"):
            print(payload["output"])
        else:
            print(payload.get("error", "conversion failed"), file=sys.stderr)
        return code

    try:
        output = convert(args.source, args.to, args.out_dir, args.timeout)
    except SofficeMissingError as exc:
        return emit({"ok": False, "error": str(exc), "soffice": None}, 3)
    except subprocess.TimeoutExpired:
        return emit({"ok": False, "error": f"soffice timed out after {args.timeout}s"}, 4)
    except (FileNotFoundError, ValueError) as exc:
        return emit({"ok": False, "error": str(exc)}, 2)
    except RuntimeError as exc:
        return emit({"ok": False, "error": str(exc)}, 1)
    return emit({"ok": True, "output": str(output), "soffice": find_soffice()}, 0)


if __name__ == "__main__":
    sys.exit(main())
