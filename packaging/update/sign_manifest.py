#!/usr/bin/env python3
"""Create and Ed25519-sign a Navin update manifest."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urljoin

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _private_key() -> Ed25519PrivateKey:
    raw = os.environ.get("UPDATE_SIGNING_KEY", "").strip()
    if not raw:
        raise SystemExit("UPDATE_SIGNING_KEY is required (base64-encoded 32-byte Ed25519 key)")
    try:
        decoded = base64.b64decode(raw, validate=True)
        if len(decoded) != 32:
            raise ValueError
        return Ed25519PrivateKey.from_private_bytes(decoded)
    except (TypeError, ValueError) as exc:
        raise SystemExit("UPDATE_SIGNING_KEY must encode exactly 32 bytes") from exc


def _artifact(value: str, base_url: str, prefix: str) -> tuple[str, dict[str, object]]:
    try:
        key, raw_path = value.split("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("artifact must be PLATFORM=PATH") from exc
    path = Path(raw_path).resolve()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"artifact not found: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    relative = f"{prefix.strip('/')}/{path.name}" if prefix.strip("/") else path.name
    return key, {
        "url": urljoin(f"{base_url.rstrip('/')}/", relative),
        "size": path.stat().st_size,
        "sha256": digest,
        "filename": path.name,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version")
    parser.add_argument("--channel", choices=("stable", "beta"), default="stable")
    parser.add_argument("--base-url", default="")
    # Where the artifacts actually sit under the base URL. It has to be told,
    # not guessed: a manifest whose URLs do not match the store signs a
    # download that 404s, and the signature makes that failure look deliberate.
    parser.add_argument("--url-prefix", default="")
    parser.add_argument("--minimum-version", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--rollout", type=int, default=100)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--merge-manifest",
        type=Path,
        help="Keep artifacts from this manifest when the version matches "
        "(partial OS publish: linux does not drop windows/macos).",
    )
    parser.add_argument("--write-public-key", type=Path)
    parser.add_argument("--public-key-only", action="store_true")
    args = parser.parse_args()

    private = _private_key()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if args.write_public_key:
        args.write_public_key.parent.mkdir(parents=True, exist_ok=True)
        args.write_public_key.write_text(base64.b64encode(public).decode("ascii") + "\n")
    if args.public_key_only:
        return
    if not args.version or not args.base_url or not args.output_dir or not args.artifact:
        parser.error("--version, --base-url, --output-dir and --artifact are required")
    if not 0 <= args.rollout <= 100:
        parser.error("--rollout must be between 0 and 100")

    prefix = args.url_prefix or f"releases/{args.version}"
    artifacts: dict[str, object] = {}
    if args.merge_manifest and args.merge_manifest.is_file():
        try:
            previous = json.loads(args.merge_manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            previous = {}
        if (
            isinstance(previous, dict)
            and previous.get("version") == args.version
            and isinstance(previous.get("artifacts"), dict)
        ):
            artifacts.update(previous["artifacts"])
    artifacts.update(dict(_artifact(value, args.base_url, prefix) for value in args.artifact))
    manifest = {
        "schemaVersion": 1,
        "channel": args.channel,
        "version": args.version,
        "minimumVersion": args.minimum_version,
        "notes": args.notes,
        "rollout": args.rollout,
        "artifacts": artifacts,
    }
    raw = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = private.sign(raw)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.json").write_bytes(raw)
    (args.output_dir / "manifest.sig").write_text(
        base64.b64encode(signature).decode("ascii") + "\n",
        encoding="ascii",
    )


if __name__ == "__main__":
    main()
