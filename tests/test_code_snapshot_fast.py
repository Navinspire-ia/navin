"""The workspace snapshot around exec is cheaper but sees exactly the same files,
and commands that clearly only read skip it."""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from navin.agent import code_validation as cv
from navin.agent.runner import _may_edit_workspace

REPO = Path(__file__).resolve().parents[1]


def _reference_snapshot(root: Path) -> dict:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "."],
        cwd=root, capture_output=True, check=True,
    )
    out = {}
    for raw in (p.decode() for p in result.stdout.split(b"\0") if p):
        path = raw.replace("\\", "/")
        if cv.development_path(path) and not cv._GENERATED_DIRS.intersection(PurePosixPath(path).parts):
            out[path] = cv._file_stamp(root / raw)
    return out


def test_fast_snapshot_matches_the_reference_on_this_repo():
    assert cv.workspace_code_snapshot(REPO) == _reference_snapshot(REPO)


@pytest.mark.parametrize("name", ["a.", ".bashrc", "a.b.", "x.tar.gz", "a..py", ".env.local", "Dockerfile",
                                  "src/App.TSX", "node_modules/x/index.js", "dist/a.js", "Makefile", "x.py"])
def test_development_file_rule_matches_the_path_rule(name):
    expected = cv.development_path(name) and not cv._GENERATED_DIRS.intersection(PurePosixPath(name).parts)
    assert cv._is_development_file(name) == bool(expected)


@pytest.mark.parametrize("command", [
    "ls -la", "cat src/a.py | head -20", "git status --short", "git diff HEAD~1 -- src",
    "cd webui && ls", "grep -rn foo src | wc -l", "find . -name '*.py'", "sed -n 1,40p a.py",
    "node --version", "rg 'def main' -n",
    # The three reads of the 2026-10-31 desktop log that were taken for edits
    # (and handed a parallel chat's changes to a password lookup).
    'cd navin-manager/packaging/sandbox; ls -a; grep -rn "PASSWORD" . | head -20; env | grep -i NM_SANDBOX',
    'cd sandbox; sed -n 20,60p sandbox.sh | grep -nE "NM=|PY=|ADMIN=" ; ls /tmp/sb 2>&1 | head',
    "cat /tmp/sb/admin.password; echo; ls /tmp/sb/admin.totp && cat /tmp/sb/admin.totp",
    "ls /nowhere 2>/dev/null || echo none", "grep -c x a.log >/dev/null", 'grep "a>b" f',
])
def test_read_only_commands_skip_the_snapshot(command):
    assert cv.exec_cannot_edit({"command": command}) is True


@pytest.mark.parametrize("command", [
    "echo hi > a.txt", "cat a | tee b", "npm test", "python -m pytest -q", "sed -i s/a/b/ x.py",
    "find . -name '*.pyc' -delete", "ls; rm -rf build", "git checkout main", "ls $(pwd)",
    "npm run dev &", "git diff --output=x.patch", "make", "FOO=1 ls", "ls || touch x",
    'echo "$(rm -rf x)"', "ls 2>err.log", "cat a >/dev/null/../b", "env FOO=1 rm x", "sort -o out in",
    "ls 'unclosed", "ls\nrm x", "ls `rm x`", "cat <(rm x)",
])
def test_anything_else_keeps_the_snapshot(command):
    assert cv.exec_cannot_edit({"command": command}) is False


def test_background_exec_keeps_the_snapshot():
    assert cv.exec_cannot_edit({"command": "ls", "background": True}) is False


