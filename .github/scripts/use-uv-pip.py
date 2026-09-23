#!/usr/bin/env python3
"""Point cloned packaging scripts at `uv pip install`.

The OS jobs fetch the closed-source tree and then run its build scripts.
Those scripts install with `python -m pip`. This rewrites that checkout
in place so the job uses uv, and fails if a pip install line was added
or removed.
"""

from __future__ import annotations

import sys
from pathlib import Path


def rewrite(text: str, replacements: list[tuple[str, str, int]], label: str) -> str:
    for old, new, expected in replacements:
        found = text.count(old)
        if found != expected:
            raise SystemExit(f"{label}: expected {expected} of {old!r}, found {found}")
        text = text.replace(old, new)
    if "-m pip install" in text:
        raise SystemExit(f"{label}: a pip install line is still present")
    return text


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: use-uv-pip.py <claw-checkout>")
    root = Path(sys.argv[1]).resolve()
    shell_prefix = '"$venv_python" -m pip install '
    windows_prefix = "& $BuildPython -m pip install "
    files = {
        "packaging/linux/build-offline.sh": [
            (
                shell_prefix + "--ignore-installed ",
                'uv pip install --python "$venv_python" --reinstall ',
                1,
            ),
            (shell_prefix, 'uv pip install --python "$venv_python" ', 3),
        ],
        "packaging/macos/build-offline.sh": [
            (shell_prefix + "--ignore-installed ", shell_prefix + "--ignore-installed ", 0),
            (shell_prefix, 'uv pip install --python "$venv_python" ', 2),
        ],
        "packaging/windows/build-offline.ps1": [
            (
                windows_prefix + "--ignore-installed ",
                'uv pip install --python "$BuildPython" --reinstall ',
                1,
            ),
            (windows_prefix, 'uv pip install --python "$BuildPython" ', 3),
        ],
    }
    for rel, replacements in files.items():
        path = root / rel
        if not path.is_file():
            raise SystemExit(f"missing {path}")
        path.write_text(rewrite(path.read_text(), replacements, rel))
        print(f"uv pip: {rel}")


if __name__ == "__main__":
    main()
