# Copyright (c) 2026-present Navinspire IA
# SPDX-License-Identifier: AGPL-3.0-only

"""What someone joining a shared session needs to see at a glance.

Dates, project, git (branch, upstream, recent commits, pending changes) and
the list of prompts so far. Shared with live guests and written into session
bundles. Never carries secrets: remote URLs lose their credentials, commit
authors keep their name only, and file contents are never read.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from navin.utils import wsl
from navin.utils.git_argv import git_route
from navin.utils.proc import no_window_kwargs

GIT_TIMEOUT_S = 8
MAX_COMMITS = 30
MAX_CHANGED_FILES = 60
MAX_PROMPTS = 400
PROMPT_PREVIEW = 160
MAX_TOUCHED_FILES = 80
EDIT_TOOLS = {"write_file", "edit_file", "apply_patch", "create_file", "delete_file", "notebook_edit"}
INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md", ".navin/AGENTS.md", "docs/README.md")
STACK_MARKERS = {
    "package.json": "Node.js", "pyproject.toml": "Python", "requirements.txt": "Python",
    "Cargo.toml": "Rust", "go.mod": "Go", "pom.xml": "Java (Maven)", "build.gradle": "Java/Kotlin (Gradle)",
    "build.gradle.kts": "Kotlin (Gradle)", "Gemfile": "Ruby", "composer.json": "PHP", "pubspec.yaml": "Flutter/Dart",
    "Dockerfile": "Docker", "docker-compose.yml": "Docker Compose", "next.config.js": "Next.js",
    "next.config.ts": "Next.js", "vite.config.ts": "Vite", "tsconfig.json": "TypeScript", "Makefile": "Make",
    "src-tauri": "Tauri", "terraform": "Terraform", "*.csproj": ".NET",
}
_CREDENTIALS = re.compile(r"(?<=://)[^/@\s]+@")


def _git(repo: Path, *args: str) -> str:
    route = git_route(repo)
    if route is None:
        return ""
    try:
        done = subprocess.run(  # noqa: S603 - fixed git argv
            [*route.argv, "-c", "core.quotepath=off", *args], capture_output=True,
            timeout=GIT_TIMEOUT_S, **no_window_kwargs(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    out = done.stdout.decode("utf-8", "replace").strip() if done.returncode == 0 else ""
    if out and route.distro and args[:2] == ("rev-parse", "--show-toplevel"):
        out = wsl.to_unc(route.distro, out)
    return out


def git_context(project_root: Path) -> dict[str, Any] | None:
    root = _git(project_root, "rev-parse", "--show-toplevel")
    if not root:
        return None
    repo = Path(root)
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    info: dict[str, Any] = {
        "root": str(repo),
        "branch": "" if branch == "HEAD" else branch,
        "detached": branch == "HEAD",
        "head": _git(repo, "rev-parse", "--short", "HEAD"),
        "upstream": _git(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"),
        "ahead": 0,
        "behind": 0,
        "remote": _CREDENTIALS.sub("", _git(repo, "remote", "get-url", "origin")),
        "branches": [b for b in _git(repo, "branch", "--sort=-committerdate", "--format=%(refname:short)").splitlines() if b][:12],
        "stashes": len([s for s in _git(repo, "stash", "list").splitlines() if s]),
    }
    if info["upstream"]:
        counts = _git(repo, "rev-list", "--left-right", "--count", "HEAD...@{u}").split()
        if len(counts) == 2 and all(c.isdigit() for c in counts):
            info["ahead"], info["behind"] = int(counts[0]), int(counts[1])

    commits = []
    log = _git(repo, "log", f"-{MAX_COMMITS}", "--date=iso-strict", "--format=%h%x1f%an%x1f%ad%x1f%s")
    for line in log.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"sha": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    info["commits"] = commits

    changes = []
    for line in _git(repo, "status", "--porcelain=v1", "--untracked-files=normal").splitlines():
        if len(line) > 3:
            changes.append({"status": line[:2].strip() or "?", "path": line[3:]})
    info["changes"] = changes[:MAX_CHANGED_FILES]
    info["changes_total"] = len(changes)
    stat = _git(repo, "diff", "HEAD", "--shortstat")
    info["insertions"] = int(m.group(1)) if (m := re.search(r"(\d+) insertion", stat)) else 0
    info["deletions"] = int(m.group(1)) if (m := re.search(r"(\d+) deletion", stat)) else 0
    return info


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
    return ""


def conversation_context(session_file: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"title": "", "created_at": "", "updated_at": "", "model": "", "prompts": [],
                            "user_messages": 0, "assistant_messages": 0, "tool_calls": 0}
    try:
        lines = session_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return info
    prompts: list[dict[str, Any]] = []
    touched: list[str] = []
    last_reply = ""
    for raw in lines:
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("_type") == "metadata":
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            info["title"] = str(meta.get("title") or "")
            info["model"] = str(meta.get("model") or meta.get("model_name") or "")
            info["created_at"] = str(row.get("created_at") or "")
            info["updated_at"] = str(row.get("updated_at") or "")
            continue
        role = row.get("role")
        if role == "user":
            text = " ".join(_text(row.get("content")).split())
            # Injected runtime context is not something the user typed.
            if not text or text.startswith("[Runtime Context"):
                continue
            info["user_messages"] += 1
            prompts.append({
                "index": info["user_messages"] - 1,
                "text": text[:PROMPT_PREVIEW],
                "at": str(row.get("timestamp") or ""),
            })
        elif role == "assistant":
            info["assistant_messages"] += 1
            calls = row.get("tool_calls") or []
            info["tool_calls"] += len(calls)
            for call in calls:
                touched.extend(_edited_paths(call))
            text = _text(row.get("content")).strip()
            if text:
                last_reply = text
    info["prompts"] = prompts[-MAX_PROMPTS:]
    info["files_touched"] = list(dict.fromkeys(touched))[-MAX_TOUCHED_FILES:]
    info["last_reply"] = last_reply[:1200]
    return info


def _edited_paths(call: Any) -> list[str]:
    fn = call.get("function") if isinstance(call, dict) else None
    if not isinstance(fn, dict) or fn.get("name") not in EDIT_TOOLS:
        return []
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except (TypeError, ValueError):
        return []
    found = [args.get(k) for k in ("path", "file_path", "notebook_path") if isinstance(args, dict)]
    for edit in (args.get("edits") or []) if isinstance(args, dict) else []:
        if isinstance(edit, dict):
            found.append(edit.get("path"))
    return [str(p) for p in found if isinstance(p, str) and p]


def project_essentials(root: Path) -> dict[str, Any]:
    """Stack, how to run it, and where the instructions live: no file contents but the README's start."""
    stack = []
    for marker, label in STACK_MARKERS.items():
        hit = any(root.glob(marker)) if "*" in marker else (root / marker).exists()
        if hit and label not in stack:
            stack.append(label)
    commands: dict[str, str] = {}
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        for name, cmd in list((pkg.get("scripts") or {}).items())[:25]:
            commands[f"npm run {name}"] = str(cmd)[:200]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        import tomllib

        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        for name, target in list((pyproject.get("project", {}).get("scripts") or {}).items())[:10]:
            commands[name] = str(target)
        if (root / "tests").is_dir():
            commands.setdefault("pytest", "run the Python tests")
    except (OSError, ValueError, AttributeError):
        pass
    try:
        for line in (root / "Makefile").read_text(encoding="utf-8").splitlines():
            if (m := re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)) and not m.group(1).startswith("."):
                commands.setdefault(f"make {m.group(1)}", "")
                if len(commands) >= 50:
                    break
    except OSError:
        pass
    readme = ""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        try:
            readme = (root / name).read_text(encoding="utf-8")[:1500]
            break
        except OSError:
            continue
    return {
        "stack": stack,
        "commands": commands,
        "readme": readme,
        "instructions": [name for name in INSTRUCTION_FILES if (root / name).is_file()],
    }


