# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""LLM fix bridge for the navin-engine daemon.

Protocol (navin-fix-request/v1):
  stdin  - one JSON object: {"schema", "engine_version", "project_root", "finding"}
  stdout - a JSON array of fix candidates:
    {"id", "target_finding", "rationale", "family",
     "patch": {"kind": "files", "edits": [{"path", "contents"}]}
            | {"kind": "unified_diff", "diff": "..."}}

The bridge is deliberately forgiving on the output side: when the model
answers nothing usable it prints `[]` and exits 0, so the engine records
"no candidates" instead of a failed fix campaign. It only exits non-zero
when the request itself is unusable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUEST_SCHEMA = "navin-fix-request/v1"

# Context collection bounds: enough to show the model the app, small enough
# to fit comfortably next to the finding in one prompt. Overridable per
# deployment (bigger models, bigger projects) with environment variables:
#   NAVIN_BRIDGE_MAX_FILES, NAVIN_BRIDGE_MAX_FILE_BYTES,
#   NAVIN_BRIDGE_MAX_TOTAL_BYTES
MAX_FILE_BYTES = 48_000
MAX_TOTAL_BYTES = 160_000
MAX_FILES = 24

# Filenames that usually hold the wiring of an app: when nothing in the
# finding names a file, these are the best first read.
ENTRY_STEMS = {"main", "app", "server", "index", "api", "worker", "cli"}


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def _limits() -> tuple[int, int, int]:
    """(max_files, max_file_bytes, max_total_bytes), env-tunable."""
    return (
        _int_env("NAVIN_BRIDGE_MAX_FILES", MAX_FILES),
        _int_env("NAVIN_BRIDGE_MAX_FILE_BYTES", MAX_FILE_BYTES),
        _int_env("NAVIN_BRIDGE_MAX_TOTAL_BYTES", MAX_TOTAL_BYTES),
    )

CODE_SUFFIXES = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".rs", ".go",
    ".rb", ".php", ".java", ".kt", ".c", ".h", ".cpp", ".hpp", ".cs",
    ".sql", ".sh",
}
# Worth showing, but only once the code is in: a manifest explains the app,
# it is not the thing to patch.
DATA_SUFFIXES = {".toml", ".yaml", ".yml", ".json"}
SOURCE_SUFFIXES = CODE_SUFFIXES | DATA_SUFFIXES
SKIP_DIRS = {
    "node_modules", "target", "dist", "build", "out", "coverage",
    "__pycache__", "venv", "vendor", "site-packages",
}
# Generated files that carry no intent: a lockfile or a bundle teaches the
# model nothing about the app and eats the whole budget.
SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "composer.lock",
    "Cargo.lock", "poetry.lock", "tsconfig.tsbuildinfo",
}

SYSTEM_PROMPT = """\
You are the candidate generator of the Navin Evolve engine. You receive one \
finding about a running service - a diagnosed robustness failure or a measured \
performance gap - plus the files of the app it was measured on. Propose up to \
{max_candidates} independent candidates.

Every candidate is verified later in an isolated sandbox: the service is \
restarted, put under load, killed and probed. Only candidates that measurably \
improve robustness without regressions are kept, so prefer small, safe, \
targeted changes over rewrites.

Reply with ONLY a JSON array (no prose, no markdown fences). Each element:
{{
  "id": "short-slug",
  "target_finding": "<the finding id>",
  "rationale": "one sentence: what the change does and why it fixes the finding",
  "family": "<the finding family>",
  "patch": {{
    "kind": "files",
    "edits": [{{"path": "relative/path.py", "contents": "ENTIRE new file contents"}}]
  }}
}}

Rules:
- "edits" must contain the COMPLETE new contents of each touched file, not a diff.
- Paths are relative to the project root; never use absolute paths or "..".
- Keep the service's external behaviour (routes, ports, CLI) unchanged.
- If you cannot propose a credible fix, reply with [].
"""


@dataclass
class FixRequest:
    project_root: Path
    finding: dict[str, Any]
    engine_version: str = ""
    # How the engine started the app it measured. In a monorepo this is the
    # only thing that says which program the numbers are about.
    start_command: str = ""


