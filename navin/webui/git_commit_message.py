# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""AI commit message for the source-control panel.

Same idea as VS Code Copilot on the SCM input: conventional commits, one
clear subject that says what changed, under 72 characters. Uses the editor
assist model (one shot), never a full agent turn.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from navin.security.workspace_access import WorkspaceScope
from navin.webui.assist_api import AssistError, _assist_preset, _load_snapshot, _route_label
from navin.webui.project_search import git_commit_diff_context

SUBJECT_MAX = 72
_MAX_TOKENS = 256
_TIMEOUT_S = 20.0
_THINK_RE = re.compile(
    r"<(think|thinking|reason)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_CONVENTIONAL_RE = re.compile(
    r"^(feat|fix|docs|style|refactor|test|chore|perf|build|ci)"
    r"(?:\([a-z0-9._/-]+\))?:\s+\S.+$",
    re.IGNORECASE,
)
_META_RE = re.compile(
    r"^(we need to|the description should|output only|write (a |the )?"
    r"(git )?commit|format \(conventional|never mention|key changes\b|"
    r"here('s| is) (the )?commit|types:\s*feat|scope:\s*optional|"
    r"reply with|une seule ligne|the subject should)",
    re.IGNORECASE,
)

_SYSTEM = (
    "You are VS Code Copilot on the Source Control commit input.\n"
    "Reply with EXACTLY one line and nothing else.\n"
    "No quotes, no markdown, no bullets, no body, no reasoning, no preamble.\n"
    "Do not restate these instructions.\n"
    "\n"
    "Line format, max "
    f"{SUBJECT_MAX} characters:\n"
    "  type(scope): description\n"
    "\n"
    "type is one of: feat, fix, docs, style, refactor, test, chore, perf, "
    "build, ci.\n"
    "scope is optional and short (git, career, engine, webui).\n"
    "description is what actually changed in the diff. Never "
    "'update N files'. No trailing period.\n"
    "Never mention AI or Copilot. Never use unicode em/en dashes.\n"
    "\n"
    "The description must name the actual change, not a file list.\n"
    "Good:\n"
    "  feat(helm): add the ocr-rh-crm backend to the deploy config\n"
    "  style: remove extra spaces in the Dockerfile\n"
    "  feat(git): generate a conventional commit message from the diff\n"
    "Bad:\n"
    "  We need to generate a conventional commit message\n"
    "  Update git_commit_message.py and 2 other files\n"
    "  feat(webui): adjust navin-engine and the related diff"
)


def sanitize_commit_message(raw: str) -> str:
    """Keep a model reply as one conventional subject line.

    Prefers a `type(scope): ...` line if one appears (including inside a
    think block). Drops instruction echoes, bodies, fences, and dashes.
    """
    extracted = _first_conventional_line(raw or "")
    text = extracted or _THINK_RE.sub("", raw or "")
    text = _strip_fences(text)
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    text = text.strip().strip('"').strip("'").strip()
    lowered = text.lower()
    for prefix in ("commit message:", "commit:", "message:"):
        if lowered.startswith(prefix):
            text = text[len(prefix) :].strip().strip('"').strip("'")
            break
    subject = ""
    for raw_line in text.splitlines():
        candidate = raw_line.strip().strip('"').strip("'").lstrip("# ")
        if not candidate:
            continue
        if _is_meta_subject(candidate):
            return ""
        subject = _clip_line(candidate, SUBJECT_MAX).rstrip(".")
        break
    if not subject or _is_meta_subject(subject) or _is_poor_subject(subject):
        return ""
    return subject


def _first_conventional_line(raw: str) -> str:
    """Pick the first `type(scope): subject` line, even inside <think>."""
    for line in (raw or "").splitlines():
        cleaned = line.strip().strip('"').strip("'").lstrip("-*# ").rstrip(".")
        if _is_meta_subject(cleaned) or _is_poor_subject(cleaned):
            continue
        if _CONVENTIONAL_RE.match(cleaned) and len(cleaned) <= SUBJECT_MAX + 8:
            return cleaned
    return ""


def _is_meta_subject(line: str) -> bool:
    """True when the model restated the prompt instead of writing a subject."""
    text = (line or "").strip()
    if not text:
        return True
    return bool(_META_RE.match(text))


