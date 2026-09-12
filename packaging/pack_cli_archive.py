# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""Publishable CLI archives from the same versioned engine as the desktop app."""

from __future__ import annotations

import argparse
import hashlib
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path


def pack_cli_archive(tree: Path, output: Path, version: str) -> Path:
    if tree.name != "navin-dist" or not any((tree / name).is_file() for name in ("navin", "navin.exe")):
        raise ValueError(f"No navin-dist engine at {tree}")
    stamp = tree / "VERSION"
    if not stamp.is_file() or stamp.read_text(encoding="utf-8-sig").strip() != version:
        raise ValueError(f"The engine at {tree} is stale or unstamped; rebuild version {version}")
    if not (output.name.endswith(".tar.gz") or output.suffix == ".zip"):
        raise ValueError("CLI archives must use .tar.gz or .zip")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".partial", dir=output.parent)
    os.close(fd)
    partial = Path(raw)
    try:
        if output.suffix == ".zip":
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                for member in sorted(tree.rglob("*")):
                    archive.write(member, member.relative_to(tree.parent).as_posix())
        else:
            with tarfile.open(partial, "w:gz", compresslevel=6) as archive:
                archive.add(tree, arcname="navin-dist")
        with partial.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        os.replace(partial, output)
        output.with_name(f"{output.name}.sha256").write_text(
            f"{digest}  {output.name}\n", encoding="ascii",
        )
    finally:
        partial.unlink(missing_ok=True)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        print(pack_cli_archive(args.tree, args.output, args.version))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Could not package the CLI: {exc}\n")


if __name__ == "__main__":
    main()