@dataclass
class BridgeResult:
    candidates: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def parse_request(text: str) -> FixRequest:
    """Validate the engine's stdin payload. Raises ValueError when unusable."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"request is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("request must be a JSON object")
    if data.get("schema") != REQUEST_SCHEMA:
        raise ValueError(
            f"unsupported request schema {data.get('schema')!r}, expected {REQUEST_SCHEMA!r}"
        )
    finding = data.get("finding")
    if not isinstance(finding, dict) or not finding.get("id"):
        raise ValueError("request has no finding with an id")
    root = Path(str(data.get("project_root", "")))
    if not root.is_dir():
        raise ValueError(f"project_root {root} is not a directory")
    return FixRequest(
        project_root=root,
        finding=finding,
        engine_version=str(data.get("engine_version", "")),
        start_command=str(data.get("start_command") or ""),
    )


def _worth_reading(rel: Path) -> bool:
    """Whether a project-relative path holds something a human wrote.

    Hidden directories are skipped wholesale: `.git`, `.venv`, `.next`,
    `.nuxt`, `.pytest_cache` and their kind hold generated matter, and a
    build cache full of tiny manifests is exactly what crowds out real code.
    """
    if rel.name in SKIP_NAMES or rel.name.endswith((".min.js", ".min.css")):
        return False
    return not any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts[:-1])


def app_scope(project_root: Path, start_command: str) -> list[Path]:
    """The directories the start command actually points at.

    A monorepo holds dozens of programs; only one of them was measured.
    ``cd site && npm run dev``, ``npm --prefix site run dev`` and
    ``uvicorn api/main.py`` all name where the app lives, and showing the
    model that subtree beats showing it a sample of the whole repository.
    Returns an empty list when the command says nothing useful.
    """
    import shlex

    if not start_command.strip():
        return []
    try:
        tokens = shlex.split(start_command, posix=True)
    except ValueError:
        tokens = start_command.split()

    named: list[str] = []
    for index, token in enumerate(tokens):
        if token in {"cd", "--prefix", "-C", "--directory", "--cwd"} and index + 1 < len(tokens):
            named.append(tokens[index + 1])
        elif not token.startswith("-") and ("/" in token or "." in token):
            named.append(token)

    scope: list[Path] = []
    for name in named:
        candidate = (project_root / name).resolve()
        if not str(candidate).startswith(str(project_root.resolve())):
            continue
        directory = candidate if candidate.is_dir() else candidate.parent
        if not directory.is_dir() or directory == project_root.resolve():
            continue
        rel = directory.relative_to(project_root.resolve())
        if any(part in SKIP_DIRS or part.startswith(".") for part in rel.parts):
            continue
        if directory not in scope:
            scope.append(directory)
    return scope


# Path-looking tokens in a finding (stack traces, log lines): the strongest
# possible signal about which file to show the model.
_PATH_TOKEN = re.compile(r"[\w][\w.\\/-]*\.[A-Za-z]{1,5}")


def _finding_path_tokens(finding_text: str) -> set[str]:
    """Multi-segment paths named by the finding, normalised to posix."""
    tokens = set()
    for match in _PATH_TOKEN.finditer(finding_text):
        token = match.group(0).replace("\\", "/").lstrip("./").lower()
        if "/" in token:
            tokens.add(token)
    return tokens


def collect_context(
    project_root: Path,
    finding: dict[str, Any],
    start_command: str = "",
) -> list[tuple[str, str]]:
    """Pick the source files most worth showing to the model.

    The search is limited to the app the engine started, when the start
    command says where it lives. Within that, ordering is by strength of
    evidence: files whose relative path appears in the finding (a stack
    trace is as precise as it gets), then files whose bare name appears,
    then entry points (main/app/server/...), then the rest smallest-first
    so the budget covers more of the app.
    """
    max_files, max_file_bytes, max_total_bytes = _limits()
    finding_text = json.dumps(finding).lower()
    path_tokens = _finding_path_tokens(finding_text)
    base = project_root.resolve()
    roots = app_scope(project_root, start_command) or [base]
    candidates: list[tuple[bool, bool, int, bool, int, Path]] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            rel = path.relative_to(base)
            if not _worth_reading(rel):
                continue
            suffix = path.suffix.lower()
            if suffix not in SOURCE_SUFFIXES:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size == 0 or size > max_file_bytes:
                continue
            rel_posix = rel.as_posix().lower()
            path_mentioned = any(
                rel_posix == token or rel_posix.endswith(f"/{token}") for token in path_tokens
            )
            name_mentioned = path.name.lower() in finding_text
            is_code = 0 if suffix in CODE_SUFFIXES else 1
            entry_point = path.stem.lower() in ENTRY_STEMS and len(rel.parts) <= 3
            candidates.append((path_mentioned, name_mentioned, is_code, entry_point, size, path))

    # Strongest evidence first (False sorts before True, hence negations):
    # full path match, then name match, then code before manifests, then
    # entry points, then smallest first to cover more ground.
    candidates.sort(key=lambda item: (not item[0], not item[1], item[2], not item[3], item[4]))

    picked: list[tuple[str, str]] = []
    total = 0
    for *_ignored, size, path in candidates:
        if len(picked) >= max_files or total + size > max_total_bytes:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        picked.append((str(path.relative_to(base)), text))
        total += size
    return picked


def build_messages(
    request: FixRequest,
    files: list[tuple[str, str]],
    max_candidates: int,
) -> list[dict[str, Any]]:
    parts = [
        "## Finding (diagnosed by the proof engine)",
        json.dumps(request.finding, indent=2, ensure_ascii=False),
        "",
    ]
    if request.start_command:
        parts += [
            "## The app the engine measured",
            f"It is started with: {request.start_command}",
            "The files below belong to that app; patch those, nothing else.",
            "",
        ]
    parts.append("## Project files")
    if not files:
        parts.append("(no readable source files found)")
    for rel, text in files:
        parts.append(f"### {rel}\n```\n{text}\n```")
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(max_candidates=max_candidates)},
        {"role": "user", "content": "\n".join(parts)},
    ]


def _extract_json_array(text: str) -> Any:
    """Pull the first JSON array out of a model reply, tolerating fences."""
    cleaned = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost [...] span.
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start != -1 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("no JSON array found in the model reply")


def _safe_relative_path(path: str) -> bool:
    if not path or path.startswith(("/", "\\")) or re.match(r"^[a-zA-Z]:", path):
        return False
    return ".." not in Path(path).parts


def parse_candidates(text: str, finding: dict[str, Any]) -> BridgeResult:
    """Normalise the model reply into engine-shaped candidates.

    Invalid entries are dropped with a note instead of failing the whole
    reply: one good candidate is worth keeping even next to a bad one.
    """
    result = BridgeResult()
    try:
        raw = _extract_json_array(text)
    except (ValueError, json.JSONDecodeError) as exc:
        result.notes.append(f"unparseable model reply: {exc}")
        return result
    if not isinstance(raw, list):
        result.notes.append("model reply is not a JSON array")
        return result

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            result.notes.append(f"candidate {index}: not an object, dropped")
            continue
        patch = item.get("patch")
        if not isinstance(patch, dict):
            result.notes.append(f"candidate {index}: missing patch, dropped")
            continue
        kind = patch.get("kind")
        if kind == "files":
            edits = patch.get("edits")
            if not isinstance(edits, list) or not edits:
                result.notes.append(f"candidate {index}: empty edits, dropped")
                continue
            ok = True
            for edit in edits:
                if (
                    not isinstance(edit, dict)
                    or not isinstance(edit.get("path"), str)
                    or not isinstance(edit.get("contents"), str)
                    or not _safe_relative_path(edit["path"])
                ):
                    result.notes.append(f"candidate {index}: unsafe or malformed edit, dropped")
                    ok = False
                    break
            if not ok:
                continue
            patch = {"kind": "files", "edits": [
                {"path": e["path"], "contents": e["contents"]} for e in edits
            ]}
        elif kind == "unified_diff":
            if not isinstance(patch.get("diff"), str) or not patch["diff"].strip():
                result.notes.append(f"candidate {index}: empty diff, dropped")
                continue
            patch = {"kind": "unified_diff", "diff": patch["diff"]}
        else:
            result.notes.append(f"candidate {index}: unknown patch kind {kind!r}, dropped")
            continue

        candidate_id = str(item.get("id") or f"llm-{index + 1}")
        rationale = str(item.get("rationale") or "").strip() or "model-proposed fix"
        result.candidates.append({
            "id": candidate_id,
            # The engine filters on target_finding; never trust the model here.
            "target_finding": str(finding.get("id", "")),
            "rationale": rationale,
            "family": str(item.get("family") or finding.get("family") or "reliability"),
            "patch": patch,
        })
    return result


async def generate(
    request: FixRequest,
    provider: Any,
    model: str,
    max_candidates: int,
) -> BridgeResult:
    files = collect_context(request.project_root, request.finding, request.start_command)
    messages = build_messages(request, files, max_candidates)
    response = await provider.chat_with_retry(
        messages,
        model=model,
        temperature=0.2,
    )
    if response.finish_reason == "error" or response.content is None:
        result = BridgeResult()
        result.notes.append(f"LLM call failed: {response.content or response.finish_reason}")
        return result
    result = parse_candidates(response.content, request.finding)
    del result.candidates[max_candidates:]
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="navin.evolve.bridge",
        description="LLM fix bridge invoked by the navin-engine daemon",
    )
    parser.add_argument("--config", type=Path, default=None, help="navin config path")
    parser.add_argument(
        "--preset",
        # The engine daemon forwards the operator's per-campaign choice via
        # this environment variable; the CLI flag still wins when given.
        default=os.environ.get("NAVIN_BRIDGE_PRESET") or None,
        help="model preset name to use",
    )
    parser.add_argument("--max-candidates", type=int, default=3)
    args = parser.parse_args(argv)

    try:
        request = parse_request(sys.stdin.read())
    except ValueError as exc:
        print(f"navin.evolve.bridge: {exc}", file=sys.stderr)
        return 1

    try:
        from navin.providers.factory import load_provider_snapshot

        snapshot = load_provider_snapshot(args.config, preset_name=args.preset)
        result = asyncio.run(
            generate(request, snapshot.provider, snapshot.model, args.max_candidates)
        )
    except Exception as exc:  # noqa: BLE001 - a broken provider must not fail the campaign
        print(f"navin.evolve.bridge: provider error: {exc}", file=sys.stderr)
        print("[]")
        return 0

    for note in result.notes:
        print(f"navin.evolve.bridge: {note}", file=sys.stderr)
    print(json.dumps(result.candidates, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