def test_another_chats_file_tool_edits_are_not_this_chats_to_validate(tmp_path):
    # 2026-10-31: two desktop chats on one project. The one that only looked
    # up a password was told to test and build the screens the other chat was
    # editing at the same time. A chat and its subagents share one owner.
    from navin.agent.tools.file_state import FileStates, bind_file_states, reset_file_states

    (tmp_path / "src").mkdir()
    for name in ("theirs.ts", "mine.ts", "made_by_a_command.ts"):
        (tmp_path / "src" / name).write_text("export const a = 1\n")
    other_chat, this_chat = FileStates(), FileStates()
    state = cv.CodeValidationState()
    state.observe_workspace(cv.workspace_code_snapshot(tmp_path), root=tmp_path)

    cv.note_chat_writes(tmp_path, {"src/theirs.ts"}, other_chat)
    cv.note_chat_writes(tmp_path, {str(tmp_path / "src" / "mine.ts")}, this_chat)
    for name in ("theirs.ts", "mine.ts", "made_by_a_command.ts"):
        (tmp_path / "src" / name).write_text("export const a = 2 // longer\n")
    token = bind_file_states(this_chat)
    try:
        state.observe_workspace(cv.workspace_code_snapshot(tmp_path), root=tmp_path)
    finally:
        reset_file_states(token)
    assert state.paths == {"src/mine.ts", "src/made_by_a_command.ts"}
    # Outside any chat (no owner bound) nothing is filtered: the safe side.
    assert cv.written_by_other_chats(tmp_path, {"src/theirs.ts"}) == set()
    assert cv.written_by_other_chats(tmp_path, {"src/theirs.ts"}, this_chat) == {"src/theirs.ts"}
    assert cv.written_by_other_chats(tmp_path, {"src/theirs.ts"}, other_chat) == set()
    # A subagent is its chat's child: what it writes is its chat's work, while
    # what its chat or a sibling writes is not the subagent's to validate.
    agent, sibling = FileStates(parent=this_chat), FileStates(parent=this_chat)
    cv.note_chat_writes(tmp_path, {"src/agent.ts"}, agent)
    assert cv.written_by_other_chats(tmp_path, {"src/agent.ts"}, this_chat) == set()
    assert cv.written_by_other_chats(tmp_path, {"src/agent.ts"}, agent) == set()
    assert cv.written_by_other_chats(tmp_path, {"src/agent.ts"}, sibling) == {"src/agent.ts"}
    assert cv.written_by_other_chats(tmp_path, {"src/agent.ts"}, other_chat) == {"src/agent.ts"}
    assert cv.written_by_other_chats(tmp_path, {"src/mine.ts"}, agent) == {"src/mine.ts"}


def test_files_another_cli_process_wrote_are_not_this_agents_to_validate(tmp_path, monkeypatch):
    # 2026-11-07: two CLIs on one project. Two subagents of one CLI stopped on
    # "Changes not validated" for 30 files the other CLI's agent was writing.
    from navin.agent.tools.file_state import FileStates, bind_file_states, reset_file_states

    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    registry = tmp_path / "state" / "chat-writes.json"
    monkeypatch.setattr(cv, "_shared_writes_path", lambda _root: registry)
    for name in ("theirs.py", "mine.py"):
        (root / "src" / name).write_text("a = 1\n")
    state = cv.CodeValidationState()
    state.observe_workspace(cv.workspace_code_snapshot(root), root=root)

    real_pid = cv.os.getpid()
    with monkeypatch.context() as other_process:
        other_process.setattr(cv.os, "getpid", lambda: real_pid + 1)
        cv.note_chat_writes(root, {"src/theirs.py"}, FileStates())
    # The other process's memory is not this one's: only the registry knows.
    cv._CHAT_WRITES.pop(cv._write_key(root / "src" / "theirs.py"))
    this_chat = FileStates()
    cv.note_chat_writes(root, {"src/mine.py"}, this_chat)
    for name in ("theirs.py", "mine.py"):
        (root / "src" / name).write_text("a = 2  # longer\n")
    token = bind_file_states(this_chat)
    try:
        state.observe_workspace(cv.workspace_code_snapshot(root), root=root)
    finally:
        reset_file_states(token)
    assert state.paths == {"src/mine.py"}
    # Once this process writes the file last, it is its own work again.
    cv.note_chat_writes(root, {"src/theirs.py"}, this_chat)
    assert cv.written_by_other_chats(root, {"src/theirs.py"}, this_chat) == set()


def test_runner_watches_editing_calls_only():
    call = lambda name, **arguments: SimpleNamespace(name=name, arguments=arguments)  # noqa: E731
    assert _may_edit_workspace(call("exec", command="npm run build")) is True
    assert _may_edit_workspace(call("exec", command="git status")) is False
    assert _may_edit_workspace(call("project", action="format")) is True
    assert _may_edit_workspace(call("project", action="status")) is False
    assert _may_edit_workspace(call("read_file", path="a.py")) is False


