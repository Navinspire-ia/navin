"""Stage static ffmpeg and ffprobe binaries into the build tree, per target.

Navin used to leave ffmpeg entirely to the user's machine: resolved from PATH,
otherwise downloaded on demand into ``~/.navin/montage/bin``. That made four
features conditional on a manual install - video assembly, social exports,
browser-recording transcode, and reading a video attached to the chat - and it
never worked at all on Apple Silicon, which had no download URL.

This module downloads the same builds the runtime installer uses, so a packaged
Navin ships one. ffprobe rides along for precise stream probing: the Linux and
Windows archives already contain it, and the macOS providers publish it as a
separate archive, declared as an ``ffprobe`` sub-entry in the manifest. The
binaries are GPL; see THIRD_PARTY_NOTICES.md for the attribution and the
written offer for corresponding source.

Usage::

    python packaging/ffmpeg_vendor.py                  # current host target
    python packaging/ffmpeg_vendor.py --target macos-arm64
    python packaging/ffmpeg_vendor.py --all
    python packaging/ffmpeg_vendor.py --check          # exit 1 if missing
    python packaging/ffmpeg_vendor.py --update-manifest # re-pin checksums

Downloads are checksum-pinned. Upstream publishes rolling "release" URLs, so a
mismatch means the remote moved: inspect the change, then re-pin deliberately
with ``--update-manifest`` rather than trusting whatever arrived.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import ssl
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import certifi

PACKAGING_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGING_DIR.parent
MANIFEST_PATH = PACKAGING_DIR / "ffmpeg-manifest.json"
VENDOR_ROOT = PACKAGING_DIR / "vendor" / "ffmpeg"

DOWNLOAD_TIMEOUT_S = 900
_CHUNK = 1 << 20

TARGETS = (
    "linux-x64",
    "linux-arm64",
    "windows-x64",
    "windows-arm64",
    "macos-x64",
    "macos-arm64",
)


class VendorError(RuntimeError):
    """Anything that must stop a release build."""


def current_target() -> str:
    """Build target for the host running this script."""
    machine = platform.machine().lower()
    arm = machine in {"arm64", "aarch64"}
    if sys.platform == "win32":
        return "windows-arm64" if arm else "windows-x64"
    if sys.platform == "darwin":
        return "macos-arm64" if arm else "macos-x64"
    return "linux-arm64" if arm else "linux-x64"


def binary_name(target: str, program: str = "ffmpeg") -> str:
    return f"{program}.exe" if target.startswith("windows") else program


def vendor_path(target: str, program: str = "ffmpeg") -> Path:
    """Where the staged binary for *target* lives."""
    return VENDOR_ROOT / target / binary_name(target, program)


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        raise VendorError(f"missing manifest: {MANIFEST_PATH}")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _entry(manifest: dict, target: str) -> dict:
    entry = manifest.get("targets", {}).get(target)
    if not entry:
        raise VendorError(
            f"no ffmpeg source for target {target!r}; known: {', '.join(sorted(TARGETS))}"
        )
    return entry


def _download(url: str, dest: Path) -> str:
    """Fetch *url* to *dest*, returning the sha256 of what arrived."""
    digest = hashlib.sha256()
    request = urllib.request.Request(url, headers={"User-Agent": "navin-build"})
    # Build machines without a system trust store (macOS runners especially)
    # fail verification against the stock context, so pin certifi's bundle.
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(
        request,
        timeout=DOWNLOAD_TIMEOUT_S,
        context=context,
    ) as response:
        with dest.open("wb") as handle:
            while True:
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                digest.update(chunk)
    return digest.hexdigest()


def _is_ffmpeg_member(name: str, target: str, program: str = "ffmpeg") -> bool:
    """True for the wanted program itself, ignoring macOS resource forks."""
    parts = Path(name).parts
    if any(part == "__MACOSX" for part in parts):
        return False
    leaf = parts[-1] if parts else ""
    if leaf.startswith("._"):
        return False
    return leaf == binary_name(target, program)


def _extract(
    archive: Path, kind: str, target: str, dest: Path, program: str = "ffmpeg"
) -> None:
    """Pull one program out of *archive* into *dest*.

    Archives nest the binary differently (``ffmpeg-*-static/ffmpeg``,
    ``ffmpeg-*/bin/ffmpeg.exe``, or the bare program), so the shallowest match
    wins rather than hardcoding a layout per source.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if kind == "zip":
        with zipfile.ZipFile(archive) as bundle:
            names = [
                n for n in bundle.namelist() if _is_ffmpeg_member(n, target, program)
            ]
            if not names:
                raise VendorError(
                    f"no {binary_name(target, program)} inside {archive.name}"
                )
            chosen = min(names, key=lambda n: len(Path(n).parts))
            with bundle.open(chosen) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)
        return
    if kind in {"tar.xz", "tar.gz", "tar"}:
        mode = {"tar.xz": "r:xz", "tar.gz": "r:gz", "tar": "r:"}[kind]
        with tarfile.open(archive, mode) as bundle:
            members = [
                m
                for m in bundle.getmembers()
                if m.isfile() and _is_ffmpeg_member(m.name, target, program)
            ]
            if not members:
                raise VendorError(
                    f"no {binary_name(target, program)} inside {archive.name}"
                )
            chosen = min(members, key=lambda m: len(Path(m.name).parts))
            extracted = bundle.extractfile(chosen)
            if extracted is None:
                raise VendorError(f"cannot read {chosen.name} from {archive.name}")
            with extracted, dest.open("wb") as out:
                shutil.copyfileobj(extracted, out)
        return
    raise VendorError(f"unsupported archive kind: {kind!r}")


