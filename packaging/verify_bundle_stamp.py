#!/usr/bin/env python3
"""Prove that a packaged target carries the exact WebUI bundle CI built.

The desktop apps do not embed the WebUI in the Tauri binary; they serve it
from the gateway bundled inside the sidecar. So the interface a user gets is
whatever ``navin/web/dist`` ended up inside that sidecar, and nothing used to
check it: a build machine with its own dependency resolution, a stale tree or
a half-copied bundle all shipped silently.

``webui/scripts/stamp-bundle.mjs`` writes ``build-info.json`` next to the
bundle, holding a sha256 over every other file in it. This script recomputes
that digest from the files actually present and compares:

  * the digest against the stamp (catches a bundle modified or truncated
    after stamping, and a stale bundle carried next to a fresh stamp);
  * the stamp against ``--expect`` (catches a target that shipped a bundle
    other than the one the release built, linted and tested once).

Usage::

    python packaging/verify_bundle_stamp.py <path> [--expect SHA256] [--quiet]

``<path>`` is either the bundle directory itself or any tree containing one,
such as an unpacked sidecar (``os/linux/bin/x64/navin-dist``), a mounted
AppImage or a macOS ``.app``. Exits non-zero on any mismatch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

STAMP_NAME = "build-info.json"


class StampError(RuntimeError):
    """A bundle that cannot be proven identical to the one CI built."""


def find_bundle(root: Path) -> Path:
    """The bundle directory at or below ``root``.

    Frozen trees nest it (``navin-dist/_internal/navin/web/dist``), and the
    depth differs per platform and PyInstaller mode, so it is searched for
    rather than assumed.
    """
    if (root / STAMP_NAME).is_file():
        return root
    # Nesting depth differs per platform and PyInstaller mode, and the search
    # may start above or below the ``navin`` package, so the stamp is looked
    # for anywhere and confirmed by the index.html sitting next to it.
    candidates = sorted(
        path for path in root.rglob(STAMP_NAME) if (path.parent / "index.html").is_file()
    )
    if not candidates:
        # A bundle with no stamp at all is the failure this script exists to
        # catch, so say which of the two cases it is.
        indexes = sorted(root.rglob("web/dist/index.html"))
        if indexes:
            raise StampError(
                f"{indexes[0].parent} holds a bundle with no {STAMP_NAME}: it was built "
                "by something other than `npm run build` (or by an older checkout)"
            )
        raise StampError(f"no WebUI bundle found under {root}")
    if len({path.parent for path in candidates}) > 1:
        joined = ", ".join(str(path.parent) for path in candidates)
        raise StampError(f"several bundles under {root}: {joined}")
    return candidates[0].parent


def bundle_files(bundle: Path) -> list[str]:
    """Bundle-relative POSIX paths of every file except the stamp, sorted."""
    found = [
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    ]
    return sorted(name for name in found if name != STAMP_NAME)


def bundle_digest(bundle: Path, files: list[str]) -> str:
    """Recompute what ``stamp-bundle.mjs`` wrote, byte for byte."""
    digest = hashlib.sha256()
    for name in files:
        file_digest = hashlib.sha256((bundle / name).read_bytes()).hexdigest()
        digest.update(f"{name}\0{file_digest}\n".encode())
    return digest.hexdigest()


def read_stamp(bundle: Path) -> dict:
    try:
        stamp = json.loads((bundle / STAMP_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StampError(f"{bundle / STAMP_NAME} is unreadable: {exc}") from exc
    if not isinstance(stamp, dict) or not stamp.get("bundle"):
        raise StampError(f"{bundle / STAMP_NAME} has no bundle digest")
    return stamp


def verify(root: Path, *, expect: str | None = None) -> dict:
    """Return the stamp of the bundle under ``root``, or raise ``StampError``."""
    bundle = find_bundle(root)
    stamp = read_stamp(bundle)
    files = bundle_files(bundle)
    recorded_count = stamp.get("files")
    if isinstance(recorded_count, int) and recorded_count != len(files):
        raise StampError(
            f"{bundle} holds {len(files)} files, the stamp recorded "
            f"{recorded_count}: the bundle was modified after it was built"
        )
    actual = bundle_digest(bundle, files)
    if actual != stamp["bundle"]:
        raise StampError(
            f"{bundle} does not match its own stamp\n"
            f"  stamped: {stamp['bundle']}\n"
            f"  actual:  {actual}"
        )
    if expect and expect != stamp["bundle"]:
        raise StampError(
            f"{bundle} is not the bundle this release built\n"
            f"  expected: {expect}\n"
            f"  shipped:  {stamp['bundle']}"
        )
    stamp["path"] = str(bundle)
    return stamp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path, help="bundle directory, or a tree holding one")
    parser.add_argument(
        "--expect",
        default=None,
        help="sha256 the bundle must carry (the digest the release built)",
    )
    parser.add_argument("--quiet", action="store_true", help="print only on failure")
    args = parser.parse_args(argv)

    try:
        stamp = verify(args.path, expect=args.expect)
    except StampError as exc:
        print(f"bundle stamp: FAILED\n{exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(
            "bundle stamp: ok "
            f"({stamp['files']} files, bundle {str(stamp['bundle'])[:16]}, "
            f"version {stamp.get('version') or 'unknown'}, "
            f"commit {str(stamp.get('commit') or 'unknown')[:12]})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
