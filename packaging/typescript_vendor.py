"""Stage the npm typescript package into the build tree.

Navin type-checks TypeScript with ``tsc``, resolved from the project's own
``node_modules`` first and then from PATH. A project that has no local install,
or a machine that never ran ``npm install``, silently loses that check. Staging
the package here gives packaged builds a fallback, so the check degrades to
"the project's own version if it has one" instead of "nothing".

Unlike ffmpeg this needs no per-target staging: 5.x is pure JavaScript, so one
copy serves every platform. It still needs node on the machine, which packaged
builds already require for the rest of the JavaScript tooling.

Usage::

    python packaging/typescript_vendor.py                   # stage it
    python packaging/typescript_vendor.py --check           # exit 1 if missing
    python packaging/typescript_vendor.py --force           # re-download
    python packaging/typescript_vendor.py --update-manifest # re-pin checksum
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import ssl
import stat
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import certifi

PACKAGING_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGING_DIR.parent
MANIFEST_PATH = PACKAGING_DIR / "typescript-manifest.json"
VENDOR_ROOT = PACKAGING_DIR / "vendor" / "typescript"

DOWNLOAD_TIMEOUT_S = 900
_CHUNK = 1 << 20


class VendorError(RuntimeError):
    """Anything that must stop a release build."""


def vendor_path() -> Path:
    """Where the staged package lives, laid out as a node package root."""
    return VENDOR_ROOT


def staged_version() -> str | None:
    """Version of what is staged, or None when nothing is."""
    manifest = vendor_path() / "package.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version") or "")
    except (OSError, ValueError):
        return None


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        raise VendorError(f"missing manifest: {MANIFEST_PATH}")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _download(url: str, dest: Path) -> str:
    """Fetch *url* to *dest*, returning the sha256 of what arrived."""
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "navin-build"})
    ssl_context = ssl.create_default_context(cafile=certifi.where())

    with urllib.request.urlopen(
        request,
        timeout=DOWNLOAD_TIMEOUT_S,
        context=ssl_context,
    ) as response:  # noqa: S310
        with dest.open("wb") as handle:
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
    return digest.hexdigest()


def _extract(archive: Path, dest: Path) -> None:
    """Unpack the npm tarball, dropping its ``package/`` prefix.

    Entries are checked rather than trusted: an npm tarball is third-party
    content, and a member named ``../`` would otherwise write outside the
    vendor tree.
    """
    staging = dest.parent / f".{dest.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            for member in bundle.getmembers():
                if not member.isfile():
                    continue
                parts = Path(member.name).parts
                if not parts or parts[0] != "package":
                    raise VendorError(f"unexpected entry in tarball: {member.name}")
                relative = Path(*parts[1:])
                if relative.is_absolute() or ".." in relative.parts:
                    raise VendorError(f"unsafe path in tarball: {member.name}")
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted = bundle.extractfile(member)
                if extracted is None:
                    raise VendorError(f"cannot read {member.name}")
                with extracted, target.open("wb") as out:
                    shutil.copyfileobj(extracted, out)
        for launcher in (staging / "bin").glob("*"):
            _make_executable(launcher)
        shutil.rmtree(dest, ignore_errors=True)
        staging.replace(dest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _make_executable(path: Path) -> None:
    if sys.platform == "win32":
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def ensure_typescript(
    *,
    manifest: dict | None = None,
    force: bool = False,
    update_manifest: bool = False,
) -> Path:
    """Stage the typescript package, returning its root directory."""
    manifest = manifest if manifest is not None else load_manifest()
    wanted = str(manifest.get("version") or "")
    dest = vendor_path()
    if not force and staged_version() == wanted:
        return dest

    url = str(manifest.get("url") or "")
    expected = str(manifest.get("sha256") or "")
    if not url:
        raise VendorError("manifest has no url")

    print(f"[typescript] downloading {url}")
    with tempfile.TemporaryDirectory(prefix="navin-typescript-") as tmp:
        archive = Path(tmp) / "typescript.tgz"
        actual = _download(url, archive)
        if update_manifest:
            manifest["sha256"] = actual
        elif not expected:
            raise VendorError(
                f"manifest has no sha256; run with --update-manifest after reviewing {url}"
            )
        elif actual != expected:
            raise VendorError(
                f"checksum mismatch for {url}\n"
                f"  expected {expected}\n"
                f"  actual   {actual}\n"
                "An npm version is immutable, so this means the registry served "
                "something else. Review it before re-pinning."
            )
        _extract(archive, dest)

    found = staged_version()
    if found != wanted:
        raise VendorError(f"staged typescript reports {found!r}, manifest wants {wanted!r}")
    size_mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / (1024 * 1024)
    print(f"[typescript] staged {dest} ({found}, {size_mb:.0f} MB)")
    return dest


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--force", action="store_true", help="re-download even if staged")
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report whether it is staged; exit 1 when missing",
    )
    parser.add_argument(
        "--update-manifest",
        action="store_true",
        help="record the downloaded checksum instead of verifying it",
    )
    args = parser.parse_args(argv)

    if args.check:
        found = staged_version()
        wanted = str(load_manifest().get("version") or "")
        state = "ok" if found == wanted else f"MISSING (found {found!r})"
        print(f"[typescript] {wanted}: {state} ({vendor_path()})")
        return 0 if found == wanted else 1

    manifest = load_manifest()
    try:
        ensure_typescript(
            manifest=manifest,
            force=args.force,
            update_manifest=args.update_manifest,
        )
    except VendorError as exc:
        print(f"[typescript] error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[typescript] download failed: {exc}", file=sys.stderr)
        return 1

    if args.update_manifest:
        _save_manifest(manifest)
        print(f"[typescript] manifest updated: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

