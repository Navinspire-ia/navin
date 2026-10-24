"""Ralph loop: feed the same prompt back at every Stop until a promise or a cap.

The user starts a loop with ``/ralph-loop "<prompt>" --max-iterations N
--completion-promise "TEXT"``. The state lives in
``<workspace>/.navin/ralph-loop.local.md`` (frontmatter + prompt body). Each
time the main agent tries to end its turn, :func:`stop_reason` refuses the end
and returns the prompt again with the iteration number, until the final answer
holds ``<promise>TEXT</promise>`` or the iteration cap is reached.

CLI (used by the Markdown commands)::

    python -m navin.agent.ralph_loop start|cancel|status <workspace> [args]
"""

from __future__ import annotations

import re
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STATE_NAME = "ralph-loop.local.md"
UNLIMITED_CEILING = 200
_PROMISE_RE = re.compile(r"<promise>(.*?)</promise>", re.DOTALL)


class RalphError(ValueError):
    """Bad /ralph-loop arguments; the message is shown to the user."""


@dataclass
class LoopState:
    prompt: str
    iteration: int = 1
    max_iterations: int = 0
    completion_promise: str = ""
    started_at: str = ""
    session: str = ""
    active: bool = True

    def cap_text(self) -> str:
        return str(self.max_iterations) if self.max_iterations > 0 else "unlimited"


def state_path(workspace: Path | str) -> Path:
    return Path(workspace) / ".navin" / STATE_NAME


# -- arguments ----------------------------------------------------------------

def parse_args(args_text: str | list[str]) -> LoopState:
    """Parse ``PROMPT [--max-iterations N] [--completion-promise TEXT]``."""
    if isinstance(args_text, str):
        try:
            tokens = shlex.split(args_text)
        except ValueError as exc:
            raise RalphError(f"Could not read the arguments ({exc}). Check the quotes.") from exc
    else:
        tokens = list(args_text)
    parts: list[str] = []
    max_iterations = 0
    promise = ""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--max-iterations":
            if i + 1 >= len(tokens) or not tokens[i + 1].strip():
                raise RalphError(
                    "--max-iterations needs a number, e.g. --max-iterations 20 (0 means unlimited)."
                )
            value = tokens[i + 1]
            if not value.isdigit():
                raise RalphError(
                    f"--max-iterations must be a whole number >= 0, got: {value}. "
                    "Examples: 10, 50, 0 (unlimited)."
                )
            max_iterations = int(value)
            i += 2
            continue
        if tok == "--completion-promise":
            if i + 1 >= len(tokens) or not tokens[i + 1].strip():
                raise RalphError(
                    "--completion-promise needs a text, e.g. --completion-promise \"ALL TESTS PASS\" "
                    "(quote promises of several words)."
                )
            promise = " ".join(tokens[i + 1].split())
            i += 2
            continue
        parts.append(tok)
        i += 1
    prompt = " ".join(parts).strip()
    if not prompt:
        raise RalphError(
            "No prompt given. Example: /ralph-loop \"Fix the failing tests\" "
            "--max-iterations 20 --completion-promise \"DONE\""
        )
    return LoopState(prompt=prompt, max_iterations=max_iterations, completion_promise=promise)


# -- state file ---------------------------------------------------------------

def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if text == "null":
        return ""
    return text


def write_state(workspace: Path | str, state: LoopState) -> Path:
    path = state_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"active: {'true' if state.active else 'false'}\n"
        f"iteration: {state.iteration}\n"
        f"max_iterations: {state.max_iterations}\n"
        f"completion_promise: {_quote(state.completion_promise) if state.completion_promise else 'null'}\n"
        f"started_at: {_quote(state.started_at)}\n"
        f"session: {_quote(state.session)}\n"
        "---\n\n"
        f"{state.prompt}\n",
        encoding="utf-8",
    )
    return path


def read_state(workspace: Path | str | None) -> LoopState | None:
    """Loaded state, None when no file. Raises RalphError when corrupt."""
    if workspace is None:
        return None
    path = state_path(workspace)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RalphError(f"cannot read the state file ({exc})") from exc
    match = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n?(.*)\Z", text, re.DOTALL)
    if not match:
        raise RalphError("the state file has no frontmatter")
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    for key in ("iteration", "max_iterations"):
        if not fields.get(key, "").isdigit():
            raise RalphError(f"'{key}' is not a valid number (got: {fields.get(key, '')!r})")
    prompt = match.group(2).strip()
    if not prompt:
        raise RalphError("the state file has no prompt")
    return LoopState(
        prompt=prompt,
        iteration=int(fields["iteration"]),
        max_iterations=int(fields["max_iterations"]),
        completion_promise=_unquote(fields.get("completion_promise", "")),
        started_at=_unquote(fields.get("started_at", "")),
        session=_unquote(fields.get("session", "")),
        active=fields.get("active", "true").lower() != "false",
    )


def clear(workspace: Path | str) -> None:
    try:
        state_path(workspace).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


# -- commands -----------------------------------------------------------------