def usage_context(session_file: Path, project_root: Path | None) -> dict[str, Any] | None:
    """Model and context fill of the last turn, as the host's context meter shows it."""
    from navin.session.context_usage_meta import last_context_usage_row
    from navin.webui.context_usage import context_usage_payload

    metadata: dict[str, Any] = {}
    messages: list[dict[str, Any]] = []
    try:
        with open(session_file, encoding="utf-8") as handle:
            for raw in handle:
                try:
                    row = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("_type") == "metadata":
                    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                else:
                    messages.append(row)
    except OSError:
        return None
    usage = context_usage_payload(
        {"metadata": metadata, "messages": messages},
        project_path=str(project_root) if project_root else None,
        prefer_persisted_window=True,
    )
    last = last_context_usage_row(metadata) or {}
    return {
        "model": str(last.get("model") or metadata.get("model") or ""),
        "tokens": int(usage.get("tokens") or 0),
        "context_window": int(usage.get("context_window") or 0),
        "percent": float(usage.get("percent") or 0),
    }


def session_context(session_file: Path | None, project_root: Path | None) -> dict[str, Any]:
    """Everything a guest or a bundle reader needs for a complete picture."""
    context: dict[str, Any] = {"generated_at": int(time.time())}
    context["conversation"] = conversation_context(session_file) if session_file else None
    try:
        context["usage"] = usage_context(session_file, project_root) if session_file else None
    except Exception:  # noqa: BLE001 - the picture is complete without the meter
        context["usage"] = None
    if context["conversation"] and context["usage"] and not context["conversation"].get("model"):
        context["conversation"]["model"] = context["usage"]["model"]
    if project_root is not None:
        project_root = project_root.expanduser().resolve()
        context["project"] = {"name": project_root.name, "path": str(project_root), **project_essentials(project_root)}
        context["git"] = git_context(project_root)
    else:
        context["project"], context["git"] = None, None
    return context


