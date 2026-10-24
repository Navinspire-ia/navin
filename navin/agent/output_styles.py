"""Selectable output styles: how the main agent talks while it works.

Builtin styles are ``default`` (adds nothing), ``explanatory`` (short
"Insight" blocks about choices and codebase patterns) and ``learning``
(the user writes the meaningful decision points, marked ``TODO(human)``).
Custom styles are Markdown files with ``name``/``description`` frontmatter
in ``<workspace>/.navin/output-styles/`` (project) or
``~/.navin/output-styles/`` (personal). Project wins over personal over
builtin on a name clash.

Active style: env ``NAVIN_OUTPUT_STYLE``, then
``<workspace>/.navin/output-style.local``, then ``~/.navin/output-style``,
else ``default``. Unknown names fall back to ``default``.

CLI: ``python -m navin.agent.output_styles list|show|set|clear [name]
[--personal] [--workspace DIR]``.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT = "default"
ENV_VAR = "NAVIN_OUTPUT_STYLE"
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

_INSIGHT_FORMAT = (
    "Use exactly this layout for an insight, in the chat, never inside a file:\n\n"
    "```\n"
    "> Insight\n"
    "> - <first point, one sentence>\n"
    "> - <second point, one sentence>\n"
    "> - <optional third point>\n"
    "```\n"
)

_EXPLANATORY = (
    "Teach while you work. The user wants to understand the code, not only "
    "receive it, so explain the reasoning behind what you do without losing "
    "focus on the task.\n\n"
    "## Insight blocks\n\n"
    "Right before or right after you write or change code, add a short "
    "Insight block with 2 or 3 points about why the code looks the way it "
    "does: the implementation choice you made and the alternative you "
    "rejected, a convention or pattern this codebase follows, a pitfall the "
    "change avoids. Prefer facts specific to this repository and this change "
    "over general programming lessons.\n\n"
    + _INSIGHT_FORMAT
    + "\nGive insights as the work happens, not in one batch at the end. "
    "They may make the answer longer than usual; keep each point brief and "
    "relevant. Never put insights in code comments or files.\n"
)

_LEARNING = (
    "Pair-program to teach. Do the routine work yourself, but hand the "
    "meaningful decisions to the user so they write the code that shapes "
    "the solution.\n\n"
    "## When to hand a piece to the user\n\n"
    "Hand over a small piece (about 5 to 10 lines) when it carries a real "
    "choice: business rules, error handling strategy, an algorithm or data "
    "structure with several valid options, a user experience decision, a "
    "design or architecture trade-off, anywhere the user's domain knowledge "
    "beats a guess.\n\n"
    "Do it yourself, without asking, for boilerplate, repetitive code, setup "
    "and configuration, plain CRUD and anything with one obvious answer.\n\n"
    "## TODO(human) protocol\n\n"
    "1. Write the surrounding code first: the file, imports, the function "
    "signature with typed parameters and return value, and a comment stating "
    "its purpose.\n"
    "2. Put a placeholder where the user's code goes, a comment starting "
    "with `TODO(human):` plus one line on what is expected. Keep the file "
    "loadable (for example `raise NotImplementedError` or a neutral return "
    "value after the comment).\n"
    "3. Stop and hand over in the chat with three labelled parts:\n"
    "   - Context: what you built and why this decision matters.\n"
    "   - Your turn: the exact file and function holding the TODO(human).\n"
    "   - Think about: the trade-offs, constraints or candidate approaches.\n"
    "4. Present it as a decision that shapes the feature, never as busy work. "
    "One handoff at a time; wait for the user before continuing. When they "
    "answer, review their code, fix only real bugs, and go on.\n\n"
    "Example handoff:\n\n"
    "```\n"
    "Context: the retry wrapper is in place. How many times to retry and how "
    "long to wait is a reliability versus latency trade-off.\n"
    "Your turn: in net/retry.py, fill in next_delay(attempt) where the "
    "TODO(human) comment is.\n"
    "Think about: fixed delay is simple and predictable; exponential backoff "
    "with jitter protects a struggling server but can make users wait.\n"
    "```\n\n"
    "## Insight blocks\n\n"
    "Also explain as you go: before or after writing code, add a short "
    "Insight block with 2 or 3 points specific to this codebase or this "
    "change.\n\n"
    + _INSIGHT_FORMAT
)


@dataclass(frozen=True)
class OutputStyle:
    name: str
    description: str
    instructions: str
    source: str  # builtin | personal | project
    path: str = ""


BUILTIN: dict[str, OutputStyle] = {
    DEFAULT: OutputStyle(DEFAULT, "Plain answers, no added teaching.", "", "builtin"),
    "explanatory": OutputStyle(
        "explanatory",
        "Adds short Insight blocks about implementation choices and codebase patterns.",
        _EXPLANATORY,
        "builtin",
    ),
    "learning": OutputStyle(
        "learning",
        "Hands meaningful decisions to you with TODO(human) markers, plus Insight blocks.",
        _LEARNING,
        "builtin",
    ),
}

_cache: dict[str, tuple[float, OutputStyle | None]] = {}


def _home() -> Path:
    return Path.home()


def _project_dir(workspace: Path | str | None) -> Path | None:
    return Path(workspace) / ".navin" if workspace else None


def _parse(path: Path, source: str) -> OutputStyle | None:
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip().strip("\"'")
            body = text[end + 4:]
    name = meta.get("name") or path.stem
    style = None
    if _NAME_RE.match(name):
        style = OutputStyle(name, meta.get("description", ""), body.strip() + "\n", source, key)
    _cache[key] = (mtime, style)
    return style


def _custom(folder: Path | None, source: str) -> dict[str, OutputStyle]:
    found: dict[str, OutputStyle] = {}
    if folder is None or not folder.is_dir():
        return found
    for path in sorted(folder.glob("*.md")):
        style = _parse(path, source)
        if style:
            found[style.name] = style
    return found


def list_styles(workspace: Path | str | None) -> list[OutputStyle]:
    merged = dict(BUILTIN)
    merged.update(_custom(_home() / ".navin" / "output-styles", "personal"))
    proj = _project_dir(workspace)
    merged.update(_custom(proj / "output-styles" if proj else None, "project"))
    return list(merged.values())


def get_style(workspace: Path | str | None, name: str | None) -> OutputStyle | None:
    if not name:
        return None
    for style in list_styles(workspace):
        if style.name == name.strip():
            return style
    return None


def _read_line(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def _selection_files(workspace: Path | str | None) -> dict[str, Path | None]:
    proj = _project_dir(workspace)
    return {
        "project": proj / "output-style.local" if proj else None,
        "personal": _home() / ".navin" / "output-style",
    }


def selected_name(workspace: Path | str | None) -> str:
    """Raw selected name (may be unknown), following the selection order."""
    files = _selection_files(workspace)
    return (
        os.environ.get(ENV_VAR, "").strip()
        or _read_line(files["project"])
        or _read_line(files["personal"])
        or DEFAULT
    )


def active_style(workspace: Path | str | None) -> OutputStyle:
    return get_style(workspace, selected_name(workspace)) or BUILTIN[DEFAULT]


def set_style(workspace: Path | str | None, name: str, scope: str = "project") -> OutputStyle:
    style = get_style(workspace, name)
    if style is None:
        raise ValueError(f"unknown output style: {name}")
    target = _selection_files(workspace).get(scope)
    if target is None:
        raise ValueError(f"invalid scope or missing workspace: {scope}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(style.name + "\n", encoding="utf-8")
    return style


def clear_style(workspace: Path | str | None, scope: str = "project") -> bool:
    target = _selection_files(workspace).get(scope)
    if target is None or not target.exists():
        return False
    target.unlink()
    return True


def prompt_section(workspace: Path | str | None) -> str:
    """System prompt section for the active style ('' for default)."""
    try:
        style = active_style(workspace)
    except Exception:  # never break prompt assembly
        return ""
    if style.name == DEFAULT or not style.instructions.strip():
        return ""
    return f"# Output Style: {style.name}\n\n{style.instructions.strip()}"


def _render_list(workspace: Path | str | None) -> str:
    active = active_style(workspace).name
    raw = selected_name(workspace)
    lines = ["Output styles (* = active):"]
    for s in list_styles(workspace):
        mark = "*" if s.name == active else " "
        lines.append(f"{mark} {s.name} [{s.source}] - {s.description or 'no description'}")
    if raw != active:
        lines.append(f"(selected '{raw}' is unknown, using default)")
    lines.append("Switch: /output-style <name> [--personal]   Clear: /output-style off")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    workspace: str | None = os.getcwd()
    if "--workspace" in args:
        i = args.index("--workspace")
        workspace = args[i + 1] if i + 1 < len(args) else workspace
        del args[i:i + 2]
    scope = "personal" if "--personal" in args else "project"
    args = [a for a in args if a != "--personal"]
    cmd = args[0] if args else "list"
    if cmd not in {"list", "show", "set", "clear"}:
        args, cmd = ["set", *args], "set"
    if cmd in {"set"} and len(args) > 1 and args[1] in {"off", "none", DEFAULT}:
        cmd = "clear"
    if cmd == "list":
        print(_render_list(workspace))
    elif cmd == "show":
        style = get_style(workspace, args[1]) if len(args) > 1 else active_style(workspace)
        if style is None:
            print(f"Unknown output style: {args[1]}")
            return 1
        print(f"{style.name} [{style.source}] - {style.description}\n\n{style.instructions or '(adds nothing)'}")
    elif cmd == "clear":
        removed = clear_style(workspace, scope)
        print(f"Cleared the {scope} output style." if removed else f"No {scope} output style was set.")
        print(f"Active now: {active_style(workspace).name}")
    else:
        if len(args) < 2:
            print(_render_list(workspace))
            return 0
        try:
            style = set_style(workspace, args[1], scope)
        except ValueError as exc:
            print(f"{exc}\n\n{_render_list(workspace)}")
            return 1
        print(f"Output style set to '{style.name}' ({scope}). Applies from the next turn.")
        if os.environ.get(ENV_VAR):
            print(f"Note: {ENV_VAR} is set and overrides this choice.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
