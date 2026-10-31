#!/usr/bin/env python3
"""Create and Ed25519-sign a Navin update manifest.

The manifest stays ``schemaVersion`` 1: every field added after the first
release is optional, and a client that predates it ignores keys it does not
know. Fields:

- ``schemaVersion``, ``channel``, ``version``, ``minimumVersion``, ``notes``,
  ``rollout``, ``artifacts``: the original format.
- ``components``: versions of what one desktop package embeds (desktop shell,
  Python runtime, CLI, web UI, native libraries). They ship together, so they
  must all equal ``version`` unless ``--allow-mixed-components`` says otherwise.
- ``dataVersion``: schema version of the local data the release expects.
- ``suspended``: the release is published but paused; no client installs it.
- ``revokedVersions``: defective versions clients must move away from.
- ``rollback`` (only when given): the known-good older release, with its own
  signed ``artifacts`` in the same shape as the top-level ones.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CHANNELS = ("stable", "beta", "enterprise")
# Everything a desktop package embeds. One build produces all of them, so a
# manifest announcing a desktop 2.1.0 with a 2.0.9 runtime describes a package
# nobody built.
COMPONENTS = ("desktop", "runtime", "cli", "webui", "native")


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


def _version_key(value: str, what: str) -> Any:
    """Comparable key, with the same rules the client uses (packaging.Version)."""
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:  # pragma: no cover - packaging ships with pip/setuptools
        match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[.-]?([0-9A-Za-z.]+))?", value.strip())
        if not match:
            raise SystemExit(f"{what}: invalid version {value!r}") from None
        major, minor, patch, pre = match.groups()
        # A pre-release sorts before the final release it precedes.
        return (int(major), int(minor), int(patch), 0 if pre else 1, pre or "")
    try:
        return Version(value)
    except InvalidVersion as exc:
        raise SystemExit(f"{what}: invalid version {value!r}") from exc


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


def _component(value: str) -> tuple[str, str]:
    name, sep, version = value.partition("=")
    name, version = name.strip(), version.strip()
    if not sep or not name or not version:
        raise SystemExit(f"--component must be NAME=VERSION, got {value!r}")
    if name not in COMPONENTS:
        raise SystemExit(
            f"--component: unknown component {name!r} (expected one of {', '.join(COMPONENTS)})"
        )
    _version_key(version, f"--component {name}")
    return name, version


def _previous_manifest(path: Path | None, version: str) -> dict[str, Any]:
    """The manifest already published for the same version, or {}."""
    if not path or not path.is_file():
        return {}
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    if not isinstance(previous, dict) or previous.get("version") != version:
        return {}
    return previous


def _components(args: argparse.Namespace, previous: dict[str, Any]) -> dict[str, str]:
    components = {name: args.version for name in COMPONENTS}
    kept = previous.get("components")
    if isinstance(kept, dict):
        components.update(
            {k: str(v) for k, v in kept.items() if k in COMPONENTS and isinstance(v, str) and v}
        )
    components.update(dict(_component(value) for value in args.component))
    mixed = sorted(name for name, version in components.items() if version != args.version)
    if mixed and not args.allow_mixed_components:
        detail = ", ".join(f"{name}={components[name]}" for name in mixed)
        raise SystemExit(
            f"components out of step with --version {args.version}: {detail}. "
            "A desktop package embeds the runtime, CLI, web UI and native libraries "
            "built together; pass --allow-mixed-components only if this is deliberate."
        )
    return components


def _revoked(args: argparse.Namespace, previous: dict[str, Any]) -> list[str]:
    revoked: set[str] = set()
    kept = previous.get("revokedVersions")
    if isinstance(kept, list):
        revoked.update(v for v in kept if isinstance(v, str) and v)
    for value in args.revoke:
        value = value.strip()
        _version_key(value, "--revoke")
        revoked.add(value)
    if args.version in revoked:
        raise SystemExit(
            f"--revoke {args.version}: cannot revoke the version being published "
            "(use --suspend to pause it)"
        )
    return sorted(revoked, key=lambda v: _version_key(v, "revokedVersions"))


def _rollback(
    args: argparse.Namespace, previous: dict[str, Any], revoked: list[str]
) -> dict[str, Any] | None:
    kept = previous.get("rollback") if isinstance(previous.get("rollback"), dict) else None
    if not args.rollback_version:
        if args.rollback_artifact:
            raise SystemExit("--rollback-artifact requires --rollback-version")
        return kept
    version = args.rollback_version.strip()
    if _version_key(version, "--rollback-version") >= _version_key(args.version, "--version"):
        raise SystemExit(
            f"--rollback-version {version} must be older than --version {args.version}"
        )
    if version in revoked:
        raise SystemExit(f"--rollback-version {version} is listed in --revoke")
    data_version = (
        args.rollback_data_version if args.rollback_data_version is not None else args.data_version
    )
    if data_version < 1:
        raise SystemExit("--rollback-data-version must be >= 1")
    if data_version > args.data_version:
        raise SystemExit(
            f"--rollback-data-version {data_version} is newer than --data-version {args.data_version}"
        )
    artifacts: dict[str, object] = {}
    if kept and kept.get("version") == version and isinstance(kept.get("artifacts"), dict):
        artifacts.update(kept["artifacts"])
    prefix = args.rollback_url_prefix or f"releases/{version}"
    artifacts.update(
        dict(_artifact(value, args.base_url, prefix) for value in args.rollback_artifact)
    )
    if not artifacts:
        raise SystemExit("--rollback-version needs at least one --rollback-artifact PLATFORM=PATH")
    notes = args.rollback_notes
    if not notes and kept and kept.get("version") == version:
        notes = str(kept.get("notes") or "")
    return {
        "version": version,
        "notes": notes,
        "dataVersion": data_version,
        "artifacts": artifacts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version")
    parser.add_argument("--channel", choices=CHANNELS, default="stable")
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
        help="Keep artifacts, components, rollback and revokedVersions from this "
        "manifest when the version matches (partial OS publish: linux does not "
        "drop windows/macos).",
    )
    parser.add_argument(
        "--component",
        action="append",
        default=[],
        metavar="NAME=VERSION",
        help=f"Override one embedded component version ({', '.join(COMPONENTS)}).",
    )
    parser.add_argument("--allow-mixed-components", action="store_true")
    parser.add_argument("--data-version", type=int, default=1)
    parser.add_argument(
        "--suspend",
        action="store_true",
        help="Publish the release paused: no client installs it.",
    )
    parser.add_argument("--revoke", action="append", default=[], metavar="VERSION")
    parser.add_argument("--rollback-version", default="")
    parser.add_argument("--rollback-notes", default="")
    parser.add_argument(
        "--rollback-artifact", action="append", default=[], metavar="PLATFORM=PATH"
    )
    parser.add_argument("--rollback-url-prefix", default="")
    parser.add_argument("--rollback-data-version", type=int, default=None)
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
    if args.data_version < 1:
        parser.error("--data-version must be >= 1")
    _version_key(args.version, "--version")

    previous = _previous_manifest(args.merge_manifest, args.version)
    components = _components(args, previous)
    revoked = _revoked(args, previous)
    rollback = _rollback(args, previous, revoked)

    prefix = args.url_prefix or f"releases/{args.version}"
    artifacts: dict[str, object] = {}
    if isinstance(previous.get("artifacts"), dict):
        artifacts.update(previous["artifacts"])
    artifacts.update(dict(_artifact(value, args.base_url, prefix) for value in args.artifact))
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "channel": args.channel,
        "version": args.version,
        "minimumVersion": args.minimum_version,
        "notes": args.notes,
        "rollout": args.rollout,
        "artifacts": artifacts,
        "components": components,
        "dataVersion": args.data_version,
        "suspended": bool(args.suspend),
        "revokedVersions": revoked,
    }
    if rollback is not None:
        manifest["rollback"] = rollback
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