def _is_poor_subject(line: str) -> bool:
    """True for empty placeholders like 'adjust X and the related diff'."""
    low = (line or "").lower()
    if "related diff" in low or "diff associe" in low:
        return True
    if re.search(r"\b\d+ other files\b", low):
        return True
    if re.search(r"\bupdate \d+ files\b", low):
        return True
    return False


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2:
        return ""
    lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _clip_line(line: str, limit: int) -> str:
    if len(line) <= limit:
        return line
    cut = line[:limit].rsplit(" ", 1)[0].rstrip("-").rstrip()
    return cut or line[:limit]


def fallback_commit_message(ctx: dict[str, Any], *, lang: str = "en") -> str:
    """Conventional-commit fallback when the model returns nothing usable."""
    files = [str(path) for path in (ctx.get("files") or []) if path]
    french = lang.lower().startswith("fr")
    if not files:
        subject = "chore: mettre a jour le projet" if french else "chore: update the project"
        return _clip_line(subject, SUBJECT_MAX)
    kind, scope, desc = _fallback_intent(files, lang=lang)
    prefix = f"{kind}({scope}): " if scope else f"{kind}: "
    return _clip_line(prefix + desc, SUBJECT_MAX).rstrip(".")


def _fallback_intent(files: list[str], *, lang: str) -> tuple[str, str, str]:
    """Kind, scope, and a concrete description from the changed paths."""
    french = lang.lower().startswith("fr")
    kind, scope = _guess_type_and_scope(files)
    blob = " ".join(path.lower().replace("\\", "/") for path in files)
    compact = re.sub(r"[^a-z0-9]+", "", blob)

    if "gitcommitmessage" in compact or "devgitpanel" in compact:
        desc = (
            "generer un message de commit conventionnel depuis le diff"
            if french
            else "generate a conventional commit message from the diff"
        )
        return "feat", "git", desc

    primary = _primary_path(files)
    label = Path(primary).name
    verbs_fr = {
        "feat": "ameliorer",
        "fix": "corriger",
        "docs": "documenter",
        "style": "nettoyer",
        "refactor": "reorganiser",
        "test": "couvrir",
        "chore": "mettre a jour",
        "perf": "accelerer",
        "build": "ajuster",
        "ci": "ajuster",
    }
    verbs_en = {
        "feat": "improve",
        "fix": "fix",
        "docs": "document",
        "style": "clean up",
        "refactor": "restructure",
        "test": "cover",
        "chore": "update",
        "perf": "speed up",
        "build": "adjust",
        "ci": "adjust",
    }
    verb = (verbs_fr if french else verbs_en).get(
        kind, "mettre a jour" if french else "update"
    )
    return kind, scope, f"{verb} {label}"


def _primary_path(files: list[str]) -> str:
    """Prefer a real source file over a submodule dir or a test."""

    def score(path: str) -> tuple[int, int]:
        posix = path.replace("\\", "/")
        name = Path(posix).name.lower()
        suffix = Path(posix).suffix.lower()
        points = 0
        if suffix in {".py", ".ts", ".tsx", ".rs", ".go", ".js"}:
            points += 6
        if "test" not in posix.lower() and "spec" not in name:
            points += 3
        if any(token in name for token in ("git", "commit", "panel", "career")):
            points += 4
        if suffix == "":
            points -= 4
        return (points, len(name))

    return max(files, key=score)


def _guess_type_and_scope(files: list[str]) -> tuple[str, str]:
    lowered_files = [path.lower() for path in files]
    joined = " ".join(lowered_files)
    compact = re.sub(r"[^a-z0-9]+", "", joined)
    kind = "chore"
    if all("test" in path for path in lowered_files) and files:
        kind = "test"
    elif any("test" in path for path in lowered_files):
        kind = "feat"
    if "fix" in joined or "bug" in joined:
        kind = "fix"
    if files and all(path.endswith((".md", ".rst")) for path in lowered_files) and len(files) <= 2:
        kind = "docs"
    if "style" in joined or any(path.endswith(".css") for path in lowered_files):
        kind = "style"

    for token, token_kind, token_scope in (
        ("gitcommit", "feat", "git"),
        ("devgitpanel", "feat", "git"),
        ("career", "feat", "career"),
    ):
        if token in compact:
            return token_kind, token_scope

    scopes = []
    for path in files:
        parts = Path(path).as_posix().split("/")
        if len(parts) >= 2:
            if parts[0] in {"webui", "navin", "crates", "site", "docs", "tests"}:
                scopes.append(parts[1] if parts[0] in {"navin", "crates", "webui"} else parts[0])
            else:
                scopes.append(parts[0])
        elif parts:
            stem = Path(parts[0]).stem.replace("test_", "").replace("_test", "")
            scopes.append(stem)
    scope = ""
    if scopes:
        counts: dict[str, int] = {}
        for item in scopes:
            token = re.sub(r"[^a-z0-9]+", "", item.lower())[:16]
            if token:
                counts[token] = counts.get(token, 0) + 1
        if counts:
            scope = max(counts, key=counts.get)
    return kind, scope