def context_markdown(context: dict[str, Any]) -> str:
    """The same picture as a readable page (CONTEXT.md in a session bundle)."""
    conv = context.get("conversation") or {}
    project = context.get("project") or {}
    git = context.get("git") or {}
    out = [f"# {conv.get('title') or project.get('name') or 'Navin session'}", ""]
    out += [f"- Started: {conv.get('created_at') or '?'}", f"- Last activity: {conv.get('updated_at') or '?'}"]
    if conv.get("model"):
        out.append(f"- Model: {conv['model']}")
    out.append(
        f"- Exchanges: {conv.get('user_messages', 0)} prompts, {conv.get('assistant_messages', 0)} replies, "
        f"{conv.get('tool_calls', 0)} tool calls"
    )
    if project:
        out += ["", "## Project", "", f"- Name: {project.get('name')}", f"- Path on the host: {project.get('path')}"]
        if project.get("stack"):
            out.append(f"- Stack: {', '.join(project['stack'])}")
        if project.get("instructions"):
            out.append(f"- Instructions: {', '.join(project['instructions'])}")
        if project.get("commands"):
            out += ["", "Commands:", ""]
            out += [f"- `{name}`" + (f": `{cmd}`" if cmd else "") for name, cmd in project["commands"].items()]
    if git:
        out += ["", "## Git", ""]
        out.append(f"- Branch: {git.get('branch') or '(detached) ' + str(git.get('head'))} at {git.get('head')}")
        if git.get("upstream"):
            out.append(f"- Upstream: {git['upstream']} (ahead {git.get('ahead', 0)}, behind {git.get('behind', 0)})")
        if git.get("remote"):
            out.append(f"- Remote: {git['remote']}")
        out.append(
            f"- Uncommitted: {git.get('changes_total', 0)} files "
            f"(+{git.get('insertions', 0)} -{git.get('deletions', 0)}), stashes: {git.get('stashes', 0)}"
        )
        if git.get("changes"):
            out += ["", "Changes:", ""] + [f"- `{c['status']}` {c['path']}" for c in git["changes"]]
        if git.get("commits"):
            out += ["", "Recent commits:", ""]
            out += [f"- `{c['sha']}` {c['date'][:10]} {c['author']}: {c['subject']}" for c in git["commits"]]
    if conv.get("files_touched"):
        out += ["", "## Files edited in this session", ""] + [f"- {path}" for path in conv["files_touched"]]
    if conv.get("prompts"):
        out += ["", "## Prompts", ""] + [f"{p['index'] + 1}. {p['text']}" for p in conv["prompts"]]
    if conv.get("last_reply"):
        out += ["", "## Last reply from the agent", "", conv["last_reply"]]
    return "\n".join(out) + "\n"