def start(workspace: Path | str, args_text: str | list[str], session: str = "") -> str:
    """Write the state and return the text shown to the agent. Raises RalphError."""
    state = parse_args(args_text)
    state.session = session or ""
    state.started_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    path = write_state(workspace, state)
    lines = [
        "Ralph loop started.",
        "",
        "Iteration: 1",
        f"Max iterations: {state.cap_text()}",
        "Completion promise: "
        + (f"{state.completion_promise} (output it only when it is true)" if state.completion_promise
           else "none (the loop only ends at the iteration cap or with /cancel-ralph)"),
        f"State file: {path}",
        "",
        "Each time you finish your answer, the same task comes back to you. Your earlier",
        "work stays in the files and in git history: read it, then improve on it.",
    ]
    if state.max_iterations == 0:
        lines.append(f"Warning: no --max-iterations set; Navin stops it after {UNLIMITED_CEILING} rounds.")
    lines += ["", "Task:", state.prompt]
    if state.completion_promise:
        lines += [
            "",
            "To end the loop, write exactly:",
            f"  <promise>{state.completion_promise}</promise>",
            "Only write it when that statement is completely and truthfully done. Never write",
            "it to escape the loop, even when stuck: keep working instead.",
        ]
    return "\n".join(lines)


def cancel(workspace: Path | str) -> str:
    try:
        state = read_state(workspace)
    except RalphError:
        clear(workspace)
        return "Removed a corrupt Ralph loop state file."
    if state is None:
        return "No active Ralph loop."
    clear(workspace)
    return f"Cancelled the Ralph loop (was at iteration {state.iteration})."


def status(workspace: Path | str) -> str:
    try:
        state = read_state(workspace)
    except RalphError as exc:
        return f"Ralph loop state file is corrupt: {exc}"
    if state is None or not state.active:
        return "No active Ralph loop."
    promise = state.completion_promise or "none"
    return (
        f"Ralph loop active: iteration {state.iteration}/{state.cap_text()}, promise: {promise}\n"
        f"Task: {state.prompt}"
    )


def summary(workspace: Path | str | None) -> str:
    """One line for the hooks summary, '' when no loop is active."""
    try:
        state = read_state(workspace)
    except RalphError:
        return ""
    if state is None or not state.active:
        return ""
    return (
        f"- ralph loop active: iteration {state.iteration}/{state.cap_text()}, "
        f"promise: {state.completion_promise or 'none'}"
    )


def _owned(state: LoopState, session: str) -> bool:
    return not state.session or not session or state.session == session


def round_cap(workspace: Path | str | None, session: str | None, default: int) -> int:
    """Stop-hook round cap for the runner: larger while a loop runs here."""
    try:
        state = read_state(workspace)
    except RalphError:
        return default
    if state is None or not state.active or not _owned(state, session or ""):
        return default
    want = state.max_iterations + 1 if state.max_iterations > 0 else UNLIMITED_CEILING
    return max(default, min(want, UNLIMITED_CEILING))


def promise_found(final_content: str | None, promise: str) -> bool:
    if not promise or not final_content:
        return False
    match = _PROMISE_RE.search(final_content)
    if not match:
        return False
    return " ".join(match.group(1).split()) == " ".join(promise.split())


def stop_reason(
    workspace: Path | str | None, session: str | None, final_content: str | None,
) -> tuple[str | None, str]:
    """(reason to keep working or None, notice for the user or '')."""
    if workspace is None:
        return None, ""
    session = session or ""
    try:
        state = read_state(workspace)
    except RalphError as exc:
        clear(workspace)
        return None, f"Ralph loop stopped: {exc}. Run /ralph-loop again to start fresh."
    if state is None or not state.active or not _owned(state, session):
        return None, ""
    if promise_found(final_content, state.completion_promise):
        clear(workspace)
        return None, (
            f"Ralph loop finished at iteration {state.iteration}: "
            f"<promise>{state.completion_promise}</promise> found."
        )
    if state.max_iterations > 0 and state.iteration >= state.max_iterations:
        clear(workspace)
        return None, f"Ralph loop finished: max iterations ({state.max_iterations}) reached."
    if state.max_iterations == 0 and state.iteration >= UNLIMITED_CEILING:
        clear(workspace)
        return None, f"Ralph loop stopped at the safety ceiling of {UNLIMITED_CEILING} iterations."
    state.iteration += 1
    if not state.session:
        state.session = session
    write_state(workspace, state)
    header = (
        f"Iteration {state.iteration} of {state.cap_text()}. Keep working on the task below; "
        "your previous work is in the files and git history."
    )
    if state.completion_promise:
        header += (
            f" Output <promise>{state.completion_promise}</promise> only when it is "
            "completely and truthfully done."
        )
    notice = f"Ralph loop: iteration {state.iteration}/{state.cap_text()}"
    return f"{header}\n\n{state.prompt}", notice


# -- CLI ----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in ("start", "cancel", "status"):
        print("usage: python -m navin.agent.ralph_loop start|cancel|status <workspace> [args]",
              file=sys.stderr)
        return 2
    action, workspace, rest = args[0], Path(args[1]).expanduser(), args[2:]
    if action == "start":
        if rest and rest[0] == "--session":
            session, rest = (rest[1] if len(rest) > 1 else ""), rest[2:]
        else:
            session = ""
        try:
            print(start(workspace, rest, session))
        except RalphError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0
    print(cancel(workspace) if action == "cancel" else status(workspace))
    return 0


if __name__ == "__main__":
    sys.exit(main())
