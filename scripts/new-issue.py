#!/usr/bin/env python3
"""Create a Navin GitHub issue that matches the human issue forms.

GitHub issue forms only run in the browser. Agents MUST use this script
instead of `gh issue create` or the Issues API. Schema:
`.github/issue-intake.schema.json`. Guide: `.github/ISSUE_INTAKE.md`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / ".github" / "issue-intake.schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def die(message: str, code: int = 1) -> None:
    print(f"new-issue: {message}", file=sys.stderr)
    raise SystemExit(code)


def ensure_prefix(title: str, prefix: str) -> str:
    stripped = title.strip()
    if stripped.lower().startswith(prefix.lower()):
        return prefix + stripped[len(prefix) :].lstrip()
    return prefix + stripped


def render_body(
    payload: dict[str, Any],
    kind_spec: dict[str, Any],
    headings: dict[str, str],
) -> str:
    order = list(kind_spec.get("required", [])) + list(kind_spec.get("optional", []))
    chunks: list[str] = []
    for key in order:
        if key == "title":
            continue
        value = payload.get(key)
        if value is None or value == "" or value == []:
            continue
        heading = headings.get(key, key.replace("_", " ").title())
        if isinstance(value, list):
            rendered = "\n".join(f"- {item}" for item in value)
        else:
            rendered = str(value).rstrip()
        chunks.append(f"### {heading}\n\n{rendered}")
    chunks.append(
        "### Confirmations\n\n"
        "- Searched existing issues for this report.\n"
        "- Excluded API keys, tokens, and `~/.navin/config.json` dumps."
    )
    return "\n\n".join(chunks) + "\n"


def validate(payload: dict[str, Any], schema: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    kind = payload.get("kind")
    kinds = schema["kinds"]
    if kind not in kinds:
        die(f"kind must be one of: {', '.join(sorted(kinds))}")
    spec = kinds[kind]
    if spec.get("refuse"):
        email = schema["security_email"]
        die(
            f"kind=security is refused. Email {email} privately. "
            "See SECURITY.md. Do not open a public GitHub issue.",
            code=2,
        )
    missing = [key for key in spec.get("required", []) if not payload.get(key)]
    if missing:
        die(f"missing required fields for kind={kind}: {', '.join(missing)}")

    enums = schema.get("enums", {})
    for key, allowed in enums.items():
        value = payload.get(key)
        if value is None:
            continue
        if key == "installations":
            if not isinstance(value, list) or not value:
                die("installations must be a non-empty list of allowed values")
            bad = [item for item in value if item not in allowed]
            if bad:
                die(f"unknown installations {bad}. allowed: {allowed}")
            continue
        if value not in allowed:
            die(f"{key}={value!r} is not in {allowed}")

    extra = set(payload) - {"kind"} - set(spec.get("required", [])) - set(
        spec.get("optional", [])
    )
    if extra:
        die(f"unknown fields for kind={kind}: {', '.join(sorted(extra))}")
    return kind, spec


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open a Navin issue that matches .github/ISSUE_TEMPLATE forms."
    )
    parser.add_argument("--schema", action="store_true", help="Print the intake schema and exit.")
    parser.add_argument("--payload", type=Path, help="JSON payload (see .github/ISSUE_INTAKE.md).")
    parser.add_argument("--kind", help="Override payload kind.")
    parser.add_argument("--title", help="Override payload title.")
    parser.add_argument(
        "--repo",
        default=None,
        help="owner/repo. Defaults to the schema repo field.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the issue and do not call gh.")
    parser.add_argument(
        "--confirm-searched",
        action="store_true",
        help="Confirm existing issues were searched.",
    )
    parser.add_argument(
        "--confirm-no-secrets",
        action="store_true",
        help="Confirm the payload has no secrets.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    schema = load_schema()
    if args.schema:
        json.dump(schema, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.payload is None:
        die("pass --payload FILE.json (or --schema)")
    if not args.confirm_searched or not args.confirm_no_secrets:
        die("both --confirm-searched and --confirm-no-secrets are required")
    try:
        payload = json.loads(args.payload.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"cannot read payload: {exc}")
    if not isinstance(payload, dict):
        die("payload must be a JSON object")
    if args.kind:
        payload["kind"] = args.kind
    if args.title:
        payload["title"] = args.title

    kind, spec = validate(payload, schema)
    title = ensure_prefix(str(payload["title"]), spec["title_prefix"])
    labels = [spec["label"]]
    body = render_body(payload, spec, schema["headings"])
    repo = args.repo or schema["repo"]
    result = {
        "repo": repo,
        "kind": kind,
        "title": title,
        "labels": labels,
        "body": body,
    }
    if args.dry_run:
        json.dump(result, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as handle:
        handle.write(body)
        body_path = handle.name
    cmd = [
        "gh",
        "issue",
        "create",
        "--repo",
        repo,
        "--title",
        title,
        "--label",
        labels[0],
        "--body-file",
        body_path,
    ]
    try:
        completed = subprocess.run(cmd, check=False, text=True, capture_output=True)
    except FileNotFoundError:
        die("gh is not on PATH")
    if completed.returncode != 0:
        die(completed.stderr.strip() or f"gh exited {completed.returncode}")
    sys.stdout.write(completed.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