def test_no_test_suite_means_a_passing_check_is_enough(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n")
    state = cv.CodeValidationState(root=tmp_path)
    state.edited({"hello.py"}, require_tests=True)
    assert state.needs_tests is False
    assert "verify is enough" in state.missing()
    state.record(cv.VerificationEvidence(checks_ok=True, summary="ok"))
    assert state.pending is False


def test_a_project_with_tests_still_needs_them(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")
    state = cv.CodeValidationState(root=tmp_path)
    state.edited({"app.py", "models.py", "views.py"}, require_tests=True)
    assert state.needs_tests is True


def _project_with_tests(root):
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (root / "tests").mkdir()
    (root / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")


def test_a_small_fix_in_a_project_with_tests_closes_on_a_check(tmp_path):
    _project_with_tests(tmp_path)
    state = cv.CodeValidationState(root=tmp_path)
    state.edited({"app.py"}, require_tests=True)
    state.edited({"app.py", "models.py"}, require_tests=True)
    assert state.needs_tests is False
    assert state.pending is True
    assert "Small change" in state.missing()
    assert "Run the tests" not in state.missing()
    state.record(cv.VerificationEvidence(checks_ok=True, summary="lint clean"))
    assert state.pending is False


def test_a_third_logic_file_or_an_edited_test_needs_the_tests(tmp_path):
    _project_with_tests(tmp_path)
    growing = cv.CodeValidationState(root=tmp_path)
    growing.edited({"app.py", "models.py"}, require_tests=True)
    growing.record(cv.VerificationEvidence(checks_ok=True, summary="lint clean"))
    growing.edited({"views.py"}, require_tests=True)
    assert growing.needs_tests is True
    assert growing.pending is True
    assert "Small change" not in growing.missing()

    test_edit = cv.CodeValidationState(root=tmp_path)
    test_edit.edited({"tests/test_app.py"}, require_tests=True)
    assert test_edit.needs_tests is True


def test_a_scratch_file_outside_the_project_never_arms_the_gate(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _project_with_tests(project)
    state = cv.CodeValidationState(root=project)
    scratch = str(tmp_path / "scratch" / "probe.py")
    state.observe(
        "write_file", {"path": scratch, "content": "print(1)\n"}, "ok", status="ok",
        require_verify=True, validate_code=True, is_test_command=lambda _c: False,
    )
    assert state.pending is False
    assert scratch in state.touched
    state.observe(
        "write_file", {"files": [
            {"path": scratch, "content": "print(2)\n"},
            {"path": str(project / "app.py"), "content": "VALUE = 1\n"},
        ]}, "ok", status="ok",
        require_verify=True, validate_code=True, is_test_command=lambda _c: False,
    )
    assert state.pending is True
    assert state.paths == {str(project / "app.py")}


def _git_repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("build/\n*.log\n")
    for folder in ("src", "build", "empty"):
        (root / folder).mkdir()
    (root / "src" / "a.py").write_text("A = 1\n")
    (root / "build" / "keep.py").write_text("B = 1\n")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)


def _backdate(root):
    # A listing taken within 2 s of a folder change is never trusted: age the tree.
    # The index is then rewritten from the aged files and aged itself, kept
    # newer than the stat data it records. Aged under its own entries, an
    # index is "racily clean": git may refresh and rewrite it during a later
    # command, which moves the listing stamp (CI run of 2026-11-04, KeyError).
    import os
    import time

    old = time.time() - 60
    git_dir = os.path.join(str(root), ".git")
    for current, dirs, names in os.walk(root):
        if current == str(root):
            dirs[:] = [name for name in dirs if name != ".git"]
        for name in (*names, current):
            path = current if name == current else os.path.join(current, name)
            os.utime(path, (old, old))
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    index_time = old + 30
    for current, _dirs, names in os.walk(git_dir):
        for name in (*names, current):
            path = current if name == current else os.path.join(current, name)
            os.utime(path, (index_time, index_time))
    os.utime(root, (old, old))


def _listing_report(root: Path) -> str:
    """What the listing of *root* stamps and how old each stamp is, for a failure message.

    Built from the product's own helpers, so it names exactly the folders and
    marks a listing watches; plus the file system clock against time.time_ns(),
    the git processes still running and the git setup that lists the tree.
    """
    import os
    import shutil
    import tempfile
    import time

    root_text = str(root)
    paths = cv._git_listing(root)
    marks = list(cv._git_marks(root) or ())
    folders = {root_text}
    for raw in paths or ():
        path = raw.replace("\\", "/")
        if path.rsplit("/", 1)[-1] == ".gitignore":
            marks.append(os.path.join(root_text, raw))
        parent = path.rpartition("/")[0]
        while parent:
            folders.add(os.path.join(root_text, parent))
            parent = parent.rpartition("/")[0]
    unlisted = cv._unlisted_folders(root, set(folders)) if paths is not None else None
    with tempfile.NamedTemporaryFile(dir=root.parent) as probe:
        probe.write(b"x")
        probe.flush()
        skew_s = (os.fstat(probe.fileno()).st_mtime_ns - time.time_ns()) / 1e9
    now = time.time_ns()
    ages = []
    for path in (*sorted(folders), *sorted(unlisted or ()), *marks):
        mtime, size = cv._stamp(path)
        ages.append(f"{path}: missing" if mtime < 0 else f"{path}: {(now - mtime) / 1e9:.3f} s old, {size} B")
    running: list[str] = []
    if shutil.which("ps"):
        table = subprocess.run(["ps", "-eo", "pid,etimes,args"], capture_output=True, text=True, check=False)
        for line in table.stdout.splitlines()[1:]:
            fields = line.split(None, 2)
            if len(fields) == 3 and fields[2].split()[0].rsplit("/", 1)[-1].startswith("git"):
                running.append(line.strip())
    config = subprocess.run(
        ["git", "-C", root_text, "config", "--list", "--show-origin"],
        capture_output=True, text=True, check=False,
    ).stdout.strip().splitlines()
    return (
        f"git lists {'nothing' if paths is None else len(paths)} paths; "
        f"git marks: {'none' if cv._git_marks(root) is None else 'found'}; "
        f"unlisted folders: {'unknown' if unlisted is None else sorted(unlisted)}; "
        f"file system clock minus time.time_ns(): {skew_s:+.3f} s; stamp ages: {ages}; "
        f"{len(cv._LISTINGS)} listings held; git processes: {running}; git config: {config}; "
        f"git env: {sorted(key for key in os.environ if key.startswith('GIT_'))}"
    )


def _moved(listing) -> list[str]:
    """The stamps of a kept listing that no longer hold, with their age."""
    import time

    now = time.time_ns()
    moved = []
    for path, mtime, size in listing.stamps or ():
        current = cv._stamp(path)
        if current != (mtime, size):
            moved.append(
                f"{path}: {(mtime, size)} -> {current}"
                f" (modified {(now - current[0]) / 1e9:.3f} s ago)"
            )
    return moved


def _why_relisted(kept, root, moved_after_first) -> str:
    present = cv._LISTINGS.get(str(root))
    return (
        f"listing of {root} not reused: "
        f"{'gone' if present is None else 'replaced, stamps=' + str(present.stamps is not None)}; "
        f"stamps already moved right after the first listing: {moved_after_first}; "
        f"stamps moved now: {_moved(kept)}; {_listing_report(root)}"
    )


def test_listing_is_reused_until_a_folder_or_the_index_moves(tmp_path):
    import time

    _git_repo(tmp_path)
    _backdate(tmp_path)
    cv._LISTINGS.clear()
    first = cv.workspace_code_snapshot(tmp_path)
    assert "src/a.py" in first
    held = cv._LISTINGS.get(str(tmp_path))
    assert held is not None and held.stamps is not None, _listing_report(tmp_path)
    kept = cv._LISTINGS[str(tmp_path)]
    moved_after_first = _moved(kept)
    # An edit in place moves no folder: same listing, new stamp.
    time.sleep(0.01)
    (tmp_path / "src" / "a.py").write_text("A = 22222\n")
    edited = cv.workspace_code_snapshot(tmp_path)
    assert cv._LISTINGS.get(str(tmp_path)) is kept, _why_relisted(kept, tmp_path, moved_after_first)
    assert edited["src/a.py"] != first["src/a.py"]
    # A new file in a watched folder, an empty folder and a git add are all seen.
    (tmp_path / "src" / "b.py").write_text("B = 2\n")
    assert "src/b.py" in cv.workspace_code_snapshot(tmp_path)
    (tmp_path / "empty" / "c.py").write_text("C = 3\n")
    assert "empty/c.py" in cv.workspace_code_snapshot(tmp_path)
    (tmp_path / "build" / "ignored.py").write_text("X = 1\n")
    assert "build/ignored.py" not in cv.workspace_code_snapshot(tmp_path)
    (tmp_path / "a.log").write_text("x")
    assert not any(path.endswith(".log") for path in cv.workspace_code_snapshot(tmp_path))
    (tmp_path / "newdir").mkdir()
    (tmp_path / "newdir" / "d.py").write_text("D = 4\n")
    assert "newdir/d.py" in cv.workspace_code_snapshot(tmp_path)
    (tmp_path / "src" / "a.py").unlink()
    # Still in the git index: listed, with no stamp, exactly like git ls-files.
    assert cv.workspace_code_snapshot(tmp_path)["src/a.py"] is None
    assert cv.workspace_code_snapshot(tmp_path) == _reference_snapshot(tmp_path)
