#!/usr/bin/env python3
"""Write the static ``latest.json`` read by the Tauri v2 updater plugin.

Format (https://v2.tauri.app/plugin/updater/#static-json-file)::

    {
      "version": "2.1.0",
      "notes": "...",
      "pub_date": "2026-01-31T12:00:00Z",
      "platforms": {
        "windows-x86_64": {"signature": "<content of the .sig>", "url": "https://.../setup.exe"},
        "darwin-aarch64": {...}, "darwin-x86_64": {...}, "linux-x86_64": {...}
      }
    }

Every artifact must come with the ``.sig`` Tauri wrote next to it (bundle
setting ``createUpdaterArtifacts``): an entry without a signature is refused,
never published, because the plugin would reject it on every client anyway
and a missing signature usually means the build was not the signed one.

With ``--public-key`` (the ``plugins.updater.pubkey`` value of tauri.conf.json)
each signature is also checked against its file before anything is written.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urljoin

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Tauri target keys are "{os}-{arch}" with os in linux/darwin/windows.
PLATFORM_RE = re.compile(r"^(linux|darwin|windows)-(x86_64|aarch64|i686|armv7)$")
# What the v2 updater installs for each OS (createUpdaterArtifacts: true).
SUFFIXES = {
    "windows": (".exe", ".msi"),
    "darwin": (".app.tar.gz",),
    "linux": (".AppImage",),
}


def _rfc3339_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _check_pub_date(value: str) -> str:
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"--pub-date must be RFC 3339, got {value!r}") from exc
    if parsed.tzinfo is None:
        raise SystemExit(f"--pub-date must carry a timezone (RFC 3339), got {value!r}")
    return value


def _minisign_lines(raw: str, what: str) -> list[str]:
    try:
        text = base64.b64decode(raw.strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise SystemExit(f"{what}: not a base64-encoded minisign file") from exc
    return [line.strip("\r") for line in text.splitlines()]


def _public_key(raw: str) -> tuple[bytes, Ed25519PublicKey]:
    lines = _minisign_lines(raw, "--public-key")
    try:
        blob = base64.b64decode(lines[1], validate=True)
    except (IndexError, ValueError) as exc:
        raise SystemExit("--public-key: malformed minisign public key") from exc
    if len(blob) != 42 or blob[:2] != b"Ed":
        raise SystemExit("--public-key: malformed minisign public key")
    return blob[2:10], Ed25519PublicKey.from_public_bytes(blob[10:])


def _verify(path: Path, signature: str, key: tuple[bytes, Ed25519PublicKey]) -> None:
    """Check a Tauri (minisign) signature against the file it claims to sign."""
    key_id, public = key
    lines = _minisign_lines(signature, f"{path.name}.sig")
    try:
        blob = base64.b64decode(lines[1], validate=True)
        trusted = lines[2]
        global_sig = base64.b64decode(lines[3], validate=True)
    except (IndexError, ValueError) as exc:
        raise SystemExit(f"{path.name}.sig: malformed minisign signature") from exc
    if len(blob) != 74 or blob[:2] not in (b"ED", b"Ed") or not trusted.startswith(
        "trusted comment: "
    ):
        raise SystemExit(f"{path.name}.sig: malformed minisign signature")
    if blob[2:10] != key_id:
        raise SystemExit(f"{path.name}.sig: signed with another key than --public-key")
    data = path.read_bytes()
    message = hashlib.blake2b(data, digest_size=64).digest() if blob[:2] == b"ED" else data
    try:
        public.verify(blob[10:], message)
        public.verify(global_sig, blob[10:] + trusted[len("trusted comment: ") :].encode("utf-8"))
    except InvalidSignature as exc:
        raise SystemExit(f"{path.name}.sig: signature does not match the file") from exc


def _entry(
    value: str, base_url: str, prefix: str, key: tuple[bytes, Ed25519PublicKey] | None
) -> tuple[str, dict[str, str]]:
    platform, sep, raw_path = value.partition("=")
    platform = platform.strip()
    if not sep or not platform or not raw_path:
        raise SystemExit(f"--artifact must be PLATFORM=PATH, got {value!r}")
    match = PLATFORM_RE.match(platform)
    if not match:
        raise SystemExit(
            f"--artifact: unknown Tauri platform {platform!r} "
            "(expected e.g. windows-x86_64, darwin-aarch64, darwin-x86_64, linux-x86_64)"
        )
    path = Path(raw_path)
    if not path.is_file():
        raise SystemExit(f"--artifact {platform}: file not found: {path}")
    suffixes = SUFFIXES[match.group(1)]
    if not path.name.endswith(suffixes):
        raise SystemExit(
            f"--artifact {platform}: {path.name} is not a Tauri updater bundle "
            f"(expected {' or '.join(suffixes)})"
        )
    sig_path = path.with_name(path.name + ".sig")
    if not sig_path.is_file():
        raise SystemExit(
            f"--artifact {platform}: {sig_path.name} missing; refusing to publish an unsigned update"
        )
    signature = sig_path.read_text(encoding="utf-8").strip()
    if not signature:
        raise SystemExit(f"--artifact {platform}: {sig_path.name} is empty")
    if key is not None:
        _verify(path, signature, key)
    relative = f"{prefix.strip('/')}/{path.name}" if prefix.strip("/") else path.name
    return platform, {
        "signature": signature,
        "url": urljoin(f"{base_url.rstrip('/')}/", relative),
    }


def build(
    *,
    version: str,
    notes: str,
    pub_date: str,
    base_url: str,
    url_prefix: str,
    artifacts: list[str],
    public_key: str = "",
    previous: dict[str, object] | None = None,
) -> dict[str, object]:
    key = _public_key(public_key) if public_key.strip() else None
    platforms: dict[str, dict[str, str]] = {}
    for value in artifacts:
        platform, entry = _entry(value, base_url, url_prefix, key)
        if platform in platforms:
            raise SystemExit(f"--artifact {platform} given twice")
        platforms[platform] = entry
    if not platforms:
        raise SystemExit("at least one --artifact PLATFORM=PATH is required")
    # Partial OS publish: linux alone must not drop windows/darwin of the same
    # version. Kept entries are only those still carrying a signature.
    kept = (previous or {}).get("platforms") if (previous or {}).get("version") == version else None
    if isinstance(kept, dict):
        for platform, entry in kept.items():
            if (
                platform not in platforms
                and PLATFORM_RE.match(str(platform))
                and isinstance(entry, dict)
                and isinstance(entry.get("signature"), str)
                and entry["signature"].strip()
                and isinstance(entry.get("url"), str)
            ):
                platforms[platform] = {"signature": entry["signature"], "url": entry["url"]}
    return {
        "version": version,
        "notes": notes,
        "pub_date": pub_date,
        "platforms": dict(sorted(platforms.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes", default="")
    parser.add_argument("--pub-date", default="", help="RFC 3339 (default: now, UTC)")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--url-prefix", default="", help="e.g. v2.1.0 (where the files sit)")
    parser.add_argument(
        "--artifact", action="append", default=[], metavar="PLATFORM=PATH",
        help="Tauri updater bundle; PATH.sig must sit next to it.",
    )
    parser.add_argument(
        "--public-key", default="",
        help="tauri.conf.json plugins.updater.pubkey: verify every signature.",
    )
    parser.add_argument(
        "--merge", type=Path,
        help="Previous latest.json: keep its other platforms when the version matches.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"v?\d+\.\d+\.\d+([-+][0-9A-Za-z.+-]+)?", args.version):
        raise SystemExit(f"--version must be semver (Tauri requirement), got {args.version!r}")
    pub_date = _check_pub_date(args.pub_date) if args.pub_date else _rfc3339_now()
    previous: dict[str, object] | None = None
    if args.merge and args.merge.is_file():
        try:
            loaded = json.loads(args.merge.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = None
        previous = loaded if isinstance(loaded, dict) else None
    latest = build(
        version=args.version,
        notes=args.notes,
        pub_date=pub_date,
        base_url=args.base_url,
        url_prefix=args.url_prefix,
        artifacts=args.artifact,
        public_key=args.public_key,
        previous=previous,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(latest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{args.output}: {', '.join(latest['platforms'])}")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