def _system_for_lang(lang: str) -> str:
    if lang.lower().startswith("fr"):
        return (
            _SYSTEM
            + "\nDescription in French, infinitive (ajouter, corriger, "
            "supprimer), like VS Code in French. One line only.\n"
            "Good:\n"
            "  feat(helm): ajouter le backend ocr-rh-crm a la config de deploiement\n"
            "  style: supprimer les espaces inutiles dans le Dockerfile\n"
            "Bad:\n"
            "  feat(webui): ajuster navin-engine et le diff associe"
        )
    return _SYSTEM + "\nDescription in English, imperative mood. One line only."


def _user_prompt(ctx: dict[str, Any], lang: str) -> str:
    files = ctx.get("files") or []
    listed = "\n".join(f"- {path}" for path in files[:40])
    recent = ctx.get("recent") or []
    recent_block = "\n".join(recent) if recent else "(none yet)"
    stat = (ctx.get("stat") or "").strip() or "(no tracked diffstat)"
    diff = (ctx.get("diff") or "").strip() or "(no diff text)"
    branch = ctx.get("branch") or ""
    lang_line = (
        "Language: French (infinitive, conventional commits)."
        if lang.lower().startswith("fr")
        else "Language: English (imperative, conventional commits)."
    )
    return (
        f"{lang_line}\n"
        f"Branch: {branch or '(unknown)'}\n"
        f"Files ({len(files)}):\n{listed}\n\n"
        f"Recent commit subjects:\n{recent_block}\n\n"
        f"Diffstat:\n{stat}\n\n"
        f"Diff:\n{diff}\n\n"
        "Now output the single commit line, nothing else."
    )


async def _ask_commit(system: str, user: str) -> tuple[str, str, str, str]:
    """One-shot call. Content and reasoning stay separate so think-dumps
    cannot become the commit subject."""
    preset = _assist_preset()
    try:
        snapshot = await asyncio.to_thread(_load_snapshot, preset)
    except Exception as exc:
        raise AssistError(f"no model configured: {exc}", status=503) from exc
    try:
        response = await asyncio.wait_for(
            snapshot.provider.chat_with_retry(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=snapshot.model,
                max_tokens=_MAX_TOKENS,
                temperature=0.2,
            ),
            timeout=_TIMEOUT_S,
        )
    except TimeoutError as exc:
        raise AssistError("the model did not answer in time", status=504) from exc
    except Exception as exc:
        raise AssistError(f"model call failed: {exc}", status=502) from exc
    content = (response.content or "").strip()
    reasoning = (getattr(response, "reasoning_content", None) or "").strip()
    return content, reasoning, str(snapshot.model or ""), _route_label(preset)


def _pick_commit_line(content: str, reasoning: str) -> str:
    """Visible content first. Reasoning only if it holds a conventional line."""
    from_content = sanitize_commit_message(content)
    if from_content:
        return from_content
    conventional = _first_conventional_line(reasoning)
    if conventional:
        return sanitize_commit_message(conventional)
    return ""


async def generate_commit_message_payload(
    scope: WorkspaceScope,
    paths: list[str] | None = None,
    lang: str = "en",
) -> dict[str, Any]:
    """Build the working-tree context and ask the model for a commit message."""
    ctx = git_commit_diff_context(scope, paths)
    try:
        content, reasoning, model, route = await _ask_commit(
            _system_for_lang(lang),
            _user_prompt(ctx, lang),
        )
    except AssistError:
        raise
    except Exception as exc:
        raise AssistError(f"model call failed: {exc}", status=502) from exc

    message = _pick_commit_line(content, reasoning)
    if not message:
        message = fallback_commit_message(ctx, lang=lang)
    return {
        "message": message,
        "model": model,
        "route": route,
        "files": ctx["files"],
    }