def _make_executable(path: Path) -> None:
    if sys.platform == "win32":
        return
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _staged(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _download_verified(
    source: dict, target: str, tmp: Path, *, update_manifest: bool
) -> Path:
    """Fetch ``source['url']`` into *tmp*, enforcing the pinned sha256."""
    url = source["url"]
    expected = source.get("sha256") or ""
    archive = tmp / "archive"
    print(f"[ffmpeg] {target}: downloading {url}")
    actual = _download(url, archive)
    if update_manifest:
        source["sha256"] = actual
    elif not expected:
        raise VendorError(
            f"{target}: manifest has no sha256; run with --update-manifest "
            f"after reviewing {url}"
        )
    elif actual != expected:
        raise VendorError(
            f"{target}: checksum mismatch for {url}\n"
            f"  expected {expected}\n"
            f"  actual   {actual}\n"
            "Upstream publishes rolling URLs. Review the new build, then "
            "re-pin with --update-manifest."
        )
    return archive


def _finish(target: str, dest: Path) -> None:
    _make_executable(dest)
    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"[ffmpeg] {target}: staged {dest} ({size_mb:.0f} MB)")


def ensure_ffmpeg(
    target: str,
    *,
    manifest: dict | None = None,
    force: bool = False,
    update_manifest: bool = False,
) -> Path:
    """Stage ffmpeg and ffprobe for *target*, returning the ffmpeg path.

    ffprobe gives frame-precise stream probing (durations for trims, crossfade
    offsets, video attachments). The Linux and Windows archives already carry
    it; the macOS providers publish it as a separate download, declared as an
    ``ffprobe`` sub-entry in the manifest. Both are release-gating: a bundle
    that silently lost one would only fail on a user's machine.
    """
    manifest = manifest if manifest is not None else load_manifest()
    entry = _entry(manifest, target)
    ffmpeg_dest = vendor_path(target, "ffmpeg")
    ffprobe_dest = vendor_path(target, "ffprobe")
    separate = entry.get("ffprobe")

    need_ffmpeg = force or not _staged(ffmpeg_dest)
    need_ffprobe = force or not _staged(ffprobe_dest)
    if not (need_ffmpeg or need_ffprobe):
        return ffmpeg_dest

    if need_ffmpeg or (need_ffprobe and separate is None):
        kind = entry.get("archive", "tar.xz")
        with tempfile.TemporaryDirectory(prefix="navin-ffmpeg-") as tmp:
            archive = _download_verified(
                entry, target, Path(tmp), update_manifest=update_manifest
            )
            if need_ffmpeg:
                _extract(archive, kind, target, ffmpeg_dest, "ffmpeg")
                _finish(target, ffmpeg_dest)
            if need_ffprobe and separate is None:
                _extract(archive, kind, target, ffprobe_dest, "ffprobe")
                _finish(target, ffprobe_dest)

    if need_ffprobe and separate is not None:
        with tempfile.TemporaryDirectory(prefix="navin-ffprobe-") as tmp:
            archive = _download_verified(
                separate, target, Path(tmp), update_manifest=update_manifest
            )
            _extract(
                archive, separate.get("archive", "zip"), target, ffprobe_dest, "ffprobe"
            )
            _finish(target, ffprobe_dest)

    return ffmpeg_dest


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--target", default=None, help="build target (default: host)")
    parser.add_argument("--all", action="store_true", help="stage every known target")
    parser.add_argument("--force", action="store_true", help="re-download even if staged")
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report whether the target is staged; exit 1 when missing",
    )
    parser.add_argument(
        "--update-manifest",
        action="store_true",
        help="record the downloaded checksums instead of verifying them",
    )
    args = parser.parse_args(argv)

    targets = list(TARGETS) if args.all else [args.target or current_target()]

    if args.check:
        missing = []
        for target in targets:
            for program in ("ffmpeg", "ffprobe"):
                path = vendor_path(target, program)
                state = "ok" if path.is_file() else "MISSING"
                if not path.is_file():
                    missing.append(target)
                print(f"[ffmpeg] {target}: {program} {state} ({path})")
        return 1 if missing else 0

    manifest = load_manifest()
    try:
        for target in targets:
            ensure_ffmpeg(
                target,
                manifest=manifest,
                force=args.force,
                update_manifest=args.update_manifest,
            )
    except VendorError as exc:
        print(f"[ffmpeg] error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"[ffmpeg] download failed: {exc}", file=sys.stderr)
        return 1

    if args.update_manifest:
        _save_manifest(manifest)
        print(f"[ffmpeg] manifest updated: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
